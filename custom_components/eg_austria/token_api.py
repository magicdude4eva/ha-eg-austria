"""Create and revoke the integration's personal token (ADR 0007, spec 01).

The only module that can send anything but ``GET``. Safety by construction:

* only ``config_flow.py`` imports it (a test checks this), and only after
  the user consented; runs (coordinator, services) can't reach it;
* its own frozen whitelist holds exactly one ``POST`` and one ``DELETE``,
  both on ``/v1/portal/portal-session/token``; no generic request method;
* it takes a session JWT (the token can't manage tokens) and sends it as the
  cookie header on a session without a cookie jar;
* one request per call, no retry: a timed-out create may exist anyway;
* the success body (it holds the token) is never logged; error bodies only
  through ``redact_text()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import logging
import re
from typing import Any, Final

import aiohttp
from yarl import URL

from .api import (
    BASE_URL,
    COOKIE_NAME,
    REQUEST_TIMEOUT,
    USER_AGENT,
    raise_for_status,
)
from .exceptions import (
    ApiChangedError,
    NotWhitelistedError,
    PortalUnavailableError,
    SchemaError,
)
from .redact import redact_text

_LOGGER = logging.getLogger(__name__)

PATH_TOKEN: Final = "/v1/portal/portal-session/token"  # noqa: S105 - path
PATH_TOKEN_ID: Final = "/v1/portal/portal-session/token/{token_id}"  # noqa: S105

# ADR 0002/0007: the only (method, path template) pairs this module can send.
TOKEN_WHITELIST: Final[frozenset[tuple[str, str]]] = frozenset(
    (("POST", PATH_TOKEN), ("DELETE", PATH_TOKEN_ID))
)

TOKEN_ID_RE: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TOKEN_RE: Final = re.compile(r"ecapi_[a-z0-9_]+")
_JSON_TYPES: Final = ("application/json", "application/problem+json")
_OK_CREATE: Final = (200, 201)
_OK_REVOKE: Final = (200, 202, 204, 404)  # 404: already gone


@dataclass(frozen=True, slots=True)
class CreatedToken:
    """The validated create response; ``token`` is returned only once."""

    id: str
    token: str = field(repr=False)
    expires_at: datetime


def format_expires_at(value: datetime) -> str:
    """UTC ISO 8601 with milliseconds and ``Z`` (as the portal's JS sends)."""

    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def parse_created_token(raw: Any, now: datetime) -> CreatedToken:
    """Validate the create response (spec 01 "Token module")."""

    if not isinstance(raw, dict):
        raise SchemaError("S1", "", "token response is not an object")
    token = raw.get("token")
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        # Never echo the value.
        raise SchemaError("S3", "token", "not a personal token")
    token_id = raw.get("id")
    if isinstance(token_id, bool) or not isinstance(token_id, int | str):
        raise SchemaError("S3", "id", "not an int or string")
    token_id = str(token_id)
    if not TOKEN_ID_RE.fullmatch(token_id):
        raise SchemaError("S3", "id", "unexpected format")
    expires = raw.get("expiresAt")
    if not isinstance(expires, str):
        raise SchemaError("S2", "expiresAt", "missing")
    try:
        expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError as err:
        raise SchemaError("S4", "expiresAt", "not an ISO timestamp") from err
    if expires_at.tzinfo is None:
        raise SchemaError("S4", "expiresAt", "no time zone")
    if expires_at <= now:
        raise SchemaError("S7", "expiresAt", "already expired")
    if raw.get("revokedAt") is not None:
        raise SchemaError("S7", "revokedAt", "token is revoked")
    return CreatedToken(id=token_id, token=token, expires_at=expires_at)


class PortalTokenClient:
    """Create or revoke a token with the user's session cookie."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        cookie: str,
        *,
        utcnow: Any = lambda: datetime.now(UTC),
    ) -> None:
        """Nothing is sent until a method is called."""

        self._session = session
        self._cookie = cookie
        self._utcnow = utcnow

    def __repr__(self) -> str:
        """Never show the cookie."""

        return "PortalTokenClient()"

    async def create_token(self, name: str, expires_at: datetime) -> CreatedToken:
        """``POST portal-session/token`` once; return the validated token."""

        body = {"name": name, "expiresAt": format_expires_at(expires_at)}
        status, content_type, raw = await self._send("POST", PATH_TOKEN, body)
        if status not in _OK_CREATE:
            raise ApiChangedError(f"HTTP {status}", status=status)
        if content_type not in _JSON_TYPES or not raw.strip():
            raise ApiChangedError("token response is not JSON")
        try:
            data = json.loads(raw)
        except ValueError as err:
            raise ApiChangedError("token response is not JSON") from err
        return parse_created_token(data, self._utcnow())

    async def revoke_token(self, token_id: str) -> None:
        """``DELETE portal-session/token/{id}`` once (404: already gone)."""

        token_id = str(token_id)
        if not TOKEN_ID_RE.fullmatch(token_id):
            raise ValueError("token id has an unexpected format")
        status, _, _ = await self._send(
            "DELETE", PATH_TOKEN_ID, None, token_id=token_id
        )
        if status not in _OK_REVOKE:
            raise ApiChangedError(f"HTTP {status}", status=status)

    async def _send(
        self,
        method: str,
        template: str,
        body: dict[str, Any] | None,
        **path_args: str,
    ) -> tuple[int, str, bytes]:
        if (method, template) not in TOKEN_WHITELIST:
            raise NotWhitelistedError("token request is not whitelisted")
        url = URL(BASE_URL + template.format(**path_args))
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Cookie": f"{COOKIE_NAME}={self._cookie}",
        }
        kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            "allow_redirects": False,
        }
        if body is not None:
            kwargs["json"] = body
        send = self._session.post if method == "POST" else self._session.delete
        try:
            async with send(url, **kwargs) as resp:
                status = resp.status
                content_type = resp.content_type
                retry_after = resp.headers.get("Retry-After")
                raw = await resp.read()
        except TimeoutError as err:
            raise PortalUnavailableError("request timed out") from err
        except aiohttp.ClientError as err:
            raise PortalUnavailableError(
                f"connection error ({type(err).__name__})"
            ) from err
        _LOGGER.debug("%s %s -> %s", method, template, status)
        if status >= 300 and not (method == "DELETE" and status == 404):
            raise_for_status(
                status,
                raw,
                retry_after,
                lambda t: redact_text(t, set(path_args.values())),
            )
        return status, content_type, raw
