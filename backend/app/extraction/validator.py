"""Validator utility for extracted field values.

Ensures extracted values conform to JSON schema types (number, integer, boolean, array, object).
"""
from __future__ import annotations

from typing import Any


def validate_field(value: Any, field_schema: dict, required: bool = False) -> list[str]:
    """Validate a single extracted field value against its schema configuration.

    Returns a list of validation error message strings. If empty, the value is valid.
    """
    if value is None:
        return ["Required field is missing"] if required else []
    expected = field_schema.get("type", "string")
    if expected == "number" and not isinstance(value, (int, float)):
        return ["Expected number"]
    if expected == "integer" and not isinstance(value, int):
        return ["Expected integer"]
    if expected == "boolean" and not isinstance(value, bool):
        return ["Expected boolean"]
    if expected == "array" and not isinstance(value, list):
        return ["Expected array"]
    if expected == "object" and not isinstance(value, dict):
        return ["Expected object"]
    return []
