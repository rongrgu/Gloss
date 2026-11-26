"""JSON Schema helpers for providers with strict structured output (DESIGN.md §6).

OpenAI's strict mode (the Responses API, and presumably `codex exec --output-schema`) wants every object
to list all of its properties in `required` and to forbid extra keys. Gloss's schemas leave some keys
optional, so `to_strict` makes those nullable, and `from_strict` drops the resulting nulls again so
callers see the same shape as with Claude.
"""

from __future__ import annotations

import copy
import json
from typing import Any


def to_strict(schema: dict) -> dict:
    s = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                props = node["properties"]
                required = set(node.get("required", []))
                for k, sub in props.items():
                    if k not in required:
                        _nullable(sub)
                node["required"] = list(props)
                node["additionalProperties"] = False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(s)
    return s


def _nullable(sub: dict) -> None:
    t = sub.get("type")
    if isinstance(t, str) and t != "null":
        sub["type"] = [t, "null"]
    elif isinstance(t, list) and "null" not in t:
        sub["type"] = [*t, "null"]
    if "enum" in sub and None not in sub["enum"]:
        sub["enum"] = [*sub["enum"], None]


def from_strict(data: Any, schema: dict) -> Any:
    """Remove keys that were optional in `schema` and came back null."""
    if isinstance(data, dict) and isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
        required = set(schema.get("required", []))
        out = {}
        for k, v in data.items():
            sub = schema["properties"].get(k, {})
            if v is None and k not in required:
                continue
            out[k] = from_strict(v, sub)
        return out
    if isinstance(data, list) and isinstance(schema, dict) and isinstance(schema.get("items"), dict):
        return [from_strict(v, schema["items"]) for v in data]
    return data


def parse_json(text: str) -> Any:
    """The answer as JSON, tolerating a ```json fence around it."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return json.loads(t)
