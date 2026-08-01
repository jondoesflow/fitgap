"""Minimal JSON-schema validation for structured model output.

Every structured response is checked against the tool's schema regardless of
provider, so a weaker model that returns malformed output fails loudly
instead of corrupting the register. Only the subset of JSON Schema the
fitgap tools actually use is supported: object/array/string/boolean/
integer/number types, ``required``, ``properties``, ``items`` and ``enum``.
"""

from __future__ import annotations

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def validate_instance(instance, schema: dict, path: str = "$") -> list[str]:
    """Return a list of human-readable validation errors (empty = valid)."""
    errors: list[str] = []
    expected_type = schema.get("type")
    check = _TYPE_CHECKS.get(expected_type)
    if check and not check(instance):
        return [f"{path}: expected {expected_type}, got {type(instance).__name__}"]

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']}")

    if expected_type == "object":
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        for key, subschema in schema.get("properties", {}).items():
            if key in instance:
                errors.extend(
                    validate_instance(instance[key], subschema, f"{path}.{key}")
                )
    elif expected_type == "array" and "items" in schema:
        for index, item in enumerate(instance):
            errors.extend(
                validate_instance(item, schema["items"], f"{path}[{index}]")
            )
    return errors
