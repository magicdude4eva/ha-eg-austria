"""Redaction helpers (spec 06).

S1 provides ``redact_text()`` for log and error messages; S6 extends this
module with key-based redaction for diagnostics.
"""

from __future__ import annotations

from collections.abc import Iterable
import re

REDACTED = "**REDACTED**"

_PATTERNS = (
    # JWT-like tokens (session cookie).
    re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"),
    # Portal personal access tokens.
    re.compile(r"ecapi_[A-Za-z0-9_]+", re.IGNORECASE),
    # Austrian metering point numbers: AT + 6-digit operator + 25 characters.
    re.compile(r"AT\d{6}[0-9A-Z]{25}"),
    # Email addresses.
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
)


def redact_text(text: str, sensitive: Iterable[object] = ()) -> str:
    """Replace tokens, metering point numbers, emails and the given values.

    ``sensitive`` holds the configured member number, location and metering
    point / community ids (any type, compared as text). They are matched as
    whole tokens, so ``1001`` does not hit ``11001``.
    """

    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    values = sorted({str(v) for v in sensitive if str(v)}, key=len, reverse=True)
    for value in values:
        text = re.sub(
            rf"(?<![0-9A-Za-z]){re.escape(value)}(?![0-9A-Za-z])", REDACTED, text
        )
    return text
