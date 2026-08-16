"""Sensitive-data redaction and stable content digests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "password",
    "token",
    "secret",
    "private_key",
    "access_key",
    "headers",
    "env",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(redact(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
