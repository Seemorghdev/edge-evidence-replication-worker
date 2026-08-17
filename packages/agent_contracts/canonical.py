"""Deterministic, fail-closed canonical encoding for bounded contracts."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
from typing import Any


class CanonicalEncodingError(ValueError):
    """Raised when a value cannot be encoded without ambiguity."""


def _payload(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise CanonicalEncodingError("floating-point values are forbidden")
    if isinstance(value, Enum):
        return _payload(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        result: dict[str, Any] = {}
        schema = getattr(value, "schema", None)
        if schema is not None:
            result["schema"] = _payload(schema)
        for item in fields(value):
            result[item.name] = _payload(getattr(value, item.name))
        return result
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalEncodingError("mapping keys must be strings")
        return {key: _payload(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_payload(item) for item in value]
    raise CanonicalEncodingError(f"unsupported canonical type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return stable UTF-8 JSON text with sorted keys and compact separators."""

    try:
        return json.dumps(
            _payload(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, CanonicalEncodingError):
            raise
        raise CanonicalEncodingError(str(exc)) from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
