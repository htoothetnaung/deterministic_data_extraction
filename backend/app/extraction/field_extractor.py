"""Deterministic and fallback field value extractor.

Analyzes text layouts and structured previews (from tables or text blocks)
to extract matched candidate values and assign extraction method metadata.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.extraction.evidence_pack import EvidencePack
import logging

logger = logging.getLogger(__name__)


class FieldIntent(Enum):
    """Categorizes field labels to apply target heuristics (e.g. titles, ratings)."""
    TITLE = "title"
    DATE = "date"
    ISSUER = "issuer"
    RATINGS = "ratings"
    RATING_DRIVERS = "rating_drivers"
    SUMMARY = "summary"
    SUBSIDIARIES = "subsidiaries"
    ANALYSTS = "analysts"
    FINANCIAL_POSITIONS = "financial_positions"
    STAKEHOLDERS = "stakeholders"
    GENERIC_TEXT = "generic_text"
    GENERIC_NUMBER = "generic_number"
    GENERIC_DATE = "generic_date"


_TITLE_ALIASES = ("documentname", "documenttitle", "document_name", "document_title", "title", "reportname")
_DATE_ALIASES = ("reportingperiod", "reportdate", "reporting_period", "report_date", "period", "date")
_ISSUER_ALIASES = ("issuer", "companyname", "company_name", "ratedentity", "rated_entity", "entity")
_RATINGS_ALIASES = ("ratings", "creditratings", "credit_ratings", "financialinstitutionratings")
_RATING_DRIVERS_ALIASES = ("ratingdrivers", "rating_drivers", "ratedrivers", "driver")
_SUMMARY_ALIASES = ("summary", "actionbasis", "rating_action_basis", "executivesummary")
_SUBSIDIARIES_ALIASES = ("subsidiaries", "associates", "subsidiariesandassociates")
_ANALYSTS_ALIASES = ("analysts", "analyst")
_FINANCIAL_POSITIONS_ALIASES = ("financialpositions", "financial_positions", "financialposition")
_STAKEHOLDERS_ALIASES = ("stakeholders", "shareholder", "stockholder")


def _detect_field_intent(field_path: str, field_schema: dict) -> FieldIntent:
    """Infer the logical intent of a field key to optimize fallback regex matches."""
    path_lower = field_path.lower()
    label = str(field_schema.get("label") or "").lower()
    combined = f"{path_lower} {label}"

    if any(a in path_lower or a in label for a in _TITLE_ALIASES):
        return FieldIntent.TITLE
    if any(a in path_lower or a in label for a in _DATE_ALIASES):
        return FieldIntent.DATE
    if any(a in path_lower or a in label for a in _ISSUER_ALIASES):
        return FieldIntent.ISSUER
    if any(a in path_lower or a in label for a in _RATINGS_ALIASES):
        return FieldIntent.RATINGS
    if any(a in path_lower or a in label for a in _RATING_DRIVERS_ALIASES):
        return FieldIntent.RATING_DRIVERS
    if any(a in path_lower or a in label for a in _SUMMARY_ALIASES):
        return FieldIntent.SUMMARY
    if any(a in path_lower or a in label for a in _SUBSIDIARIES_ALIASES):
        return FieldIntent.SUBSIDIARIES
    if any(a in path_lower or a in label for a in _ANALYSTS_ALIASES):
        return FieldIntent.ANALYSTS
    if any(a in path_lower or a in label for a in _FINANCIAL_POSITIONS_ALIASES):
        return FieldIntent.FINANCIAL_POSITIONS
    if any(a in path_lower or a in label for a in _STAKEHOLDERS_ALIASES):
        return FieldIntent.STAKEHOLDERS

    if "date" in combined:
        return FieldIntent.GENERIC_DATE
    expected = str(field_schema.get("type") or "string")
    if expected in {"number", "integer"}:
        return FieldIntent.GENERIC_NUMBER

    intent = FieldIntent.GENERIC_TEXT
    logger.debug("field_extractor: detect_intent field=%s intent=%s", field_path, intent.value)
    return intent


_NOISE_LINE_RE = re.compile(
    r"^(#+\s|!\[|!|[-=+*]{3,}|___{3,}|"
    r"(?:credit\s+rating\s+(?:rationale|action|report))|"
    r"(?:financial\s+institution\s+ratings)|"
    r"(?:table\s+of\s+contents)|"
    r"(?:page\s+\d+))",
    re.I,
)
_CATEGORY_HEADING_RE = re.compile(r"^[A-Z][A-Z\s&]{5,60}$")


@dataclass
class ExtractedCandidate:
    """An extraction result candidate retrieved from a single evidence chunk."""
    value: Any
    confidence: float
    evidence_ids: list[str]
    extraction_method: str = "keyword_rule"


class FieldExtractor:
    """Heuristic extractor parsing values from text blocks and tables."""

    def extract(self, field_path: str, field_schema: dict, pack: EvidencePack) -> list[ExtractedCandidate]:
        """Extract all candidate values for a schema field using the supplied evidence pack.

        Workflow:
        1. Checks for direct intent rules (titles, dates, analysts lists).
        2. Inspects structured table rows if table schemas are expected.
        3. Falls back to label-based text matches.
        """
        expected = str(field_schema.get("type") or "string")
        intent = _detect_field_intent(field_path, field_schema)
        logger.debug("field_extractor: extract field=%s expected=%s intent=%s items=%d", field_path, expected, intent.value, len([*pack.tables, *pack.text_snippets]))
        candidates: list[ExtractedCandidate] = []
        for item in [*pack.tables, *pack.text_snippets]:
            text = str(item.get("markdown") or item.get("text") or "")
            text = _strip_html(text)

            value = _fallback_extract(field_path, field_schema, expected, text, intent)
            logger.debug("field_extractor: fallback_extract field=%s intent=%s result=%s", field_path, intent.value, "found" if value is not None else "none")
            if value is not None:
                value = _clean_value(value, expected)
                candidates.append(
                    ExtractedCandidate(
                        value=value,
                        confidence=0.88,
                        evidence_ids=[str(item["evidence_id"])],
                        extraction_method="keyword_rule",
                    )
                )
                continue

            value = _extract_structured_value(expected, item, text, field_path)
            logger.debug("field_extractor: structured_value field=%s result=%s", field_path, "found" if value is not None else "none")
            if value is not None:
                cleaned_value = _clean_structured_value(value, expected, field_path)
                if cleaned_value is not None:
                    candidates.append(
                        ExtractedCandidate(
                            value=cleaned_value,
                            confidence=0.84,
                            evidence_ids=[str(item["evidence_id"])],
                            extraction_method="table_parser" if str(item.get("source_type", "")).startswith("table") else "keyword_rule",
                        )
                    )
                continue

            value = _extract_value(field_path, field_schema, expected, text, intent)
            logger.debug("field_extractor: extract_value field=%s result=%s", field_path, "found" if value is not None else "none")
            if value is None:
                continue
            value = _clean_value(value, expected)
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
        logger.debug("field_extractor: extract field=%s candidates=%d", field_path, len(candidates))
        return candidates


_FINANCIAL_TABLE_INDICATORS = re.compile(
    r"(?im)(income\s*statement|balance\s*sheet|cash\s*flow|profit|loss|revenue|"
    r"assets|liabilities|equity|dividend|earnings|financial\s*(highlights|summary)|"
    r"credit\s*rating\s*definition|rating\s*definition)"
)


def _is_financial_or_definition_table(item: dict, text: str) -> bool:
    """Analyze column names and headers to verify if the chunk is a financial table."""
    if not str(item.get("source_type", "")).startswith("table"):
        return False
    metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
    columns = metadata.get("columns") if isinstance(metadata, dict) else []
    if isinstance(columns, list) and any(
        _FINANCIAL_TABLE_INDICATORS.search(str(c)) for c in columns
    ):
        return True
    if _FINANCIAL_TABLE_INDICATORS.search(text[:500]):
        return True
    return False


_RATING_DRIVERS_END = re.compile(
    r"(?im)^(credit\s+rating\s+definition|rating\s+outlook|methodology\s+|disclaimer|"
    r"analyst\s+certification|important\s+notice|appendix)"
)


def _extract_rating_drivers(text: str) -> str | None:
    """Locate and return the core text lines of the rating drivers section."""
    start = re.search(r"(?im)rating\s+(drivers|rationale|considerations)", text)
    if not start:
        return None
    cutoff = _RATING_DRIVERS_END.search(text, start.end())
    section = text[start.start():cutoff.start()] if cutoff else text[start.start():]
    section = _strip_html(section)
    section = re.sub(r"(?im)#{1,6}\s+.*", "", section)
    section = re.sub(r"\n{3,}", "\n\n", section).strip()
    return section[:2000] if len(section) > 50 else None


def _extract_ratings(text: str) -> list[str] | None:
    """Parse text structure block-by-block to extract credit rating tuples."""
    lines = text.splitlines()
    ratings = []
    in_block = False
    started = False
    for line in lines:
        cleaned = line.strip()
        if re.search(r"(?im)^(?:#{1,3}\s*)?(?:rating|financial\s+institution\s+ratings?)", cleaned):
            in_block = True
            started = True
            continue
        if in_block:
            if not cleaned:
                if started:
                    continue
                break
            if re.search(r"(?im)^(#{1,3}\s+|disclaimer|appendix|important|methodology)", cleaned):
                break
            if _NOISE_LINE_RE.match(cleaned) or _CATEGORY_HEADING_RE.match(cleaned):
                continue
            rating_match = re.match(
                r"^([A-Z][A-Za-z0-9\s&/-]{2,60}?)\s{2,}([A-Z]{1,3}(?:\+|-)?)",
                cleaned,
            )
            if rating_match:
                ratings.append(f"{rating_match.group(1).strip()}: {rating_match.group(2)}")
                started = True
                continue
            if not started:
                m = re.match(r"^([A-Z][A-Za-z\s&/]+)", cleaned)
                if m:
                    ratings.append(m.group(1).strip())
                    started = True
    return ratings if ratings else None


def _extract_analysts(text: str) -> list[str] | None:
    """Search context blocks to extract listing of lead credit analysts."""
    lines = text.splitlines()
    analysts = []
    in_block = False
    for line in lines:
        cleaned = line.strip()
        if re.search(r"(?im)^(analysts?\s*[:]|analyst\s+team|research\s+analysts?)", cleaned):
            in_block = True
            continue
        if in_block:
            if not cleaned or re.search(r"(?im)^(disclaimer|important|appendix|rating)", cleaned):
                break
            name_match = re.match(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})", cleaned)
            if name_match:
                analysts.append(name_match.group(1).strip())
            elif re.search(r"[A-Z][a-z]+@", cleaned):
                continue
    return analysts[:10] if analysts else None


def _extract_subsidiaries(text: str) -> list[str] | None:
    """Scan company listings to parse child subsidiary corporation names."""
    lines = text.splitlines()
    entities = []
    in_block = False
    for line in lines:
        cleaned = line.strip()
        if re.search(r"(?im)(subsidiaries|significant\s+investments|associates?)[\s:]*$", cleaned):
            in_block = True
            continue
        if in_block:
            if not cleaned:
                continue
            border_match = re.match(r"^[-=+]{5,}$", cleaned)
            if border_match:
                continue
            if re.search(r"(?im)^(total|note|disclaimer|appendix)", cleaned):
                break
            if re.search(r"(?im)(income|profit|revenue|assets|liabilities)", cleaned):
                continue
            name_match = re.match(r"^([A-Z][A-Za-z\.\s&]+(?:Sdn\s+Bhd|Sdn\.\s*Bhd\.|Limited|Berhad|Bhd|Sdn|Inc|Corp|PLC|Ltd|Group|Holdings?))", cleaned)
            if name_match:
                entities.append(name_match.group(1).strip())
    return entities[:15] if entities else None


_COVER_DATE_RE = re.compile(
    r"(?im)(?:dated?\s*[:\-]?\s*)?"
    r"(\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{4}\b)"
)
_COVER_DATE_SHORT_RE = re.compile(
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
    re.I,
)
_COVER_TITLE_RE = re.compile(r"(?im)^#{1,3}\s+(credit\s+rating\s+(?:rationale|action|report)|.+)")
_COVER_RATED_ENTITY_RE = re.compile(
    r"(?im)(?:rated\s+(?:entity|obligor)[\s:]*)?"
    r"([A-Z][A-Za-z\.\s&]{5,80}?(?:Sdn\s+Bhd|Sdn\.\s*Bhd\.|Berhad|Bhd|Sdn|Inc|Corp|PLC|Ltd|Limited|Group|Holdings?))"
)
_COVER_ISSUER_LINE_RE = re.compile(
    r"^([A-Z][A-Za-z\.\s&]+(?:Sdn\s+Bhd|Sdn\.\s*Bhd\.|Berhad|Bhd|Sdn|Inc|Corp|PLC|Ltd|Limited|Group|Holdings?))"
)


def _fallback_extract(field_path: str, field_schema: dict, expected: str, text: str, intent: FieldIntent) -> Any:
    """Execute direct fallback rules based on target field intent classes."""
    if intent == FieldIntent.TITLE:
        m = _COVER_TITLE_RE.search(text)
        if m:
            return m.group(1).strip()[:120]
        m = re.search(r"(?im)^(.{10,80}?(?:Rationale|Report|Opinion))[\s]*$", text)
        if m:
            return m.group(1).strip()[:120]
        return None

    if intent == FieldIntent.DATE:
        m = _COVER_DATE_RE.search(text)
        if m:
            return m.group(1)
        m = _COVER_DATE_SHORT_RE.search(text)
        if m:
            return m.group(1)
        return None

    if intent == FieldIntent.ISSUER:
        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith(("#", ">", "|", "-", "!"))]
        for line in lines[:5]:
            m = _COVER_ISSUER_LINE_RE.match(line)
            if m:
                return m.group(1).strip()[:100]
        m = _COVER_RATED_ENTITY_RE.search(text)
        if m:
            return m.group(1).strip()[:100]
        return None

    if intent == FieldIntent.RATINGS:
        return _extract_ratings(text)

    if intent == FieldIntent.RATING_DRIVERS:
        return _extract_rating_drivers(text)

    if intent == FieldIntent.SUMMARY:
        m = re.search(
            r"(?im)^(?:#{1,3}\s*)?(?:summary|rating\s+action\s*(?:basis|rationale)|executive\s+summary)"
            r"[\s:]*\n+(.{50,1200}?)(?=\n#{1,3}\s|\n(?:\-{3,}|={3,})\s*$|\Z)",
            text,
            re.DOTALL,
        )
        if m:
            result = _strip_html(m.group(1))
            result = re.sub(r"\s+", " ", result).strip()
            return result[:1500] if len(result) > 30 else None
        return None

    if intent == FieldIntent.ANALYSTS:
        return _extract_analysts(text)

    if intent == FieldIntent.SUBSIDIARIES:
        return _extract_subsidiaries(text)

    return None


def _clean_value(value: Any, expected: str) -> Any:
    """Normalize extracted text (strip markdown headings, brackets, bold formatting)."""
    if value is None:
        return None
    if expected in {"number", "integer", "boolean"}:
        return value
    if isinstance(value, str):
        cleaned = _strip_html(value)
        cleaned = re.sub(r"!\[.*?\]\(.*?\)", "", cleaned)
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"\*{1,3}([^*]+?)\*{1,3}", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = cleaned.strip("| -")
        return cleaned or None
    return value


_COVER_NOISE_RE = re.compile(
    r"(?im)^(rating\s+rationale|credit\s+rating\s+rationale|financial\s+institution\s+ratings?|"
    r"credit\s+rating\s+report|table\s+of\s+contents|page\s+\d+)\s*!*\s*$",
)
_PUNCT_ONLY_RE = re.compile(r"^[\s!#\-*=+|_~`>.<{}\[\]()]{2,}$")
_RATING_AGENCY_RE = re.compile(r"(?i)(RAM\s+Rating|Malaysian\s+Rating|MARC|S&P|Moody|Fitch|Standard\s+&\s+Poor)")
_IMAGE_FIELD_RE = re.compile(r"(?i)(image|figure|chart|visual|logo|photo)")


def _is_cover_noise(value: str) -> bool:
    """Determine if a string is header/layout garbage instead of a real name or title."""
    stripped = value.strip()
    if not stripped or _PUNCT_ONLY_RE.match(stripped):
        logger.debug("field_extractor: cover_noise value=%s reason=%s", repr(value[:80]), "empty_or_punctuation")
        return True
    if _COVER_NOISE_RE.match(stripped):
        logger.debug("field_extractor: cover_noise value=%s reason=%s", repr(value[:80]), "cover_noise_pattern")
        return True
    if stripped.endswith("!") and stripped.count("!") >= 2:
        logger.debug("field_extractor: cover_noise value=%s reason=%s", repr(value[:80]), "multiple_exclamation")
        return True
    if re.match(r"^(#+\s|!{1,}\[)", stripped):
        logger.debug("field_extractor: cover_noise value=%s reason=%s", repr(value[:80]), "heading_or_image_marker")
        return True
    return False


def sanitize_extracted_value(value: Any, field_path: str, expected: str) -> Any:
    """Final sanitizer with authority to reject noisy unsupported values.

    Used by both deterministic and schema-constrained extraction to ensure
    phrases like ``RATING RATIONALE ! !``, markdown bold, image markers, and
    cover/logo noise cannot be returned as field values.
    """
    if value is None:
        return None
    if expected in {"number", "integer", "boolean"}:
        return value

    path_lower = field_path.lower()
    is_image_field = bool(_IMAGE_FIELD_RE.search(path_lower))

    if isinstance(value, str):
        if is_image_field:
            stripped = value.strip()
            if not stripped:
                return None
            return stripped
        cleaned = _clean_value(value, expected)
        if cleaned is None:
            return None
        if _is_cover_noise(cleaned):
            logger.debug("field_extractor: sanitize_reject field=%s value=%s reason=%s", field_path, repr(cleaned[:80] if cleaned else value), "cover_noise")
            return None
        if _RATING_AGENCY_RE.search(cleaned) and any(
            kw in path_lower for kw in ("title", "reportname", "documentname")
        ) and not _RATING_AGENCY_RE.search(path_lower):
            logger.debug("field_extractor: sanitize_reject field=%s value=%s reason=%s", field_path, repr(cleaned[:80] if cleaned else value), "rating_agency_as_title")
            return None
        if re.search(r"https?://\S+\.(jpg|jpeg|png|gif|svg|webp)", cleaned, re.I):
            cleaned = re.sub(r"https?://\S+\.(jpg|jpeg|png|gif|svg|webp)\S*", "", cleaned, flags=re.I).strip()
            if not cleaned:
                logger.debug("field_extractor: sanitize_reject field=%s value=%s reason=%s", field_path, repr(cleaned[:80] if cleaned else value), "image_stripped")
                return None
        return cleaned

    if isinstance(value, list):
        cleaned_list = []
        for v in value:
            cv = sanitize_extracted_value(v, field_path, expected)
            if cv is not None:
                cleaned_list.append(cv)
        return cleaned_list if cleaned_list else None

    if isinstance(value, dict):
        cleaned_dict = {}
        for k, v in value.items():
            cv = sanitize_extracted_value(v, field_path, expected)
            if cv is not None:
                cleaned_dict[k] = cv
        return cleaned_dict if cleaned_dict else None

    return value


def _clean_structured_value(value: Any, expected: str, field_path: str) -> Any:
    """Clean layout list items, discarding noise table headers and formatting tags."""
    if value is None:
        return None
    path_lower = field_path.lower()
    if isinstance(value, list):
        if expected == "array" and not any(
            kw in path_lower for kw in ("table", "financial", "statement", "row")
        ):
            cleaned = [_clean_value(v, "string") for v in value if isinstance(v, str)]
            cleaned = [v for v in cleaned if v and not _FINANCIAL_TABLE_INDICATORS.search(v)]
            return cleaned if cleaned else None
        cleaned = []
        for v in value:
            if isinstance(v, dict):
                cleaned.append({k: _clean_value(vk, "string") for k, vk in v.items()})
            elif isinstance(v, str):
                cv = _clean_value(v, "string")
                if cv:
                    cleaned.append(cv)
            else:
                cleaned.append(v)
        return cleaned if cleaned else None
    if isinstance(value, dict):
        return {k: _clean_value(v, "string") for k, v in value.items()}
    return value


def _strip_html(text: str) -> str:
    """Remove HTML tables tags and unescape text strings."""
    text = re.sub(r"<[\/]?(?:table|tr|td|th|thead|tbody|tfoot|caption|col|colgroup)[^>]*>", " ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text


def _extract_structured_value(expected: str, item: dict, text: str, field_path: str) -> Any:
    """Retrieve tabular layout lists or objects if columns or rows match expectation types."""
    if _is_financial_or_definition_table(item, text):
        path_lower = field_path.lower()
        if not any(kw in path_lower for kw in ("table", "financial", "statement", "row", "subsidiaries")):
            return None

    path_lower = field_path.lower()
    is_table_field = any(kw in path_lower for kw in ("table", "financial", "statement", "row"))

    if expected == "array":
        if not is_table_field and str(item.get("source_type", "")).startswith("table"):
            return None
        metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
        rows = metadata.get("rows") if isinstance(metadata, dict) else None
        if isinstance(rows, list) and rows:
            return rows
        lines = [
            line.strip(" |-") for line in text.splitlines()
            if line.strip(" |-")
            and not _NOISE_LINE_RE.search(line)
            and not _CATEGORY_HEADING_RE.match(line.strip())
        ]
        return lines[:20] if lines else None
    if expected == "object":
        if not is_table_field and str(item.get("source_type", "")).startswith("table"):
            return None
        metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
        rows = metadata.get("rows") if isinstance(metadata, dict) else None
        if isinstance(rows, list) and rows:
            return rows[0] if isinstance(rows[0], dict) else None
    return None


def _extract_value(field_path: str, field_schema: dict, expected: str, text: str, intent: FieldIntent = FieldIntent.GENERIC_TEXT) -> Any:
    """Scan text with regex to locate field labels followed by values (e.g. 'Key: Value')."""
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

    if intent == FieldIntent.GENERIC_DATE or "date" in " ".join([field_path, str(field_schema.get("description") or "")]).lower():
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
        match = re.search(r"(?att)(?<![\w.-])-?\d+(?:,\d{3})*(?:\.\d+)?(?![\w.-])", text)
        # Fix typo in regex flag "?att" -> "(?<![\w.-])"
        match = re.search(r"(?<![\w.-])-?\d+(?:,\d{3})*(?:\.\d+)?(?![\w.-])", text)
        if match:
            return _coerce(match.group(0), expected)
    if expected == "boolean":
        match = re.search(r"\b(true|false|yes|no|approved|rejected)\b", text, re.I)
        if match:
            return _coerce(match.group(1), expected)
    return None


def _humanize_key(value: str) -> str:
    """Split camelCase and snake_case keys into spaced words."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value.replace("_", " "))
    return re.sub(r"\s+", " ", spaced).strip()


def _coerce(value: str, expected: str) -> Any:
    """Coerce string value options into target Python datatypes."""
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