"""
Schema Sanitizer for Google Cloud Code / Gemini API.
Transforms standard JSON Schema from Anthropic tool declarations
into strict Gemini Protobuf-compatible schema.
"""

from typing import Any, Dict, Set

# Fields that Google Gemini API rejects and must be stripped
UNSUPPORTED_SCHEMA_FIELDS: Set[str] = {
    "additionalProperties",
    "$schema",
    "$id",
    "$comment",
    "$ref",
    "$defs",
    "definitions",
    "const",
    "contentMediaType",
    "contentEncoding",
    "if",
    "then",
    "else",
    "not",
    "patternProperties",
    "unevaluatedProperties",
    "unevaluatedItems",
    "dependentRequired",
    "dependentSchemas",
    "propertyNames",
    "minContains",
    "maxContains",
}

TYPE_MAP: Dict[str, str] = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def score_schema_option(schema: Any) -> int:
    """Scores schema option: objects (3) > arrays (2) > scalars (1) > null (0)."""
    if not isinstance(schema, dict):
        return 0
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        return 3
    if t == "array" or "items" in schema or "prefixItems" in schema:
        return 2
    if t and t != "null":
        return 1
    return 0


def sanitize_schema(schema: Any) -> Any:
    """
    Recursively transforms a JSON schema dict into a Gemini-compatible format:
    - Converts 'type' to uppercase (e.g., 'object' -> 'OBJECT')
    - Handles 'const' by converting to enum
    - Handles type arrays (e.g. ['string', 'null'] -> type: 'STRING', nullable: True)
    - Resolves anyOf / oneOf to the most informative option
    - Preserves constraints (minLength, maximum, pattern, etc.) as description hints
    - Strips unsupported Protobuf schema properties
    - Ensures every ARRAY has a valid single items object schema
    - Strips properties/required from non-OBJECT schemas
    - Filters 'required' list to only contain declared properties
    """
    if not isinstance(schema, dict):
        if isinstance(schema, list):
            return [sanitize_schema(item) for item in schema]
        return schema

    # 1. Handle anyOf / oneOf flattening if type is not directly declared
    if "type" not in schema:
        for union_key in ("anyOf", "oneOf"):
            if union_key in schema and isinstance(schema[union_key], list) and schema[union_key]:
                best_opt = max(schema[union_key], key=score_schema_option, default=None)
                if isinstance(best_opt, dict):
                    merged = {**best_opt, **{k: v for k, v in schema.items() if k != union_key}}
                    return sanitize_schema(merged)

    result: Dict[str, Any] = {}

    # Extract all declared property names first
    declared_properties = set()
    if "properties" in schema and isinstance(schema["properties"], dict):
        declared_properties = set(schema["properties"].keys())

    # Collect constraints to append to description
    constraint_hints = []
    for constraint in ("minLength", "maxLength", "minimum", "maximum", "pattern", "minItems", "maxItems"):
        if constraint in schema and not isinstance(schema[constraint], (dict, list)):
            constraint_hints.append(f"{constraint}: {schema[constraint]}")

    # Convert const to enum
    if "const" in schema and "enum" not in schema:
        result["enum"] = [str(schema["const"])]

    for key, value in schema.items():
        if key in UNSUPPORTED_SCHEMA_FIELDS:
            continue

        if key == "type":
            if isinstance(value, str):
                result["type"] = TYPE_MAP.get(value.lower(), value.upper())
            elif isinstance(value, list):
                # Handle multi-type array e.g. ["string", "null"]
                non_null = [t for t in value if t != "null"]
                if "null" in value:
                    result["nullable"] = True
                chosen = non_null[0] if non_null else "string"
                result["type"] = TYPE_MAP.get(chosen.lower(), chosen.upper())

        elif key == "properties" and isinstance(value, dict):
            props = {}
            for prop_name, prop_schema in value.items():
                props[prop_name] = sanitize_schema(prop_schema)
            result["properties"] = props

        elif key == "items" and isinstance(value, (dict, list)):
            result["items"] = sanitize_schema(value)

        elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
            # Flatten or map child schemas
            result[key] = [sanitize_schema(item) for item in value]

        elif key == "required" and isinstance(value, list):
            # Only keep required properties that are actually in declared_properties
            if declared_properties:
                valid_required = [p for p in value if p in declared_properties]
                if valid_required:
                    result["required"] = valid_required
            else:
                result["required"] = value

        elif key in ("description", "format", "nullable"):
            result[key] = value

        elif key == "enum" and isinstance(value, list):
            result["enum"] = [str(x) for x in value]

    # Append constraint hints to description if any were present
    if constraint_hints:
        hint_text = "; ".join(constraint_hints)
        existing_desc = result.get("description", "")
        result["description"] = f"{existing_desc} ({hint_text})".strip() if existing_desc else hint_text

    # Phase 6 (Issue #368): Google Protobuf strictly requires every ARRAY type to have an items object.
    # Tuple-form prefixItems / items without inner items will otherwise throw:
    # "parameters.properties[...].items.items: missing field"
    if result.get("type") == "ARRAY":
        if "items" not in result or not isinstance(result["items"], (dict, list)):
            prefix = schema.get("prefixItems")
            if isinstance(prefix, list) and prefix:
                result["items"] = sanitize_schema(prefix[0])
            else:
                result["items"] = {"type": "STRING"}
        elif isinstance(result["items"], list):
            # Google Protobuf expects a single items object schema, not a tuple list
            result["items"] = result["items"][0] if result["items"] else {"type": "STRING"}

    # Phase 7: Google Protobuf strictly rejects properties and required on non-OBJECT schemas
    if result.get("type") != "OBJECT":
        result.pop("properties", None)
        result.pop("required", None)

    # If type is OBJECT and properties is empty, ensure it's still a valid object
    if result.get("type") == "OBJECT" and "properties" not in result:
        result["properties"] = {}

    return result
