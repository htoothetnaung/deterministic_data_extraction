from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
import logging

logger = logging.getLogger(__name__)

from app.core.config import runtime_env_value
from app.extraction.evidence_pack import EvidencePack
from app.extraction.field_extractor import ExtractedCandidate, FieldExtractor
from app.extraction.prompts import SINGLE_FIELD_LLM_PROMPT
from app.models.settings import RuntimeSettings

GEMINI_FLASH_MODEL = "gemini-2.5-flash"
OPENAI_EXTRACTION_MODEL = "gpt-5-mini"


def _openai_ssl_context() -> ssl.SSLContext:
    """Build an SSL context from the certifi bundle when available.

    Mirrors ``app.services.extraction_lab._openai_ssl_context`` so that OpenAI
    calls succeed in environments where ``SSL_CERT_FILE`` points at a broken
    system bundle.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


@dataclass
class ConsistencyReport:
    null_fields_detected: int = 0
    null_retries: int = 0
    recovered_nulls: int = 0
    candidate_conflicts: int = 0
    critic_issues: list[str] = field(default_factory=list)
    adk_available: bool = False
    model_used: str = GEMINI_FLASH_MODEL

    @property
    def consistency_score(self) -> float:
        penalties = self.null_fields_detected + self.candidate_conflicts + len(self.critic_issues)
        return round(max(0.0, 1.0 - penalties * 0.15), 3)

    def model_dump(self) -> dict[str, Any]:
        return {
            "null_fields_detected": self.null_fields_detected,
            "null_retries": self.null_retries,
            "recovered_nulls": self.recovered_nulls,
            "candidate_conflicts": self.candidate_conflicts,
            "critic_issues": self.critic_issues,
            "critic_issue_count": len(self.critic_issues),
            "consistency_score": self.consistency_score,
            "adk_available": self.adk_available,
            "model_used": self.model_used,
        }


class AgenticFieldExtractor:
    """Bounded agentic extraction wrapper.

    Uses the existing deterministic extractor first, then a Gemini Flash JSON
    fallback when available. ADK is detected lazily so local cost-effective
    runs do not depend on ADK import side effects.
    """

    def __init__(self, model_name: str = GEMINI_FLASH_MODEL) -> None:
        self.rule_extractor = FieldExtractor()
        self.model_name = model_name
        self.adk_available = _adk_available()

    def extract(self, field_path: str, field_schema: dict, pack: EvidencePack, settings: RuntimeSettings | None = None) -> list[ExtractedCandidate]:
        tier = settings.model.model_tier if (settings and settings.model) else "cost_effective"
        
        # Map tier to model name and provider
        openai_model = None
        gemini_model = None
        primary_provider = "gemini"

        if tier == "speed":
            openai_model = "gpt-4o-mini"
            primary_provider = "openai"
        elif tier == "balanced":
            openai_model = "gpt-4o"
            primary_provider = "openai"
        elif tier == "quality":
            gemini_model = "gemini-2.5-pro"
            primary_provider = "gemini"
        else: # cost_effective
            gemini_model = "gemini-2.5-flash"
            primary_provider = "gemini"

        if primary_provider == "openai":
            logger.debug("agentic: openai_candidate field=%s model=%s", field_path, openai_model)
            candidate = _openai_candidate(field_path, field_schema, pack, openai_model, settings)
            if candidate:
                logger.info("agentic: openai_candidate field=%s success=True", field_path)
                return [candidate]
        else:
            logger.debug("agentic: gemini_candidate field=%s model=%s", field_path, gemini_model)
            candidate = _gemini_candidate(field_path, field_schema, pack, gemini_model or self.model_name, settings)
            if candidate:
                logger.info("agentic: gemini_candidate field=%s success=True", field_path)
                return [candidate]

        # Rule extraction fallback
        logger.debug("agentic: primary model field=%s failed, falling back to deterministic", field_path)
        candidates = self.rule_extractor.extract(field_path, field_schema, pack)
        if candidates:
            logger.info("agentic: deterministic field=%s candidates=%d", field_path, len(candidates))
            return candidates

        # Final secondary model fallback
        if primary_provider == "openai":
            logger.debug("agentic: falling back to gemini field=%s", field_path)
            candidate = _gemini_candidate(field_path, field_schema, pack, self.model_name, settings)
        else:
            logger.debug("agentic: falling back to openai field=%s", field_path)
            candidate = _openai_candidate(field_path, field_schema, pack, OPENAI_EXTRACTION_MODEL, settings)
        
        if candidate:
            logger.info("agentic: fallback field=%s success=True", field_path)
            return [candidate]
            
        logger.warning("agentic: all_tiers_failed field=%s", field_path)
        return []


def detect_conflict(candidate_values: list[Any]) -> bool:
    normalized = {" ".join(str(value).strip().lower().split()) for value in candidate_values if value is not None}
    conflict = len(normalized) > 1
    if conflict:
        logger.info("agentic: conflict_detected values=%d", len(candidate_values))
    return conflict


def critic_issues(final_json: dict[str, Any], required_fields: set[str]) -> list[str]:
    issues: list[str] = []
    missing = sorted(field for field in required_fields if _missing(final_json.get(field)))
    if missing:
        issues.append(f"missing_required:{','.join(missing)}")

    lower = {key.lower(): value for key, value in final_json.items()}
    assets = _number_like(lower.get("assets") or lower.get("total_assets"))
    liabilities = _number_like(lower.get("liabilities") or lower.get("total_liabilities"))
    equity = _number_like(lower.get("equity") or lower.get("total_equity"))
    if assets is not None and liabilities is not None and equity is not None:
        tolerance = max(1.0, abs(assets) * 0.02)
        if abs(assets - (liabilities + equity)) > tolerance:
            issues.append("accounting_mismatch:assets_vs_liabilities_plus_equity")
    return issues


def _openai_candidate(
    field_path: str,
    field_schema: dict,
    pack: EvidencePack,
    model_name: str,
    settings: RuntimeSettings | None = None,
) -> ExtractedCandidate | None:
    """Primary LLM extractor for the DB-backed pipeline.

    Sends the field definition and the evidence pack to the OpenAI Responses
    API and asks for a strict-JSON value. Returns ``None`` when unavailable so
    the caller falls back to the deterministic extractor. Never raises.
    """
    api_key = runtime_env_value("OPENAI_API_KEY")
    if not api_key:
        logger.debug("agentic: openai_candidate field=%s reason=no_api_key", field_path)
        return None
    evidence = [
        {
            "evidence_id": item.get("evidence_id"),
            "source_type": item.get("source_type"),
            "text": str(item.get("markdown") or item.get("text") or "")[:4000],
        }
        for item in [*pack.tables, *pack.text_snippets]
    ]
    if not evidence:
        logger.debug("agentic: openai_candidate field=%s reason=empty_evidence", field_path)
        return None
    prompt = {
        "field": {
            "key": field_path,
            "label": field_schema.get("description") or field_path,
            "type": str(field_schema.get("type") or "string"),
            "description": field_schema.get("description") or field_path,
            "required": bool(field_schema.get("required")),
        },
        "evidence": evidence,
        "instructions": SINGLE_FIELD_LLM_PROMPT,
    }
    req_data = {
        "model": model_name,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": json.dumps(prompt, ensure_ascii=False)}],
            }
        ],
        "text": {"format": {"type": "json_object"}},
    }
    if settings is not None and settings.model is not None:
        req_data["temperature"] = settings.model.temperature
        if settings.model.max_tokens:
            req_data["max_completion_tokens"] = settings.model.max_tokens

    body = json.dumps(req_data).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60, context=_openai_ssl_context()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        logger.warning("agentic: openai_candidate field=%s failed: %s", field_path, e)
        return None
    parsed = _extract_response_payload(payload)
    value = parsed.get("value")
    if _missing(value):
        logger.debug("agentic: openai_candidate field=%s reason=missing_value", field_path)
        return None
    confidence = parsed.get("confidence")
    evidence_ids = parsed.get("evidence_ids")
    return ExtractedCandidate(
        value=value,
        confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.85,
        evidence_ids=[str(item) for item in evidence_ids if item] if isinstance(evidence_ids, list) else pack.evidence_ids[:1],
        extraction_method="llm_text",
    )


def _extract_response_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Best-effort JSON extraction from an OpenAI Responses API payload."""
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return _parse_json(output_text)
    for item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                return _parse_json(content["text"])
    return {}


def _gemini_candidate(
    field_path: str,
    field_schema: dict,
    pack: EvidencePack,
    model_name: str,
    settings: RuntimeSettings | None = None,
) -> ExtractedCandidate | None:
    api_key = runtime_env_value("GOOGLE_API_KEY") or runtime_env_value("GEMINI_API_KEY")
    if not api_key:
        logger.debug("agentic: gemini_candidate field=%s reason=no_api_key", field_path)
        return None
    try:
        from google import genai
    except ImportError:
        logger.debug("agentic: gemini_candidate field=%s reason=genai_not_installed", field_path)
        return None

    evidence = [
        {
            "evidence_id": item.get("evidence_id"),
            "source_type": item.get("source_type"),
            "text": str(item.get("markdown") or item.get("text") or "")[:4000],
        }
        for item in [*pack.tables, *pack.text_snippets]
    ]
    if not evidence:
        logger.debug("agentic: gemini_candidate field=%s reason=empty_evidence", field_path)
        return None
    prompt = {
        "field_path": field_path,
        "field_schema": field_schema,
        "evidence": evidence,
        "instructions": "Return strict JSON: {\"value\": any, \"confidence\": number, \"evidence_ids\": [string]}. Use null when unsupported.",
    }
    try:
        from google.genai import types
        config_kwargs = {}
        if settings is not None and settings.model is not None:
            config_kwargs["temperature"] = settings.model.temperature
            if settings.model.max_tokens:
                config_kwargs["max_output_tokens"] = settings.model.max_tokens
        
        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        response = genai.Client(api_key=api_key).models.generate_content(
            model=model_name,
            contents=json.dumps(prompt, ensure_ascii=False),
            config=config,
        )
        payload = _parse_json(getattr(response, "text", "") or "")
    except Exception as e:
        logger.warning("agentic: gemini_candidate field=%s failed: %s", field_path, e)
        return None
    value = payload.get("value")
    if _missing(value):
        return None
    confidence = payload.get("confidence")
    evidence_ids = payload.get("evidence_ids")
    return ExtractedCandidate(
        value=value,
        confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.68,
        evidence_ids=[str(item) for item in evidence_ids if item] if isinstance(evidence_ids, list) else pack.evidence_ids[:1],
        extraction_method="llm_text",
    )


def _adk_available() -> bool:
    try:
        import google.adk  # noqa: F401
    except ImportError:
        return False
    return True


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _number_like(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").replace("$", "").strip())
        except ValueError:
            return None
    return None


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}
