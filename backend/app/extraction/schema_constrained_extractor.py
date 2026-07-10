"""Schema-constrained data extractor using OpenAI API.

Translates hierarchical schemas into nullable strict JSON schemas, executes structured
OpenAI response completions, cleans markdown markup anomalies, and validates outputs.
"""
from __future__ import annotations

import html
import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import runtime_env_value
from app.extraction.document_map import DocumentMap
from app.extraction.evidence_pack import EvidencePack
from app.extraction.prompts import FINANCIAL_EXTRACTION_SYSTEM_PROMPT

import logging

logger = logging.getLogger(__name__)


SCHEMA_EXTRACTION_MODEL = "gpt-5-mini"


@dataclass
class SchemaFieldAudit:
    """Detailed audit log details for a single field extraction execution."""
    field_path: str
    field_schema: dict[str, Any]
    evidence_ids: list[str] = field(default_factory=list)
    evidence_preview: list[dict[str, Any]] = field(default_factory=list)
    raw_value: Any = None
    cleaned_value: Any = None
    validation_errors: list[str] = field(default_factory=list)
    confidence: float = 0.0
    retry_count: int = 0
    extraction_method: str = "schema_llm"


@dataclass
class SchemaExtractionResult:
    """Overall result of a schema-driven extraction operation."""
    data: dict[str, Any]
    confidence_by_field: dict[str, float]
    evidence_ids_by_field: dict[str, list[str]]
    audit: dict[str, SchemaFieldAudit]
    model_used: str = SCHEMA_EXTRACTION_MODEL
    used_llm: bool = False
    error: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


class SchemaConstrainedExtractor:
    """Core RAG LLM data extraction client driving OpenAI structured completions."""

    def __init__(self, model_name: str = SCHEMA_EXTRACTION_MODEL) -> None:
        """Initialize the extractor client setting target LLM model model_name."""
        self.model_name = model_name

    def extract(
        self,
        schema: dict[str, Any],
        field_packs: dict[str, EvidencePack],
        cover_evidence: list[dict[str, Any]],
        document_map: DocumentMap | None = None,
    ) -> SchemaExtractionResult:
        """Execute extraction across a target JSON schema using matched RAG evidence packs.

        Workflow:
        1. Compiles context window inputs using either evidence packs or a structured DocumentMap outline.
        2. Configures a nullable strict schema copy of the target JSON schema.
        3. Calls OpenAI completions post endpoint requesting structured JSON outputs.
        4. Parses and logs results, returning mapped audit records.
        """
        audit = _initial_audit(schema, field_packs)
        logger.info("schema_extractor: extract fields=%d model=%s", len(field_packs), self.model_name)
        api_key = runtime_env_value("OPENAI_API_KEY")
        if not api_key:
            logger.warning("schema_extractor: no_api_key")
            return SchemaExtractionResult(data={}, confidence_by_field={}, evidence_ids_by_field={}, audit=audit, error="OPENAI_API_KEY is not configured")

        logger.debug("schema_extractor: building_evidence_context cover_items=%d", len(cover_evidence))
        if document_map is not None:
            evidence = _build_document_map_context(schema, document_map, field_packs)
            logger.info("schema_extractor: document_map_context doc_map_title=%s pages=%d headings=%d",
                        document_map.cover.title, document_map.page_count, _count_headings(document_map.heading_tree))
        else:
            evidence = _build_evidence_context(field_packs, cover_evidence)
        logger.debug("schema_extractor: evidence_context items=%d", len(evidence))
        try:
            payload = _call_openai_schema_extractor(api_key, self.model_name, schema, evidence)
        except Exception as exc:
            logger.exception("schema_extractor: llm_call failed model=%s: %s", self.model_name, exc)
            return SchemaExtractionResult(data={}, confidence_by_field={}, evidence_ids_by_field={}, audit=audit, model_used=self.model_name, error=str(exc))

        data = _extract_data_payload(payload)
        properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
        data = {key: data.get(key) for key in properties}
        logger.info("schema_extractor: llm_success model=%s fields_extracted=%d", self.model_name, len(data))
        confidence = {key: 0.88 for key, value in data.items() if not _missing(value)}
        evidence_ids = {key: field_packs[key].evidence_ids[:4] for key in properties if key in field_packs and not _missing(data.get(key))}
        for key, item in audit.items():
            item.raw_value = data.get(key)
            item.confidence = confidence.get(key, 0.0)
            item.evidence_ids = evidence_ids.get(key, item.evidence_ids)
            logger.debug("schema_extractor: field=%s confidence=%s value_present=%s", key, confidence.get(key), key in data)
        return SchemaExtractionResult(
            data=data,
            confidence_by_field=confidence,
            evidence_ids_by_field=evidence_ids,
            audit=audit,
            model_used=self.model_name,
            used_llm=True,
            raw_response=payload,
        )


def validate_schema_value_quality(field_path: str, field_schema: dict[str, Any], value: Any) -> list[str]:
    """Perform post-extraction quality checks to flag potential hallucination or noise.

    Identifies issues like raw HTML/markdown leakage, mismatched entities,
    or placeholder tables returned as values.
    """
    if _missing(value):
        return []
    expected = str(field_schema.get("type") or "string")
    intent = _field_intent(field_path, field_schema)
    errors: list[str] = []
    if intent != "image" and _contains_raw_markup(value):
        errors.append("Value contains raw markup or image placeholders")
    if intent == "title" and isinstance(value, str) and re.search(r"credit\s+rating\s+definitions", value, re.I):
        errors.append("Document title points to appendix definitions instead of the report title")
    if intent == "ratings" and isinstance(value, list):
        useful = [item for item in value if _looks_like_rating_item(item)]
        if not useful or len(useful) < max(1, len(value) // 2):
            errors.append("Ratings value does not look like rating/instrument entries")
    if intent in {"analysts", "subsidiaries"} and isinstance(value, list):
        noisy = [item for item in value if _looks_like_cover_noise(item)]
        if noisy and len(noisy) >= max(1, len(value) // 2):
            errors.append(f"{intent} value is dominated by cover-page headings/noise")
    if expected == "array" and isinstance(value, list) and not value:
        errors.append("Expected non-empty array value")
    return errors


def clean_schema_value(value: Any, expected: str) -> Any:
    """Recursively clean HTML tag blocks and extra spaces from JSON extractions."""
    if value is None:
        return None
    if expected in {"number", "integer", "boolean"}:
        return value
    if isinstance(value, str):
        cleaned = _strip_markup(value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or None
    if isinstance(value, list):
        cleaned_items = [clean_schema_value(item, "string" if isinstance(item, str) else "object") for item in value]
        return [item for item in cleaned_items if not _missing(item)] or None
    if isinstance(value, dict):
        cleaned = {str(key): clean_schema_value(val, "string" if isinstance(val, str) else "object") for key, val in value.items()}
        return {key: val for key, val in cleaned.items() if not _missing(val)} or None
    return value


def write_extraction_audit(
    job_id: str,
    case_id: str,
    schema_id: str,
    mode: str,
    audit: dict[str, SchemaFieldAudit],
    *,
    model_used: str,
    error: str | None = None,
) -> str:
    """Save raw extraction metadata and audit trails in the parser_outputs/extraction_audits directory."""
    root = Path(__file__).resolve().parents[2] / "parser_outputs" / "extraction_audits"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{job_id}.json"
    payload = {
        "job_id": job_id,
        "case_id": case_id,
        "schema_id": schema_id,
        "mode": mode,
        "model_used": model_used,
        "error": error,
        "fields": {
            key: {
                "field_path": item.field_path,
                "field_schema": item.field_schema,
                "evidence_ids": item.evidence_ids,
                "evidence_preview": item.evidence_preview,
                "raw_value": item.raw_value,
                "cleaned_value": item.cleaned_value,
                "validation_errors": item.validation_errors,
                "confidence": item.confidence,
                "retry_count": item.retry_count,
                "extraction_method": item.extraction_method,
            }
            for key, item in audit.items()
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path)


def _call_openai_schema_extractor(api_key: str, model_name: str, schema: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Execute HTTP POST call to OpenAI completions endpoints, requesting schema validation checks."""
    response_schema = _nullable_strict_schema(schema)
    logger.debug("schema_extractor: calling_openai model=%s fields=%d", model_name, len(schema.get('properties', {})))
    request_payload = {
        "model": model_name,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "schema": schema,
                                "field_guidance": _field_guidance(schema),
                                "evidence": evidence,
                                "instructions": (
                                    FINANCIAL_EXTRACTION_SYSTEM_PROMPT + "\n"
                                    "Instructions: Extract a JSON object that matches the schema. Use only supplied evidence. "
                                    "Return null for unsupported fields. Do not copy image markers, raw HTML, "
                                    "appendix definitions, unrelated headings, or narrative into fields where they do not belong."
                                ),
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "extraction_result",
                "schema": response_schema,
                "strict": True,
            }
        },
    }
    try:
        result = _post_openai(api_key, request_payload)
        logger.debug("schema_extractor: openai_response status=ok")
        return result
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            logger.warning("schema_extractor: openai_http_error code=%s", exc.code)
            raise
        logger.info("schema_extractor: json_schema_mode_unsupported, falling back to json_object")
        request_payload["text"] = {"format": {"type": "json_object"}}
        return _post_openai(api_key, request_payload)


def _post_openai(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Perform synchronous HTTP post request to the OpenAI completions endpoint."""
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    logger.debug("schema_extractor: openai_request url=%s timeout=%d", "https://api.openai.com/v1/responses", 90)
    with urllib.request.urlopen(request, timeout=90, context=_openai_ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_data_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON string text from raw OpenAI response block."""
    text = payload.get("output_text")
    if isinstance(text, str) and text.strip():
        parsed = _parse_json(text)
        logger.debug("schema_extractor: parsed_from_output_text keys=%s", list(parsed.keys())[:5])
        return parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
    for item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        for content in item.get("content", []) if isinstance(item, dict) and isinstance(item.get("content"), list) else []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parsed = _parse_json(content["text"])
                return parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
    logger.debug("schema_extractor: json_parse_failed returning_empty")
    return {}


def _initial_audit(schema: dict[str, Any], field_packs: dict[str, EvidencePack]) -> dict[str, SchemaFieldAudit]:
    """Generate initial SchemaFieldAudit maps to populate validation states."""
    properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
    audit: dict[str, SchemaFieldAudit] = {}
    for key, field_schema in properties.items():
        pack = field_packs.get(key)
        audit[key] = SchemaFieldAudit(
            field_path=key,
            field_schema=field_schema if isinstance(field_schema, dict) else {},
            evidence_ids=pack.evidence_ids[:4] if pack else [],
            evidence_preview=_pack_preview(pack) if pack else [],
        )
    return audit


def _build_evidence_context(field_packs: dict[str, EvidencePack], cover_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten, deduplicate, and compile evidence list rows from all field packs."""
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in cover_evidence:
        evidence_id = str(row.get("evidence_id") or "")
        if evidence_id and evidence_id in seen:
            continue
        if evidence_id:
            seen.add(evidence_id)
        items.append(_evidence_context_item(row, field_hint="cover"))
    for field_path, pack in field_packs.items():
        for row in [*pack.tables, *pack.text_snippets]:
            evidence_id = str(row.get("evidence_id") or "")
            key = evidence_id or f"{field_path}:{len(items)}"
            if key in seen:
                continue
            seen.add(key)
            items.append(_evidence_context_item(row, field_hint=field_path))
    logger.debug("schema_extractor: evidence_context built items=%d unique=%d", len(items), len(seen))
    return items[:80]


def _build_document_map_context(
    schema: dict[str, Any],
    doc_map: DocumentMap,
    field_packs: dict[str, EvidencePack],
) -> list[dict[str, Any]]:
    """Compile document map outline summaries, cover metadata, and tables into the context pool."""
    items: list[dict[str, Any]] = []

    items.append({
        "evidence_id": "doc_map_cover",
        "field_hint": "cover",
        "source_type": "document_map",
        "text": f"Document Title: {doc_map.cover.title or 'Unknown'}\n"
               f"Date: {doc_map.cover.date or 'Unknown'}\n"
               f"Entities: {', '.join(doc_map.cover.entities[:10]) or 'None detected'}\n"
               f"Pages: {doc_map.page_count}",
    })

    heading_text = _headings_context(doc_map.heading_tree)
    if heading_text.strip():
        items.append({
            "evidence_id": "doc_map_headings",
            "field_hint": "all",
            "source_type": "document_map",
            "text": heading_text[:3000],
        })

    for i, table in enumerate(doc_map.tables[:15]):
        heading = f" near '{table.nearby_heading}'" if table.nearby_heading else ""
        items.append({
            "evidence_id": f"doc_map_table_{i}",
            "field_hint": "all",
            "page_number": table.page,
            "source_type": "table",
            "text": f"Table {i + 1} (page {table.page}): {table.caption or 'Untitled'}{heading}\n{table.text[:2000]}",
        })

    for field_path, pack in field_packs.items():
        for row in [*pack.tables, *pack.text_snippets]:
            items.append(_evidence_context_item(row, field_hint=field_path))

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("evidence_id") or hash(str(item.get("text", ""))[:200]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    logger.debug("schema_extractor: doc_map_context built items=%d after_dedup=%d", len(items), len(deduped))
    return deduped[:80]


def _headings_context(nodes: list[Any], depth: int = 0) -> str:
    """Format markdown headings outline layout."""
    lines: list[str] = []
    indent = "  " * depth
    for node in nodes:
        prefix = "#" * min(node.level, 4)
        lines.append(f"{indent}{prefix} {node.text} (page {node.page})")
        if hasattr(node, "children") and node.children:
            lines.append(_headings_context(node.children, depth + 1))
    return "\n".join(lines)


def _count_headings(nodes: list[Any]) -> int:
    """Calculate heading outline item count."""
    if not nodes:
        return 0
    return sum(1 + _count_headings(n.children) for n in nodes if hasattr(n, "children"))


def _field_guidance(schema: dict[str, Any]) -> dict[str, str]:
    """Compile custom guidance strings tailored to specific target field intents."""
    properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
    guidance: dict[str, str] = {}
    for key, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue
        intent = _field_intent(key, field_schema)
        if intent == "title":
            guidance[key] = "Extract the report/document title from the cover or first page. Do not use appendix headings such as CREDIT RATING DEFINITIONS."
        elif intent == "date":
            guidance[key] = "Extract the report date or reporting period from the cover page/header. Prefer month-year or ISO date when clear."
        elif intent == "issuer":
            guidance[key] = "Extract the rated company/issuer/entity name, not the rating agency name."
        elif intent == "rating_drivers":
            guidance[key] = "Extract concise rating drivers/rationale only. Exclude raw tables, definitions, and unrelated narrative."
        elif intent == "ratings":
            guidance[key] = "Extract actual credit ratings and rated instruments/facilities only. Exclude headings, ratio discussion, and unrelated risk text."
        elif intent == "analysts":
            guidance[key] = "Extract analyst/contact person names only when present. Exclude cover headings and document titles."
        elif intent == "subsidiaries":
            guidance[key] = "Extract subsidiary or associate names only. Exclude financial tables and cover headings."
        elif intent == "image":
            guidance[key] = "Extract image or figure references only when evidence contains image URLs. Return the URL plus a short caption when possible."
    return guidance


def _evidence_context_item(row: dict[str, Any], field_hint: str) -> dict[str, Any]:
    """Convert an evidence item dict into a simplified context dictionary for LLM prompt injections."""
    text = _strip_markup(str(row.get("markdown") or row.get("text") or ""))
    source_url = _source_url(row)
    if not text.strip() and source_url:
        text = f"Image evidence: {source_url}"
    return {
        "evidence_id": row.get("evidence_id"),
        "field_hint": field_hint,
        "page_number": row.get("page_number"),
        "source_type": row.get("source_type"),
        "text": text[:5000],
        "source_url": source_url,
    }


def _pack_preview(pack: EvidencePack) -> list[dict[str, Any]]:
    """Compile text previews to include in extraction audits."""
    return [
        {
            "evidence_id": row.get("evidence_id"),
            "page_number": row.get("page_number"),
            "source_type": row.get("source_type"),
            "text_preview": _strip_markup(str(row.get("markdown") or row.get("text") or ""))[:500],
        }
        for row in [*pack.tables, *pack.text_snippets]
    ][:5]


def _nullable_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a target schema structure into a strict response format where all attributes are nullable."""
    converted = _nullable_schema_node(schema)
    if converted.get("type") == "object" and isinstance(converted.get("properties"), dict):
        converted["required"] = list(converted["properties"].keys())
        converted["additionalProperties"] = False
    return converted


def _nullable_schema_node(node: Any) -> Any:
    """Recursively set schema node properties to allow null types."""
    if not isinstance(node, dict):
        return node
    out = {key: _nullable_schema_node(value) for key, value in node.items() if key != "required"}
    typ = out.get("type")
    if typ == "object":
        properties = out.get("properties")
        if isinstance(properties, dict):
            out["properties"] = {key: _nullable_schema_node(value) for key, value in properties.items()}
            out["required"] = list(properties.keys())
        out["additionalProperties"] = False
    elif typ == "array" and isinstance(out.get("items"), dict):
        out["items"] = _nullable_schema_node(out["items"])
    if isinstance(typ, str) and typ != "null":
        out["type"] = [typ, "null"]
    return out


def _field_intent(field_path: str, field_schema: dict[str, Any]) -> str:
    """Classify the intent category of a field key."""
    haystack = f"{field_path} {field_schema.get('description') or ''}".lower()
    if any(token in haystack for token in ("image", "figure", "chart", "logo", "visual")):
        return "image"
    if any(token in haystack for token in ("documenttitle", "document title", "documentname", "document name", "report title")):
        return "title"
    if any(token in haystack for token in ("reportdate", "report date", "reportingperiod", "reporting period", "period ended")):
        return "date"
    if any(token in haystack for token in ("issuer", "companyname", "company name", "ratedentity", "rated entity")):
        return "issuer"
    if any(token in haystack for token in ("ratingdrivers", "rating drivers", "rating rationale", "rationale")):
        return "rating_drivers"
    if any(token in haystack for token in ("ratings", "rating", "instrument")):
        return "ratings"
    if "analyst" in haystack:
        return "analysts"
    if any(token in haystack for token in ("subsidiar", "associate")):
        return "subsidiaries"
    return "generic"


def _contains_raw_markup(value: Any) -> bool:
    """Verify if a string value contains markdown images or tag markers."""
    if isinstance(value, str):
        return bool(re.search(r"<[^>]+>|!\[|!!\[|/api/parser-benchmarks/media", value))
    if isinstance(value, list):
        return any(_contains_raw_markup(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_raw_markup(item) for item in value.values())
    return False


def _source_url(row: dict[str, Any]) -> str | None:
    """Parse visual image asset URLs out of metadata dictionaries or markdown links."""
    direct = row.get("source_url") or row.get("url")
    if isinstance(direct, str) and direct:
        return direct
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    direct = metadata.get("source_url") or metadata.get("url")
    if isinstance(direct, str) and direct:
        return direct
    text = str(row.get("markdown") or row.get("text") or "")
    match = re.search(r"!\[[^\]]*]\(([^)]+)\)|(/api/parser-benchmarks/media/\S+)", text)
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip() or None


def _looks_like_cover_noise(value: Any) -> bool:
    """Identify layout cover headers leakage."""
    text = _strip_markup(str(value)).strip()
    return not text or text == "!" or bool(re.match(r"^(credit rating rationale|financial institution ratings|project finance ratings)$", text, re.I))


def _looks_like_rating_item(value: Any) -> bool:
    """Validate if value contains standard credit rating tokens."""
    text = _strip_markup(str(value))
    return bool(re.search(r"\b(?:AAA|AA[1-3]?|A[1-3]?|BBB|P1|P2|stable|reaffirmed|assigned|upgraded|downgraded)\b", text, re.I))


def _strip_markup(text: str) -> str:
    """Remove markdown image anchors, brackets, and HTML tags from a text string."""
    text = re.sub(r"!!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return html.unescape(text)


def _parse_json(text: str) -> dict[str, Any]:
    """Safely decode text string into a JSON dictionary."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _openai_ssl_context() -> ssl.SSLContext:
    """Create secure default SSL connection context, falling back gracefully on certifi errors."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _missing(value: Any) -> bool:
    """Check if value is empty, null, or blank."""
    return value is None or value == "" or value == [] or value == {}
