"""Token module tests (spec 01 "Token module", test plan case 8a, ADR 0007).

Nothing touches the network: a session double with ``post`` and ``delete``
only. Tokens and cookies are generated at run time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import re
import secrets
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from multidict import CIMultiDict
import pytest

from custom_components.eg_austria import token_api
from custom_components.eg_austria.exceptions import (
    ApiChangedError,
    AuthError,
    ForbiddenError,
    NotWhitelistedError,
    PortalUnavailableError,
    SchemaError,
)
from custom_components.eg_austria.token_api import (
    PATH_TOKEN,
    PATH_TOKEN_ID,
    TOKEN_WHITELIST,
    CreatedToken,
    PortalTokenClient,
    format_expires_at,
    parse_created_token,
)

from .conftest import FakeResponse, _Ctx

PACKAGE = Path(__file__).parent.parent / "custom_components" / "eg_austria"
NOW = datetime(2026, 6, 8, 4, 0, tzinfo=UTC)
COOKIE = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjF9." + secrets.token_hex(8)


def new_token() -> str:
    """Return a random synthetic personal token."""
    return "ecapi_" + secrets.token_hex(24)


class FakeTokenPortal:
    """Session double: queued responses, recorded ``post``/``delete`` calls."""

    def __init__(self) -> None:
        """Start with an empty queue."""
        self.queue: list[FakeResponse | BaseException] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def add(
        self,
        status: int = 201,
        body: Any = None,
        content_type: str = "application/json",
        exception: BaseException | None = None,
    ) -> None:
        """Queue one response (``body`` as JSON unless bytes)."""
        if exception is not None:
            self.queue.append(exception)
            return
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.queue.append(FakeResponse(status, raw, content_type, CIMultiDict()))

    def _record(self, verb: str, url: object, kwargs: dict[str, Any]) -> _Ctx:
        self.calls.append((verb, str(url), kwargs))
        if not self.queue:
            raise AssertionError("unexpected request")
        return _Ctx(self.queue.pop(0))

    def post(self, url: object, **kwargs: Any) -> _Ctx:
        """Record a POST."""
        return self._record("POST", url, kwargs)

    def delete(self, url: object, **kwargs: Any) -> _Ctx:
        """Record a DELETE."""
        return self._record("DELETE", url, kwargs)


def body(**overrides: Any) -> dict[str, Any]:
    """Return a create response like the portal's (synthetic)."""
    data: dict[str, Any] = {
        "id": 11,
        "token": new_token(),
        "name": "Home Assistant",
        "expiresAt": "2027-06-08T04:00:00.000Z",
        "revokedAt": None,
        "lastUsedAt": None,
        "createdAt": "2026-06-08T04:00:00.000Z",
    }
    data.update(overrides)
    return data


@pytest.fixture
def portal() -> FakeTokenPortal:
    """Fake session."""
    return FakeTokenPortal()


def client(portal: FakeTokenPortal) -> PortalTokenClient:
    """Return a token client with the fixed clock."""
    return PortalTokenClient(portal, COOKIE, utcnow=lambda: NOW)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Whitelist and import boundary


def test_token_whitelist_is_exact() -> None:
    """One POST and one DELETE, both on the token path, nothing else."""

    assert {
        ("POST", "/v1/portal/portal-session/token"),
        ("DELETE", "/v1/portal/portal-session/token/{token_id}"),
    } == TOKEN_WHITELIST
    assert isinstance(TOKEN_WHITELIST, frozenset)


def test_only_config_flow_imports_token_module() -> None:
    """Runs (coordinator, services, setup) can't reach the write calls."""

    importers = sorted(
        path.name
        for path in PACKAGE.glob("*.py")
        if path.name != "token_api.py"
        and re.search(
            r"^\s*(from \.token_api |import .*token_api)", path.read_text(), re.M
        )
    )
    assert importers == ["config_flow.py"]


def test_token_module_sends_no_other_verb() -> None:
    """Only ``.post(`` and ``.delete(`` on the session appear in the module."""

    source = (PACKAGE / "token_api.py").read_text()
    for verb in ("get", "put", "patch", "request", "head", "options"):
        assert not re.search(rf"_session\.{verb}\b", source), verb


async def test_unlisted_template_refused(portal: FakeTokenPortal) -> None:
    """``_send`` checks the whitelist before anything is sent."""

    with pytest.raises(NotWhitelistedError):
        await client(portal)._send("POST", PATH_TOKEN_ID, None, token_id="1")
    with pytest.raises(NotWhitelistedError):
        await client(portal)._send("GET", PATH_TOKEN, None)
    assert not portal.calls


# ---------------------------------------------------------------------------
# Create


async def test_create_sends_one_post_with_cookie(portal: FakeTokenPortal) -> None:
    """Cookie header, JSON body ``{name, expiresAt}``, no redirects."""

    raw = body()
    portal.add(201, raw)
    expires = NOW + timedelta(days=365)
    created = await client(portal).create_token("Home Assistant", expires)
    assert created == CreatedToken(
        id="11",
        token=raw["token"],
        expires_at=datetime(2027, 6, 8, 4, 0, tzinfo=UTC),
    )
    ((verb, url, kwargs),) = portal.calls
    assert verb == "POST"
    assert url == "https://portal.eg-austria.at/api/v1/portal/portal-session/token"
    assert kwargs["json"] == {
        "name": "Home Assistant",
        "expiresAt": "2027-06-08T04:00:00.000Z",
    }
    headers = kwargs["headers"]
    assert headers["Cookie"] == f"portal.jwtToken={COOKIE}"
    assert "Authorization" not in headers
    assert kwargs["allow_redirects"] is False


async def test_create_accepts_200_and_string_id(portal: FakeTokenPortal) -> None:
    """200 works as well as 201; a string id is kept as text."""

    portal.add(200, body(id="abc-1"), content_type="application/problem+json")
    created = await client(portal).create_token("x", NOW + timedelta(days=1))
    assert created.id == "abc-1"


@pytest.mark.parametrize(
    "exception",
    [TimeoutError(), aiohttp.ClientConnectionError("down")],
)
async def test_create_no_retry(portal: FakeTokenPortal, exception: Exception) -> None:
    """A timeout or connection error is not repeated (token may exist)."""

    portal.add(exception=exception)
    with pytest.raises(PortalUnavailableError):
        await client(portal).create_token("x", NOW + timedelta(days=1))
    assert len(portal.calls) == 1


@pytest.mark.parametrize(
    ("status", "payload", "error"),
    [
        (401, {"message": "Invalid portal session"}, AuthError),
        (403, {"message": "Access denied: No energy communities"}, ForbiddenError),
        (404, {"message": "Not Found"}, ApiChangedError),
        (422, {"message": "expiresAt must be a date"}, ApiChangedError),
        (503, {}, PortalUnavailableError),
        (204, b"", ApiChangedError),
    ],
)
async def test_create_status_mapping(
    portal: FakeTokenPortal, status: int, payload: Any, error: type[Exception]
) -> None:
    """Errors map like the read client; 503 is not retried."""

    portal.add(status, payload)
    with pytest.raises(error):
        await client(portal).create_token("x", NOW + timedelta(days=1))
    assert len(portal.calls) == 1


@pytest.mark.parametrize(
    ("raw", "content_type"),
    [
        (b"<html>maintenance</html>", "text/html"),
        (b"", "application/json"),
        (b"{not json", "application/json"),
    ],
)
async def test_create_not_json(
    portal: FakeTokenPortal, raw: bytes, content_type: str
) -> None:
    """A non-JSON success body is an API change."""

    portal.add(201, raw, content_type=content_type)
    with pytest.raises(ApiChangedError):
        await client(portal).create_token("x", NOW + timedelta(days=1))


@pytest.mark.parametrize(
    "raw",
    [
        [],
        body(token=None),
        body(token="ecapi_UPPER"),
        body(token="eyJ.not.personal"),
        body(id=True),
        body(id=None),
        body(id="bad id/.."),
        body(id=1.5),
        body(expiresAt=None),
        body(expiresAt="next year"),
        body(expiresAt="2027-06-08T04:00:00"),
        body(expiresAt="2026-06-01T00:00:00.000Z"),
        body(revokedAt="2026-06-08T04:00:00.000Z"),
    ],
)
def test_create_response_validation(raw: Any) -> None:
    """Every malformed response is a schema error, without the token in it."""

    with pytest.raises(SchemaError) as err:
        parse_created_token(raw, NOW)
    if isinstance(raw, dict) and isinstance(raw.get("token"), str):
        assert raw["token"] not in str(err.value)


async def test_created_token_never_logged(
    portal: FakeTokenPortal, caplog: pytest.LogCaptureFixture
) -> None:
    """The success body and the cookie stay out of logs and ``repr``."""

    caplog.set_level(logging.DEBUG)
    raw = body()
    portal.add(201, raw)
    c = client(portal)
    created = await c.create_token("x", NOW + timedelta(days=1))
    assert raw["token"] not in caplog.text
    assert COOKIE not in caplog.text
    assert raw["token"] not in repr(created)
    assert COOKIE not in repr(c)


async def test_error_body_redacted(
    portal: FakeTokenPortal, caplog: pytest.LogCaptureFixture
) -> None:
    """An error body echoing a token is redacted in the message and log."""

    caplog.set_level(logging.DEBUG)
    leaked = new_token()
    portal.add(400, {"message": f"bad token {leaked}"})
    with pytest.raises(ApiChangedError) as err:
        await client(portal).create_token("x", NOW + timedelta(days=1))
    assert leaked not in str(err.value)
    assert leaked not in caplog.text


def test_format_expires_at() -> None:
    """UTC with milliseconds and ``Z``, from any time zone."""

    local = datetime(2027, 1, 1, 1, 0, tzinfo=ZoneInfo("Europe/Vienna"))
    assert format_expires_at(local) == "2027-01-01T00:00:00.000Z"


# ---------------------------------------------------------------------------
# Revoke


@pytest.mark.parametrize("status", [200, 204, 404])
async def test_revoke_sends_one_delete(portal: FakeTokenPortal, status: int) -> None:
    """DELETE on the id path with the cookie; 404 means already gone."""

    portal.add(status, b"", content_type="")
    await client(portal).revoke_token("11")
    ((verb, url, kwargs),) = portal.calls
    assert verb == "DELETE"
    assert url == "https://portal.eg-austria.at/api/v1/portal/portal-session/token/11"
    assert kwargs["headers"]["Cookie"] == f"portal.jwtToken={COOKIE}"
    assert "json" not in kwargs


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, AuthError), (500, PortalUnavailableError), (201, ApiChangedError)],
)
async def test_revoke_errors(
    portal: FakeTokenPortal, status: int, error: type[Exception]
) -> None:
    """Revoke errors map like the read client."""

    portal.add(status, {})
    with pytest.raises(error):
        await client(portal).revoke_token("11")


@pytest.mark.parametrize("token_id", ["", "../token", "1/2", "a" * 65, "1?x=1"])
async def test_revoke_bad_id_sends_nothing(
    portal: FakeTokenPortal, token_id: str
) -> None:
    """An id outside the pattern raises before any request."""

    with pytest.raises(ValueError, match="token id"):
        await client(portal).revoke_token(token_id)
    assert not portal.calls


def test_module_docstring_mentions_adr() -> None:
    """The module names its ADR (guards against silent scope creep)."""

    assert "ADR 0007" in (token_api.__doc__ or "")
