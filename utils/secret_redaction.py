"""Central redaction helpers for credentials that may appear in exceptions."""

from __future__ import annotations

import re
from typing import Any


_BEARER_RE = re.compile(
    r"(?i)(?P<prefix>\b(?:authorization\s*:\s*)?bearer\s+)"
    r"(?P<secret>[^\s,;]+)"
)
_TOKEN_FIELD_RE = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:access_token|refresh_token|id_token|"
    r"api[_-]?key)[\"']?\s*[:=]\s*[\"']?)"
    r"(?P<secret>[^\"'\s,;}\]]+)"
)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\b"
)
_MANAGED_KEY_RE = re.compile(r"\b(?:sk|sess)-[A-Za-z0-9._-]{8,}\b")


def redact_secrets(value: Any) -> str:
    """Return text with common OAuth/API-key representations removed."""
    text = str(value)
    text = _BEARER_RE.sub(lambda match: match.group("prefix") + "[REDACTED]", text)
    text = _TOKEN_FIELD_RE.sub(
        lambda match: match.group("prefix") + "[REDACTED]",
        text,
    )
    text = _JWT_RE.sub("[REDACTED]", text)
    return _MANAGED_KEY_RE.sub("[REDACTED]", text)


__all__ = ["redact_secrets"]
