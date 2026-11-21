"""Canonical JSON and SHA-256 for filing payloads (docs/08 §1).

The hash binds an approval to exact bytes, so the encoding is fixed: sorted keys, no
insignificant whitespace, Decimals as strings, datetimes as UTC ISO 8601 with 'Z', UTF-8,
NFC-normalised strings. Floats are refused: money is never a float (CLAUDE.md rule 1).
Normalising Decimal("10.0") to "10.00" is the payload schema's job, not this module's.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any


class CanonicalError(ValueError):
    """The payload contains a value with no canonical encoding."""


def _normalise(value: Any, path: str) -> Any:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        raise CanonicalError(f"{path}: floats are not allowed; use a decimal string")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalError(f"{path}: non-finite Decimal")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalError(f"{path}: datetime must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalError(f"{path}: keys must be strings, got {type(key).__name__}")
            nkey = unicodedata.normalize("NFC", key)
            if nkey in out:
                raise CanonicalError(f"{path}: keys collide after NFC normalisation: {nkey!r}")
            out[nkey] = _normalise(item, f"{path}.{nkey}")
        return out
    if isinstance(value, list | tuple):
        return [_normalise(item, f"{path}[{i}]") for i, item in enumerate(value)]
    raise CanonicalError(f"{path}: unsupported type {type(value).__name__}")


def canonical_json(payload: Mapping[str, Any]) -> bytes:
    normalised = _normalise(payload, "$")
    text = json.dumps(normalised, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_hash(payload: Mapping[str, Any]) -> str:
    """Hex SHA-256 of canonical_json(payload)."""
    return sha256_hex(canonical_json(payload))
