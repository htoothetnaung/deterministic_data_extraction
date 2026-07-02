from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.extraction.evidence_pack import EvidencePack


@dataclass
class ExtractedCandidate:
    value: Any
    confidence: float
    evidence_ids: list[str]
    extraction_method: str = "keyword_rule"


class FieldExtractor:
    def extract(self, field_path: str, field_schema: dict, pack: EvidencePack) -> list[ExtractedCandidate]:
        expected = str(field_schema.get("type") or "string")
        candidates: list[ExtractedCandidate] = []
        for item in [*pack.tables, *pack.text_snippets]:
            text = str(item.get("markdown") or item.get("text") or "")
            value = _extract_structured_value(expected, item, text)
            if value is not None:
                candidates.append(
                    ExtractedCandidate(
                        value=value,
                        confidence=0.84,
                        evidence_ids=[str(item["evidence_id"])],
                        extraction_method="table_parser" if str(item.get("source_type", "")).startswith("table") else "keyword_rule",
                    )
                )
                continue
            value = _extract_value(field_path, field_schema, expected, text)
            if value is None:
                continue
            confidence = 0.86 if str(item.get("source_type", "")).startswith("table") else 0.72
            candidates.append(
                ExtractedCandidate(
                    value=value,
                    confidence=confidence,
                    evidence_ids=[str(item["evidence_id"])],
                    extraction_method="table_parser" if confidence > 0.8 else "keyword_rule",
                )
            )
        return candidates


def _extract_structured_value(expected: str, item: dict, text: str) -> Any:
    if expected == "array":
        metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
        rows = metadata.get("rows") if isinstance(metadata, dict) else None
        if isinstance(rows, list) and rows:
            return rows
        lines = [line.strip(" |-") for line in text.splitlines() if line.strip(" |-")]
        return lines[:20] if lines else None
    if expected == "object":
        metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
        rows = metadata.get("rows") if isinstance(metadata, dict) else None
        if isinstance(rows, list) and rows:
            return rows[0] if isinstance(rows[0], dict) else None
    return None


def _extract_value(field_path: str, field_schema: dict, expected: str, text: str) -> Any:
    label = re.escape(_humanize_key(field_path))
    labeled = re.search(rf"(?im){label}\s*(?:[:=|-]|\s{{2,}})\s*([^\n\r|]{{1,260}})", text)
    if labeled:
        raw = labeled.group(1).strip()
        coerced = _coerce(raw, expected)
        return coerced if coerced is not None else raw
    field_label = str(field_schema.get("description") or "").split("\n")[0].strip()
    if field_label:
        alt_label = re.escape(_humanize_key(field_label))
        alt_match = re.search(rf"(?im){alt_label}\s*(?:[:=|-]|\s{{2,}})\s*([^\n\r|]{{1,260}})", text)
        if alt_match:
            raw = alt_match.group(1).strip()
            coerced = _coerce(raw, expected)
            return coerced if coerced is not None else raw
    hint = " ".join([field_path, str(field_schema.get("description") or "")]).lower()
    if "date" in hint:
        date_match = re.search(
            r"\b(?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b"
            r"|\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"
            r"|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
            text,
            re.I,
        )
        if date_match:
            return date_match.group(0).strip()
    if expected in {"number", "integer"}:
        match = re.search(r"(?<![\w.-])-?\d+(?:,\d{3})*(?:\.\d+)?(?![\w.-])", text)
        if match:
            return _coerce(match.group(0), expected)
    if expected == "boolean":
        match = re.search(r"\b(true|false|yes|no|approved|rejected)\b", text, re.I)
        if match:
            return _coerce(match.group(1), expected)
    return None


def _humanize_key(value: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value.replace("_", " "))
    return re.sub(r"\s+", " ", spaced).strip()


def _coerce(value: str, expected: str) -> Any:
    clean = value.strip()
    if expected == "integer":
        try:
            return int(float(clean.replace(",", "")))
        except ValueError:
            return None
    if expected == "number":
        try:
            return float(clean.replace(",", ""))
        except ValueError:
            return None
    if expected == "boolean":
        lowered = clean.lower()
        if lowered in {"true", "yes", "approved"}:
            return True
        if lowered in {"false", "no", "rejected"}:
            return False
        return None
    return clean
