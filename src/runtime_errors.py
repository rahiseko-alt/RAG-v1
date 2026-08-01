"""Runtime error formatting helpers."""
from __future__ import annotations

import re


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"pk-lf-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-lf-[A-Za-z0-9_\-]{8,}"),
]


def safe_error_message(exc: Exception) -> str:
    """Return an exception message with API-key-like values redacted."""
    text = str(exc)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[redacted-key]", text)
    return text
