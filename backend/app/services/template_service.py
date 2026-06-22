"""Template service.

PLACEHOLDER business logic for creating / managing extraction templates.
The in-memory store lives in ``app.data.mock``; swap for a real DB later.
"""
from __future__ import annotations

from typing import Optional

from app.models.template import (
    ExtractionTemplate,
    TemplateCreate,
    TemplateFieldDefinition,
)


def _store():
    # Lazy import to avoid circular import with app.data.mock at module load.
    from app.data.mock import store
    return store


def create_template(payload: TemplateCreate) -> ExtractionTemplate:
    store = _store()
    template = ExtractionTemplate(
        id=store.gen_id("tpl"),
        name=payload.name,
        description=payload.description,
        document_type=payload.document_type,
        fields=payload.fields,
        ocr_method=payload.ocr_method,
        chunking_strategy=payload.chunking_strategy,
        max_pages=payload.max_pages,
        loop_condition=payload.loop_condition,
        source_document_id=payload.source_document_id,
    )
    store.templates[template.id] = template
    return template


def list_templates() -> list[ExtractionTemplate]:
    return list(_store().templates.values())


def get_template(template_id: str) -> Optional[ExtractionTemplate]:
    return _store().templates.get(template_id)


def update_template(template_id: str, patch: dict) -> Optional[ExtractionTemplate]:
    store = _store()
    tpl = store.templates.get(template_id)
    if not tpl:
        return None
    data = tpl.model_dump()
    data.update(patch)
    from app.models.document import utcnow

    data["updated_at"] = utcnow()
    tpl = ExtractionTemplate(**data)
    store.templates[template_id] = tpl
    return tpl


def delete_template(template_id: str) -> bool:
    return _store().templates.pop(template_id, None) is not None


def add_field(template_id: str, field: TemplateFieldDefinition) -> Optional[ExtractionTemplate]:
    store = _store()
    tpl = store.templates.get(template_id)
    if not tpl:
        return None
    tpl.fields.append(field)
    return update_template(template_id, {})
