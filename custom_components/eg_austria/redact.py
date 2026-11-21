"""Redaction helpers (spec 06).

``redact_text()`` (S1) scrubs log and error messages. S6 adds what the
diagnostics download needs: the key list ``TO_REDACT``, ``redact_tree()``
(key-based redaction plus ``redact_text()`` on every string) and
``schema_fingerprint()`` (key to type name, no values).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any, Final

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


# Keys whose values never leave the box (spec 06 "Redaction"): config entry
# data plus the ``metering-point`` and ``portal-session`` keys found in G1.
TO_REDACT: Final = frozenset(
    {
        "token",
        "member_number",
        "location_id",
        "mp_ids",
        "id",
        "energyCommunityId",
        "communityId",
        "meteringPointId",
        "meteringPointNumber",
        "email",
        "name",
        "firstName",
        "lastName",
        "address",
        "street",
        "zip",
        "city",
        "iban",
        "bic",
        "instance",
        "traceId",
        "memberId",
        "locationId",
        "number",
        "houseNumber",
        "postalCode",
        "stairsFloorDoor",
        "displayName",
        "energyCommunityName",
        "fromName",
        "toName",
        "fromMeteringpointNumber",
        "toMeteringpointNumber",
        "ecNumber",
        "substationName",
    }
)

# Dates and timestamps are not free text: an id that happens to equal a
# date part (a location ``12`` in ``2026-12-01``) must not mangle them.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}([T ][0-9:.]+([+-]\d{2}:\d{2}|Z)?)?")


def _redact_str(text: str, sensitive: Iterable[object]) -> str:
    if _ISO_DATE.fullmatch(text):
        return text
    return redact_text(text, sensitive)


def redact_tree(
    value: Any,
    sensitive: Iterable[object] = (),
    keys: frozenset[str] = TO_REDACT,
) -> Any:
    """Return a copy with ``keys`` replaced and every string scrubbed.

    Values under a key in ``keys`` become ``REDACTED`` whatever their type;
    every other string (dict keys included) goes through ``redact_text()``
    with ``sensitive``. Numbers, booleans and ``None`` are kept.
    """

    sensitive = list(sensitive)
    if isinstance(value, Mapping):
        return {
            _redact_str(str(k), sensitive): (
                REDACTED if k in keys else redact_tree(v, sensitive, keys)
            )
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value, key=str) if isinstance(value, set | frozenset) else value
        return [redact_tree(v, sensitive, keys) for v in items]
    if isinstance(value, str):
        return _redact_str(value, sensitive)
    return value


def schema_fingerprint(value: Any) -> Any:
    """Return key -> type name, recursively, first list element only.

    ``{"a": [{"b": 1.5}], "c": None}`` becomes
    ``{"a": [{"b": "float"}], "c": "null"}``. No values survive.
    """

    if isinstance(value, Mapping):
        return {str(k): schema_fingerprint(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [schema_fingerprint(value[0])] if value else []
    if value is None:
        return "null"
    return type(value).__name__
