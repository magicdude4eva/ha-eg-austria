"""Tests for the read-only API client (spec 07 cases 1-8).

The aiohttp session is replaced by ``FakePortal``; no request leaves the process.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import inspect
import json
import logging
from pathlib import Path
import re
import secrets
from types import SimpleNamespace

import aiohttp
import jwt
import pytest

from custom_components.eg_austria import api as api_module
from custom_components.eg_austria.api import (
    BASE_URL,
    PATH_ANALYSIS,
    PATH_DAY_COMPLETENESS,
    PATH_METERING_POINT,
    USER_AGENT,
    WHITELIST,
    EgAustriaClient,
    get_portal_session,
)
from custom_components.eg_austria.const import VERSION
from custom_components.eg_austria.exceptions import (
    ApiChangedError,
    AuthError,
    ForbiddenError,
    NotWhitelistedError,
    PortalUnavailableError,
    RateLimitedError,
    SchemaError,
    StaleClientError,
    TokenExpiredError,
)
from custom_components.eg_austria.models import Aggregation, Direction
from custom_components.eg_austria.redact import REDACTED, redact_text
from custom_components.eg_austria.token import TokenFormatError, TokenKind, jwt_expiry

from .conftest import FakeClock, FakePortal, fixture_bytes

MEMBER = "9000001"
BEG, EEG = 2001, 2002
MP_C, MP_G = 1001, 1002
JSON = "application/json"


def personal_token() -> str:
    """Build a synthetic personal token (never a real one)."""

    return "ecapi_" + secrets.token_hex(32)


def session_jwt(exp: datetime, **claims: object) -> str:
    """Build a JWT signed with a key generated for this test."""

    payload = {"exp": int(exp.timestamp()), "iat": int(exp.timestamp()) - 86400}
    payload.update(claims)
    return jwt.encode(payload, secrets.token_hex(32), algorithm="HS256")


@pytest.fixture
def token() -> str:
    """Return a fresh personal token per test."""

    return personal_token()


@pytest.fixture
def client(mock_portal: FakePortal, token: str, clock: FakeClock) -> EgAustriaClient:
    """Client with a personal token and fake time."""

    return EgAustriaClient(
        mock_portal,
        token,
        TokenKind.PERSONAL,
        token_exp=int((clock.now + timedelta(days=300)).timestamp()),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utcnow=clock.utcnow,
    )


def requests_made(mocked: FakePortal) -> list[tuple[str, str, dict]]:
    """Return (method, url, kwargs) of every request, in order."""

    return mocked.calls


def add(mocked: FakePortal, **kw) -> None:
    """Queue one response."""

    mocked.add(**kw)


# ---------------------------------------------------------------------------
# 1. URLs with repeated array keys


async def test_analysis_url_exact(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """Arrays are repeated keys in a fixed order; both dates inclusive."""

    add(mock_portal, body=fixture_bytes("analysis_hour_normal_beg.json"))
    data = await client.get_analysis(
        MEMBER,
        Aggregation.HOUR,
        date(2026, 6, 1),
        date(2026, 6, 7),
        [BEG, EEG],
        [MP_C, MP_G],
    )
    ((method, url, _),) = requests_made(mock_portal)
    assert method == "GET"
    assert url == (
        f"{BASE_URL}/v1/portal/member/{MEMBER}/analysis-data?aggregation=Hour"
        "&startDate=2026-06-01&endDate=2026-06-07"
        f"&energyCommunityIds={BEG}&energyCommunityIds={EEG}&mpIds={MP_C}&mpIds={MP_G}"
    )
    assert len(data.consumption) == 7 * 24


async def test_other_endpoint_urls(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """Every endpoint hits exactly its documented path."""

    add(mock_portal, body=fixture_bytes("session_one_member.json"))
    add(mock_portal, body=fixture_bytes("metering_point_single.json"))
    add(mock_portal, body=fixture_bytes("day_completeness_normal.json"))
    add(mock_portal, body=b"", content_type="")
    add(mock_portal, body=fixture_bytes("version_a.json"))
    await client.get_session()
    await client.get_metering_points(MEMBER)
    await client.get_day_completeness(
        MEMBER, date(2026, 6, 1), date(2026, 6, 7), [BEG], [MP_C, MP_G]
    )
    assert await client.get_maintenance() == []
    await client.get_version()
    urls = [u for _, u, _ in requests_made(mock_portal)]
    assert urls == [
        f"{BASE_URL}/v1/portal/portal-session",
        f"{BASE_URL}/v1/portal/member/{MEMBER}/metering-point",
        f"{BASE_URL}/v1/portal/member/{MEMBER}/analysis-data/day-completeness"
        f"?startDate=2026-06-01&endDate=2026-06-07&energyCommunityIds={BEG}"
        f"&mpIds={MP_C}&mpIds={MP_G}",
        f"{BASE_URL}/v1/portal/maintenance",
        f"{BASE_URL}/v1/version",
    ]
    assert client.request_count == 5


async def test_day_completeness_chunked_to_13_months(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """A 2-year range is split into 13-month requests, results concatenated."""

    for _ in range(2):
        add(mock_portal, body=fixture_bytes("day_completeness_normal.json"))
    result = await client.get_day_completeness(
        MEMBER, date(2026, 1, 1), date(2027, 6, 30), [BEG], [MP_C]
    )
    urls = [u for _, u, _ in requests_made(mock_portal)]
    assert "startDate=2026-01-01&endDate=2027-01-31" in urls[0]
    assert "startDate=2027-02-01&endDate=2027-06-30" in urls[1]
    assert len(result) == 14


async def test_schema_fingerprints_kept(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """The last analysis-data / day-completeness shapes are kept, no values."""

    assert client.schema_fingerprints == {}
    add(mock_portal, body=fixture_bytes("analysis_hour_normal_beg.json"))
    add(mock_portal, body=fixture_bytes("day_completeness_normal.json"))
    add(mock_portal, body=fixture_bytes("version_a.json"))
    await client.get_analysis(
        MEMBER, Aggregation.HOUR, date(2026, 6, 1), date(2026, 6, 7), [BEG], [MP_C]
    )
    await client.get_day_completeness(
        MEMBER, date(2026, 6, 1), date(2026, 6, 7), [BEG], [MP_C]
    )
    await client.get_version()
    prints = client.schema_fingerprints
    assert set(prints) == {"analysis-data", "day-completeness"}
    point = prints["analysis-data"]["consumptionDataPoints"][0]
    assert point["consumption"] in {"float", "int"}
    assert point["dateTimeFrom"] == "str"
    assert len(prints["analysis-data"]["consumptionDataPoints"]) == 1
    assert isinstance(prints["day-completeness"], list)


async def test_raw_sink_gets_every_response(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """Issue #84: the debug export sees template, params and raw body."""

    seen: list[tuple[str, list[tuple[str, str]], object]] = []
    client.raw_sink = lambda t, p, r: seen.append((t, p, r))
    add(mock_portal, body=fixture_bytes("analysis_hour_normal_beg.json"))
    add(mock_portal, body=fixture_bytes("version_a.json"))
    await client.get_analysis(
        MEMBER, Aggregation.HOUR, date(2026, 6, 1), date(2026, 6, 7), [BEG], [MP_C]
    )
    await client.get_version()
    assert [t for t, _, _ in seen] == [
        "/v1/portal/member/{member}/analysis-data",
        "/v1/version",
    ]
    assert ("energyCommunityIds", str(BEG)) in seen[0][1]
    assert isinstance(seen[0][2], dict)
    assert "consumptionDataPoints" in seen[0][2]
    client.raw_sink = None


@pytest.mark.parametrize(
    ("member", "communities", "mps", "error"),
    [
        ("90000/../x", [BEG], [MP_C], ValueError),
        (MEMBER, [], [MP_C], ValueError),
        (MEMBER, [BEG], [], ValueError),
        (MEMBER, ["2001"], [MP_C], TypeError),
    ],
)
async def test_invalid_arguments_rejected(
    client: EgAustriaClient,
    mock_portal: FakePortal,
    member: str,
    communities: list,
    mps: list,
    error: type[Exception],
) -> None:
    """Bad member numbers and missing ids fail before any request."""

    with pytest.raises(error):
        await client.get_analysis(
            member,
            Aggregation.HOUR,
            date(2026, 6, 1),
            date(2026, 6, 1),
            communities,
            mps,
        )
    assert requests_made(mock_portal) == []


# ---------------------------------------------------------------------------
# 2. Auth headers


async def test_personal_token_header_only(
    client: EgAustriaClient, token: str, mock_portal: FakePortal
) -> None:
    """Personal token: Bearer header, no cookie; Set-Cookie is ignored."""

    for _ in range(2):
        add(
            mock_portal,
            body=fixture_bytes("version_a.json"),
            headers={"Set-Cookie": "portal.jwtToken=; Max-Age=0; Path=/"},
        )
    await client.get_version()
    await client.get_version()
    for _, _, kwargs in requests_made(mock_portal):
        headers = kwargs["headers"]
        assert headers["Authorization"] == f"Bearer {token}"
        assert "Cookie" not in headers
        assert headers["Accept"] == JSON
        assert headers["User-Agent"] == USER_AGENT
        assert kwargs["allow_redirects"] is False


async def test_session_jwt_cookie_only(
    mock_portal: FakePortal, clock: FakeClock
) -> None:
    """Session JWT: cookie header, no Bearer; second call sends the same token."""

    value = session_jwt(clock.now + timedelta(days=20))
    client = EgAustriaClient(
        mock_portal,
        value,
        TokenKind.SESSION,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utcnow=clock.utcnow,
    )
    assert client.token_exp == int((clock.now + timedelta(days=20)).timestamp())
    for _ in range(2):
        add(
            mock_portal,
            body=fixture_bytes("version_a.json"),
            headers={"Set-Cookie": "portal.jwtToken=; Max-Age=0; Path=/"},
        )
    await client.get_version()
    await client.get_version()
    calls = requests_made(mock_portal)
    assert len(calls) == 2
    for _, _, kwargs in calls:
        assert kwargs["headers"]["Cookie"] == f"portal.jwtToken={value}"
        assert "Authorization" not in kwargs["headers"]
    assert value not in repr(client)


async def test_expired_token_sends_nothing(
    mock_portal: FakePortal, clock: FakeClock
) -> None:
    """Past the stored expiry the client raises before any request."""

    value = session_jwt(clock.now - timedelta(seconds=1))
    client = EgAustriaClient(mock_portal, value, TokenKind.SESSION, utcnow=clock.utcnow)
    with pytest.raises(TokenExpiredError):
        await client.get_version()
    assert isinstance(TokenExpiredError("x"), AuthError)
    assert requests_made(mock_portal) == []


async def test_portal_session_has_no_cookie_jar(hass) -> None:
    """The HA-created session ignores Set-Cookie (DummyCookieJar)."""

    session = get_portal_session(hass)
    assert isinstance(session.cookie_jar, aiohttp.DummyCookieJar)


async def test_portal_session_shared_and_never_closed(hass) -> None:
    """One session per HA instance, reused by entries and flows (issue #17).

    Home Assistant owns sessions from ``async_create_clientsession``; a
    closed one (only after HA closed it) is replaced.
    """

    first = get_portal_session(hass)
    assert get_portal_session(hass) is first
    # Closing it here would trip HA's frame check; stand in a closed one.
    hass.data[api_module._SESSION_KEY] = SimpleNamespace(closed=True)
    second = get_portal_session(hass)
    assert second is not first
    assert not second.closed
    assert get_portal_session(hass) is second


def test_user_agent_has_no_personal_data() -> None:
    """User-Agent is name, version and repo only; version matches the manifest."""

    manifest = json.loads(
        (
            Path(__file__).parents[1] / "custom_components/eg_austria/manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["version"] == VERSION

    assert (
        f"ha-eg-austria/{VERSION} (+https://github.com/magicdude4eva/ha-eg-austria)"
    ) == USER_AGENT


# ---------------------------------------------------------------------------
# 3. Whitelist


def test_whitelist_is_get_only_and_exact() -> None:
    """The whitelist holds the six phase-1 GET paths plus transactions (#74)."""

    assert {m for m, _ in WHITELIST} == {"GET"}
    assert {p for _, p in WHITELIST} == {
        "/v1/portal/member/{member}/metering-point",
        "/v1/portal/member/{member}/analysis-data",
        "/v1/portal/member/{member}/analysis-data/day-completeness",
        "/v1/portal/maintenance",
        "/v1/version",
        "/v1/portal/portal-session",
        "/v1/portal/member/{member}/billing/transactions",
    }
    assert isinstance(WHITELIST, frozenset)


def test_no_non_get_reachable() -> None:
    """No public method or code path can send another verb."""

    public = {
        name
        for name, _ in inspect.getmembers(EgAustriaClient, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public == {
        "get_session",
        "get_metering_points",
        "get_analysis",
        "get_day_completeness",
        "get_transactions",
        "get_maintenance",
        "get_version",
        "maintenance_active",
    }
    source = inspect.getsource(api_module)
    for verb in ("post", "put", "patch", "delete", "request", "head", "options"):
        assert not re.search(rf"\.{verb}\(", source, re.IGNORECASE), verb
    assert '"POST"' not in source
    assert '"DELETE"' not in source


@pytest.mark.parametrize(
    "template",
    [
        "/v1/portal/portal-session/token",
        "/v1/portal/member/{member}/billing/pending-invoices",
        "/v1/portal/member/{member}/billing/transactions/1/download",
        "/v1/portal/member/{member}/metering-point/",
        "/v1/portal/member/{member}/part-factor-changes",
    ],
)
async def test_get_rejects_unlisted_template(
    client: EgAustriaClient, mock_portal: FakePortal, template: str
) -> None:
    """``_get`` refuses anything outside the whitelist before sending."""

    with pytest.raises(NotWhitelistedError):
        await client._get(template, member=MEMBER)
    assert requests_made(mock_portal) == []


async def test_member_argument_only_for_member_paths(client: EgAustriaClient) -> None:
    """A member number is required exactly where the template has one."""

    with pytest.raises(ValueError, match="member"):
        await client._get(PATH_METERING_POINT)
    with pytest.raises(ValueError, match="member"):
        await client._get("/v1/version", member=MEMBER)


# ---------------------------------------------------------------------------
# 4. Error mapping


@pytest.mark.parametrize(
    ("status", "body", "content_type", "error"),
    [
        (
            400,
            "error_400_stale_client.json",
            "application/problem+json",
            StaleClientError,
        ),
        (400, "error_400_other.json", "application/problem+json", ApiChangedError),
        (401, "error_401.json", "application/problem+json", AuthError),
        (403, None, JSON, ForbiddenError),
        (404, None, JSON, ApiChangedError),
        (405, None, JSON, ApiChangedError),
        (422, None, JSON, ApiChangedError),
        (302, None, "text/html", ApiChangedError),
        (500, None, JSON, PortalUnavailableError),
    ],
)
async def test_status_mapping(
    client: EgAustriaClient,
    mock_portal: FakePortal,
    status: int,
    body: str | None,
    content_type: str,
    error: type[Exception],
) -> None:
    """HTTP status codes map to the typed errors of spec 01."""

    add(
        mock_portal,
        status=status,
        body=fixture_bytes(body) if body else b"{}",
        content_type=content_type,
    )
    with pytest.raises(error) as excinfo:
        await client.get_metering_points(MEMBER)
    assert type(excinfo.value) is error
    assert MEMBER not in str(excinfo.value)
    if isinstance(excinfo.value, ApiChangedError):
        assert excinfo.value.status == status


async def test_stale_client_is_api_changed(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """StaleClientError is an ApiChangedError (same repair, finer type)."""

    add(mock_portal, status=400, body=fixture_bytes("error_400_stale_client.json"))
    with pytest.raises(ApiChangedError, match="aktualisiert"):
        await client.get_version()


async def test_403_with_auth_body_is_auth_error(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """403 whose body is about the token → AuthError (reauth)."""

    add(mock_portal, status=403, body=b'{"message": "Invalid token"}')
    with pytest.raises(AuthError) as excinfo:
        await client.get_version()
    assert type(excinfo.value) is AuthError


async def test_429_retry_after(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """429 → RateLimitedError with Retry-After seconds; no retry."""

    add(mock_portal, status=429, body=b"{}", headers={"Retry-After": "120"})
    with pytest.raises(RateLimitedError) as excinfo:
        await client.get_version()
    assert excinfo.value.retry_after == 120
    assert client.request_count == 1


async def test_429_without_retry_after(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """A missing or odd Retry-After is None."""

    add(mock_portal, status=429, body=b"", headers={"Retry-After": "soon"})
    with pytest.raises(RateLimitedError) as excinfo:
        await client.get_version()
    assert excinfo.value.retry_after is None


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"<html><body>Wartungsarbeiten</body></html>", "text/html"),
        (b"{not json", JSON),
        (b"", JSON),
    ],
)
async def test_bad_body_without_maintenance_is_api_changed(
    client: EgAustriaClient, mock_portal: FakePortal, body: bytes, content_type: str
) -> None:
    """HTML, invalid JSON or an empty body outside maintenance → ApiChangedError."""

    add(mock_portal, body=body, content_type=content_type)
    with pytest.raises(ApiChangedError):
        await client.get_version()


async def test_bad_body_during_maintenance_is_unavailable(
    client: EgAustriaClient, mock_portal: FakePortal, clock: FakeClock
) -> None:
    """During an announced window an HTML page means the portal is down."""

    clock.now = datetime(2026, 6, 8, 2, 0, tzinfo=UTC)
    add(mock_portal, body=fixture_bytes("maintenance_active.json"))
    add(mock_portal, body=b"<html></html>", content_type="text/html")
    windows = await client.get_maintenance()
    assert client.maintenance_active()
    assert windows[0].start == datetime(2026, 6, 8, 1, 0, tzinfo=UTC)
    with pytest.raises(PortalUnavailableError):
        await client.get_version()


async def test_schema_error_from_parser(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """A wrong shape surfaces as SchemaError with the key path."""

    add(mock_portal, body=fixture_bytes("analysis_bad_s2_missing_key.json"))
    with pytest.raises(SchemaError) as excinfo:
        await client.get_analysis(
            MEMBER, Aggregation.HOUR, date(2026, 6, 3), date(2026, 6, 3), [BEG], [MP_C]
        )
    assert excinfo.value.rule == "S2"
    assert excinfo.value.path == "consumptionDataPoints[0].consumptionFaulty"


# ---------------------------------------------------------------------------
# 5. Retries


@pytest.mark.parametrize("first", ["timeout", "connection", 502, 503, 504])
async def test_one_retry_then_success(
    client: EgAustriaClient, mock_portal: FakePortal, clock: FakeClock, first
) -> None:
    """Timeout, connection error and 502-504 are retried once after 30 s."""

    if first == "timeout":
        mock_portal.add(exception=TimeoutError())
    elif first == "connection":
        mock_portal.add(exception=aiohttp.ClientConnectionError())
    else:
        add(mock_portal, status=first, body=b"")
    add(mock_portal, body=fixture_bytes("version_b.json"))
    version = await client.get_version()
    assert version.git_commit == "b" * 40
    assert client.request_count == 2
    assert 30 in clock.sleeps


async def test_retry_only_once(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """Two timeouts in a row → PortalUnavailableError after one retry."""

    mock_portal.add(exception=TimeoutError())
    mock_portal.add(exception=TimeoutError())
    with pytest.raises(PortalUnavailableError, match="timed out"):
        await client.get_version()
    assert client.request_count == 2


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500])
async def test_no_retry_on_4xx_and_500(
    client: EgAustriaClient, mock_portal: FakePortal, clock: FakeClock, status: int
) -> None:
    """4xx (and plain 500) are not retried."""

    add(mock_portal, status=status, body=b"{}")
    add(mock_portal, body=fixture_bytes("version_a.json"))
    with pytest.raises(Exception):  # noqa: B017, PT011
        await client.get_version()
    assert client.request_count == 1
    assert 30 not in clock.sleeps


# ---------------------------------------------------------------------------
# 6. Spacing


async def test_request_spacing(
    client: EgAustriaClient, mock_portal: FakePortal, clock: FakeClock
) -> None:
    """Request starts are at least min_interval apart; no wait after a pause."""

    for _ in range(3):
        add(mock_portal, body=fixture_bytes("version_a.json"))
    await client.get_version()
    clock.advance(0.5)
    await client.get_version()
    assert clock.sleeps == [pytest.approx(1.5)]
    clock.advance(10)
    await client.get_version()
    assert clock.sleeps == [pytest.approx(1.5)]


async def test_spacing_can_be_doubled(
    client: EgAustriaClient, mock_portal: FakePortal, clock: FakeClock
) -> None:
    """The coordinator doubles spacing after a 429 via ``min_interval``."""

    client.min_interval = 4.0
    for _ in range(2):
        add(mock_portal, body=fixture_bytes("version_a.json"))
    await client.get_version()
    await client.get_version()
    assert clock.sleeps == [pytest.approx(4.0)]


# ---------------------------------------------------------------------------
# 7. Range limit


@pytest.mark.parametrize("aggregation", [Aggregation.HOUR, Aggregation.FIFTEEN_MINUTES])
async def test_fine_range_limit(
    client: EgAustriaClient, mock_portal: FakePortal, aggregation: Aggregation
) -> None:
    """32 local days at Hour raise before any request; 31 are allowed."""

    with pytest.raises(ValueError, match="31 days"):
        await client.get_analysis(
            MEMBER, aggregation, date(2026, 5, 1), date(2026, 6, 1), [BEG], [MP_C]
        )
    with pytest.raises(ValueError, match="before"):
        await client.get_analysis(
            MEMBER, aggregation, date(2026, 5, 2), date(2026, 5, 1), [BEG], [MP_C]
        )
    assert requests_made(mock_portal) == []
    add(mock_portal, body=fixture_bytes("analysis_hour_tail_incomplete.json"))
    data = await client.get_analysis(
        MEMBER, aggregation, date(2026, 5, 1), date(2026, 5, 31), [BEG], [MP_C]
    )
    assert len(data.points(Direction.CONSUMPTION)) == 31 * 24


async def test_day_aggregation_has_no_31_day_limit(
    client: EgAustriaClient, mock_portal: FakePortal
) -> None:
    """Day aggregation is not limited by the Hour rule."""

    add(mock_portal, body=fixture_bytes("analysis_day_dst_autumn.json"))
    await client.get_analysis(
        MEMBER, Aggregation.DAY, date(2026, 1, 1), date(2026, 12, 31), [BEG], [MP_C]
    )


# ---------------------------------------------------------------------------
# 8. Logs never contain secrets


async def test_logs_never_contain_secrets(
    client: EgAustriaClient,
    token: str,
    mock_portal: FakePortal,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Debug logs show templates and redacted messages only."""

    caplog.set_level(logging.DEBUG, logger="custom_components.eg_austria")
    add(mock_portal, body=fixture_bytes("metering_point_single.json"))
    add(mock_portal, status=503, body=b"")
    add(mock_portal, status=400, body=fixture_bytes("error_400_other.json"))
    add(mock_portal, status=401, body=fixture_bytes("error_401.json"))
    await client.get_metering_points(MEMBER)
    with pytest.raises(ApiChangedError) as err_400:
        await client.get_analysis(
            MEMBER,
            Aggregation.HOUR,
            date(2026, 6, 1),
            date(2026, 6, 1),
            [BEG],
            [MP_C, MP_G],
        )
    with pytest.raises(AuthError) as err_401:
        await client.get_day_completeness(
            MEMBER, date(2026, 6, 1), date(2026, 6, 1), [BEG], [MP_C]
        )
    text = caplog.text + str(err_400.value) + str(err_401.value)
    assert PATH_ANALYSIS in caplog.text
    assert PATH_DAY_COMPLETENESS in caplog.text
    for secret in (token, MEMBER, str(BEG), str(MP_C), str(MP_G), "AT0000", "Example"):
        assert secret not in text, secret
    assert "traceId" not in text


# ---------------------------------------------------------------------------
# Helpers: token expiry and redaction


def test_jwt_expiry_reads_exp_only() -> None:
    """``exp`` is read without a signature check."""

    exp = datetime(2026, 7, 1, tzinfo=UTC)
    assert jwt_expiry(session_jwt(exp, email="member@example.invalid")) == int(
        exp.timestamp()
    )


@pytest.mark.parametrize(
    "value",
    ["not-a-jwt", "a.b", "a.!!!.c", "eyJ.e30.c", personal_token()],
    # Fixed ids: the random token must not differ between pytest-xdist workers.
    ids=["no-dots", "two-parts", "not-json", "no-exp", "personal-token"],
)
def test_jwt_expiry_rejects_bad_tokens(value: str) -> None:
    """Non-JWTs, bad payloads and missing ``exp`` raise TokenFormatError."""

    with pytest.raises(TokenFormatError) as excinfo:
        jwt_expiry(value)
    assert value not in str(excinfo.value)


def test_session_client_requires_jwt(mock_portal: FakePortal) -> None:
    """A session-kind client with a non-JWT fails at construction."""

    with pytest.raises(TokenFormatError):
        EgAustriaClient(mock_portal, personal_token(), TokenKind.SESSION)


def test_redact_text() -> None:
    """Patterns and configured ids are replaced; partial numbers are kept."""

    tok = session_jwt(datetime(2026, 7, 1, tzinfo=UTC))
    pat = personal_token()
    text = (
        f"t={tok} p={pat} mp=AT{'0' * 27}1001 mail=member@example.invalid "
        f"member {MEMBER} ids {BEG},{MP_C} other 11001"
    )
    out = redact_text(text, [MEMBER, BEG, MP_C, ""])
    for secret in (tok, pat, "AT000", "member@", MEMBER, str(BEG), f",{MP_C}"):
        assert secret not in out
    assert "11001" in out
    assert out.count(REDACTED) == 7
