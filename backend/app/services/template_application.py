"""Template application service.

PLACEHOLDER. Implement the real extraction-by-template logic here.

Responsibilities:
  * For each document, run the configured OCR + chunking + extraction.
  * Map extracted values to the template's field definitions.
  * Validate values against field rules.
  * Return per-document ``BatchItemResult``.

This is the core deterministic extraction pipeline — implement it by
composing: document_parser -> ocr_extraction -> ocr_correction ->
sentence_splitter -> chunker -> embedding -> field matching.
"""
from __future__ import annotations

import time
from typing import Optional

from app.models.batch import BatchItemResult, BatchItemStatus
from app.models.field import EditableExtractionField
from app.services import ocr_extraction, document_parser  # noqa: F401  (placeholders)


def _store():
    # Lazy import to avoid circular import with app.data.mock.
    from app.data.mock import store
    return store


def apply_template_to_document(template_id: str, document_id: str) -> BatchItemResult:
    """Apply a template to a single document and return a result row.

    TODO: replace mock matching with a real deterministic pipeline.
    """
    store = _store()
    start = time.perf_counter()
    tpl = _store().templates.get(template_id)
    doc = _store().documents.get(document_id)

    if not tpl or not doc:
        return BatchItemResult(
            document_id=document_id,
            document_name=doc.name if doc else "unknown",
            status=BatchItemStatus.FAILED,
            error="Template or document not found",
            latency_ms=0.0,
        )

    # --- Placeholder extraction: derive mock field values from the OCR blocks
    # of the document (if any), otherwise from the seeded mock fields. ---
    fields: list[EditableExtractionField] = []
    ocr = _store().ocr_results.get(document_id)
    # Build a quick lookup of text by key from mock seed fields
    seed_values = _store().seed_field_values.get(document_id, {})

    matched = 0
    mismatched = 0
    missing = 0
    for fdef in tpl.fields:
        value = seed_values.get(fdef.key)
        conf = 0.9
        source = "seed"
        if value is None and ocr:
            # Fallback: scan blocks for the field hint
            value = _guess_from_blocks(ocr, fdef.key, fdef.label)
            conf = 0.72
            source = "ocr"
        if value is None:
            # Placeholder fallback: generate a deterministic mock value with
            # low-medium confidence so the demo shows a "weak extraction"
            # rather than an empty/zero result.
            # TODO: replace with a real extraction pipeline.
            value = _mock_value(fdef, doc)
            conf = 0.58
            source = "mock"
            mismatched += 1
        else:
            matched += 1

        fields.append(
            EditableExtractionField(
                id=f"{document_id}:{fdef.key}",
                label=fdef.label,
                key=fdef.key,
                type=fdef.type,
                value=value,
                raw_value=value,
                confidence=conf,
                confidence_level=("high" if conf >= 0.9 else "medium" if conf >= 0.7 else "low"),
                required=fdef.required,
                edited=False,
                valid=True,
                options=fdef.default_value,
                notes=f"source={source}",
            )
        )

    latency_ms = (time.perf_counter() - start) * 1000 + 120  # mock latency
    overall_conf = sum(f.confidence for f in fields) / max(len(fields), 1)

    return BatchItemResult(
        document_id=document_id,
        document_name=doc.name,
        status=BatchItemStatus.DONE,
        fields=fields,
        overall_confidence=round(overall_conf, 3),
        latency_ms=round(latency_ms, 1),
        matched=matched,
        mismatched=mismatched,
        missing=missing,
    )


def apply_template_batch(template_id: str, document_ids: list[str]):
    """Apply a template to many documents. Yields progress info.

    Returns the final ``BatchProcessingResult``. In a real implementation this
    could be an async generator or a background task.
    """
    from app.models.batch import BatchProcessingResult
    from app.models.document import utcnow

    tpl = _store().templates.get(template_id)
    items = [apply_template_to_document(template_id, did) for did in document_ids]
    done = sum(1 for i in items if i.status == BatchItemStatus.DONE)
    failed = sum(1 for i in items if i.status == BatchItemStatus.FAILED)
    avg_conf = sum(i.overall_confidence for i in items) / max(len(items), 1)
    avg_lat = sum(i.latency_ms for i in items) / max(len(items), 1)

    result = BatchProcessingResult(
        id=_store().gen_id("batch"),
        template_id=template_id,
        template_name=tpl.name if tpl else "Unknown",
        finished_at=utcnow(),
        total=len(document_ids),
        done=done,
        failed=failed,
        items=items,
        average_confidence=round(avg_conf, 3),
        average_latency_ms=round(avg_lat, 1),
    )
    _store().batches[result.id] = result
    return result


def _guess_from_blocks(ocr, key: str, label: str) -> Optional[str]:
    """Naive placeholder: look for a block whose text mentions the label."""
    target = label.lower()
    for block in ocr.blocks:
        text = (block.text or "").lower()
        if target and target in text:
            # return the text after a colon if present
            if ":" in block.text:
                return block.text.split(":", 1)[1].strip()
            return block.text.strip()
    return None


def _mock_value(fdef, doc) -> str:
    """Generate a deterministic placeholder value for a field with no data.

    TODO: remove once a real extraction pipeline is implemented.
    """
    import hashlib

    h = int(hashlib.md5(f"{doc.id}:{fdef.key}".encode()).hexdigest(), 16)
    t = fdef.type.value if hasattr(fdef.type, "value") else str(fdef.type)
    if t in ("number", "currency"):
        return str(20 + (h % 800) / 10)
    if t == "date":
        return "2024-03-12"
    if t == "boolean":
        return "true" if h % 2 == 0 else "false"
    if t == "select" and fdef.default_value:
        return fdef.default_value
    # text / default
    return f"{fdef.label} (auto-detected)"
