"""Static checks that an extraction schema is safe for Claude structured outputs.

Run at registration time so a plugin mistake fails at import/CI time instead of as an API 400
or - worse - a silently relaxed schema (the SDK moves unsupported keywords into descriptions).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Keywords the SDK's transform_schema passes through as real constraints.
_ALLOWED = {
    "type", "anyOf", "oneOf", "allOf", "enum", "const", "description", "title", "properties",
    "additionalProperties", "required", "format", "items", "$ref", "$defs",
}  # fmt: skip


def lint_schema(model: type[BaseModel]) -> list[str]:
    schema = model.model_json_schema()
    problems: list[str] = []
    defs: dict[str, Any] = schema.get("$defs", {})

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        for key in node:
            if key not in _ALLOWED and key != "minItems":
                problems.append(f"{path}: unsupported keyword {key!r}")
        if node.get("minItems") not in (None, 0, 1):
            problems.append(f"{path}: minItems > 1 is not supported")
        if node.get("type") == "object" and "properties" in node:
            props = node["properties"]
            missing = set(props) - set(node.get("required", []))
            if missing:
                problems.append(f"{path}: properties must all be required (use `X | None`): {sorted(missing)}")
            for name, sub in props.items():
                walk(sub, f"{path}.{name}")
        for key in ("anyOf", "oneOf", "allOf"):
            for i, sub in enumerate(node.get(key, [])):
                walk(sub, f"{path}.{key}[{i}]")
        if "items" in node:
            walk(node["items"], f"{path}[]")

    walk({k: v for k, v in schema.items() if k != "$defs"}, model.__name__)
    for name, sub in defs.items():
        walk(sub, f"$defs.{name}")

    # Recursive schemas are not supported: detect cycles in $ref edges.
    edges = {name: _refs(sub) for name, sub in defs.items()}

    def has_cycle(start: str) -> bool:
        stack, seen = [start], set()
        while stack:
            cur = stack.pop()
            for nxt in edges.get(cur, ()):
                if nxt == start:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    problems += [f"$defs.{n}: recursive schema" for n in defs if has_cycle(n)]
    return problems


def _refs(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            found.add(ref.removeprefix("#/$defs/"))
        for v in node.values():
            found |= _refs(v)
    elif isinstance(node, list):
        for v in node:
            found |= _refs(v)
    return found
