"""Token kinds and local JWT expiry (ADR 0007).

S1 provides what the API client needs: the token kind and the ``exp`` claim of
a session JWT, decoded locally without a signature check. S2 extends this
module with parsing of pasted input (quotes, ``portal.jwtToken=…; Path=/``).
"""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum
import json


class TokenKind(StrEnum):
    """How a token is sent to the portal."""

    PERSONAL = "personal"  # ``ecapi_…``, header ``Authorization: Bearer``
    SESSION = "session"  # session JWT, header ``Cookie: portal.jwtToken=``


class TokenFormatError(ValueError):
    """The value is not a JWT with a numeric ``exp`` claim."""


def jwt_expiry(token: str) -> int:
    """Return the ``exp`` claim (Unix seconds) of a session JWT.

    Only ``exp`` is read; every other claim (email, ``sub``) is discarded.
    The error message never contains the token.
    """

    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise TokenFormatError("not a JWT")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError) as err:
        raise TokenFormatError("JWT payload is not JSON") from err
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if isinstance(exp, bool) or not isinstance(exp, int | float):
        raise TokenFormatError("JWT has no numeric exp")
    return int(exp)
