"""Token kinds and local JWT expiry (ADR 0007).

S1 provides what the API client needs: the token kind and the ``exp`` claim of
a session JWT, decoded locally without a signature check. S2 adds parsing of
pasted input (whitespace, quotes, ``portal.jwtToken=…; Path=/``) and kind
detection for the config flow (spec 02).
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from enum import StrEnum
import json
import re


class TokenKind(StrEnum):
    """How a token is sent to the portal."""

    PERSONAL = "personal"  # ``ecapi_…``, header ``Authorization: Bearer``
    SESSION = "session"  # session JWT, header ``Cookie: portal.jwtToken=``


class TokenFormatError(ValueError):
    """The value is not a JWT with a numeric ``exp`` claim."""


def _jwt_claims(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise TokenFormatError("not a JWT")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError) as err:
        raise TokenFormatError("JWT payload is not JSON") from err
    if not isinstance(claims, dict):
        raise TokenFormatError("JWT has no numeric exp")
    return claims


def jwt_issued_at(token: str) -> int | None:
    """Return the ``iat`` claim (Unix seconds) of a session JWT, if any.

    For diagnostics (spec 06); every other claim is discarded.
    """

    try:
        iat = _jwt_claims(token).get("iat")
    except TokenFormatError:
        return None
    if isinstance(iat, bool) or not isinstance(iat, int | float):
        return None
    return int(iat)


def jwt_expiry(token: str) -> int:
    """Return the ``exp`` claim (Unix seconds) of a session JWT.

    Only ``exp`` is read; every other claim (email, ``sub``) is discarded.
    The error message never contains the token.
    """

    exp = _jwt_claims(token).get("exp")
    if isinstance(exp, bool) or not isinstance(exp, int | float):
        raise TokenFormatError("JWT has no numeric exp")
    return int(exp)


COOKIE_PREFIX = "portal.jwtToken="
_PERSONAL_RE = re.compile(r"ecapi_[a-z0-9_]+")
_JWT_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_QUOTES = "\"'`"


@dataclass(frozen=True, slots=True)
class ParsedToken:
    """A cleaned token, its kind and (session JWT only) its ``exp`` claim."""

    token: str = field(repr=False)
    kind: TokenKind
    exp: int | None = None


def _strip(value: str) -> str:
    value = value.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        value = value[1:-1].strip()
    return value


def parse_token(raw: str) -> ParsedToken:
    """Clean pasted input and detect the token kind (ADR 0007).

    Accepted: a personal token (``ecapi_…``), a raw session JWT, or a cookie
    string containing ``portal.jwtToken=<jwt>`` (anything after ``;`` is
    dropped), each optionally wrapped in whitespace and quotes. Only the
    ``exp`` claim of a JWT is read. Raises ``TokenFormatError`` (never with
    the token in the message) for anything else. Expiry against the clock is
    the caller's check.
    """

    value = _strip(raw)
    if _PERSONAL_RE.fullmatch(value):
        return ParsedToken(value, TokenKind.PERSONAL)
    if COOKIE_PREFIX in value:
        value = value.split(COOKIE_PREFIX, 1)[1]
    value = _strip(value.split(";", 1)[0])
    if not _JWT_RE.fullmatch(value):
        raise TokenFormatError("not a personal token or JWT")
    return ParsedToken(value, TokenKind.SESSION, jwt_expiry(value))
