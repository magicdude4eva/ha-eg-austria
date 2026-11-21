"""Config flow tests (spec 02, test plan cases 31-36).

The S1 client is replaced by ``FakeClient``; nothing touches the network.
Tokens are generated at run time (synthetic, random) and never written to a
file.
"""

from __future__ import annotations

import copy
from datetime import timedelta
import json
from pathlib import Path
import re
import secrets
import string
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
import jwt
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eg_austria import config_flow as cf
from custom_components.eg_austria.const import (
    CONF_COMMUNITIES,
    CONF_IMPORT_HISTORY,
    CONF_LOCATION_ID,
    CONF_MAX_HOURLY_KWH,
    CONF_MEMBER_NUMBER,
    CONF_MP_IDS,
    CONF_RETIRED_SLUGS,
    CONF_RUN_MINUTE,
    CONF_SITE_SLUG,
    CONF_TOKEN,
    CONF_TOKEN_EXP,
    CONF_TOKEN_KIND,
    DOMAIN,
)
from custom_components.eg_austria.exceptions import (
    ApiChangedError,
    AuthError,
    ForbiddenError,
    PortalUnavailableError,
    RateLimitedError,
    SchemaError,
)
from custom_components.eg_austria.models import parse_metering_points, parse_session
from custom_components.eg_austria.token import (
    TokenFormatError,
    TokenKind,
    parse_token,
)

from .conftest import load_fixture

MEMBER = "9000001"
COMPONENT = Path(__file__).parent.parent / "custom_components" / DOMAIN


def session_jwt(days: float) -> str:
    """Return a random synthetic session JWT expiring in ``days``."""

    exp = dt_util.utcnow() + timedelta(days=days)
    payload = {"exp": int(exp.timestamp()), "sub": "1", "email": "x@example.invalid"}
    return jwt.encode(payload, secrets.token_hex(32), algorithm="HS256")


def personal_token() -> str:
    """Return a random synthetic personal token."""

    return "ecapi_" + secrets.token_hex(24)


def mp_raw(name: str = "metering_point_single.json") -> list[dict[str, Any]]:
    """Return a deep copy of a synthetic metering-point fixture."""

    return copy.deepcopy(load_fixture(name))


class FakeClient:
    """Stands in for ``EgAustriaClient``; results and errors set per test."""

    session: Any = None
    mp: Any = None
    session_error: Exception | None = None
    mp_error: Exception | None = None
    instances: list[FakeClient] = []  # noqa: RUF012

    def __init__(
        self, session: Any, token: str, kind: TokenKind, *, token_exp: int | None
    ) -> None:
        """Record the constructor arguments."""
        self.token, self.kind, self.token_exp = token, kind, token_exp
        self.calls: list[tuple[str, ...]] = []
        FakeClient.instances.append(self)

    async def get_session(self):
        """Return or raise the configured session result."""
        self.calls.append(("get_session",))
        if FakeClient.session_error is not None:
            raise FakeClient.session_error
        return FakeClient.session

    async def get_metering_points(self, member: str):
        """Return or raise the configured metering-point result."""
        self.calls.append(("get_metering_points", member))
        if FakeClient.mp_error is not None:
            raise FakeClient.mp_error
        return FakeClient.mp


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Prepare the recorder before ``hass`` (manifest dependency since S4)."""


@pytest.fixture(autouse=True)
def recorder(recorder_mock) -> None:
    """Run every flow test with the test recorder (flow init sets up deps)."""


@pytest.fixture(autouse=True)
def fake_client():
    """Patch the client, the session factory and entry setup."""

    FakeClient.session = parse_session(load_fixture("session_one_member.json"))
    FakeClient.mp = parse_metering_points(load_fixture("metering_point_single.json"))
    FakeClient.session_error = None
    FakeClient.mp_error = None
    FakeClient.instances = []
    portal_session = MagicMock()
    portal_session.close = AsyncMock()
    # Flow tests stop at the entry: no setup (which needs the recorder, S4).
    with (
        patch.object(cf, "EgAustriaClient", FakeClient),
        patch.object(cf, "create_portal_session", return_value=portal_session),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_setup",
            AsyncMock(return_value=True),
        ),
        # Reauth and reconfigure reload the entry without going through
        # ConfigEntries.async_setup; keep that reload from setting up for real.
        patch(
            "custom_components.eg_austria.async_setup_entry",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.eg_austria.async_unload_entry",
            AsyncMock(return_value=True),
        ),
    ):
        yield portal_session


async def start(hass: HomeAssistant) -> dict[str, Any]:
    """Start a user flow."""

    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def submit(hass: HomeAssistant, result: dict[str, Any], **data: Any):
    """Submit a step."""

    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


async def run_to_entry(hass: HomeAssistant, token: str | None = None):
    """Walk the default path with a session JWT (30 days left)."""

    result = await submit(hass, await start(hass), token=token or session_jwt(30))
    assert result["step_id"] == "site"
    result = await submit(
        hass, result, metering_points=["1001", "1002"], site_slug="home"
    )
    assert result["step_id"] == "communities"
    return await submit(
        hass, result, include_1=True, slug_1="beg", include_2=True, slug_2="eeg"
    )


def make_entry(hass: HomeAssistant, **overrides: Any) -> MockConfigEntry:
    """Add a stored entry like the flow creates it."""

    data = {
        CONF_TOKEN: session_jwt(20),
        CONF_TOKEN_KIND: "session",
        CONF_TOKEN_EXP: int((dt_util.utcnow() + timedelta(days=20)).timestamp()),
        CONF_MEMBER_NUMBER: MEMBER,
        CONF_LOCATION_ID: "5001",
        CONF_MP_IDS: [1001, 1002],
        CONF_COMMUNITIES: [{"id": 2001, "slug": "beg", "name": "Example BEG"}],
        CONF_RETIRED_SLUGS: [],
        CONF_SITE_SLUG: "home",
        CONF_RUN_MINUTE: 200,
    } | overrides
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=cf.ENTRY_TITLE,
        data=data,
        options=cf.default_options(),
        unique_id=cf.entry_unique_id(MEMBER, data[CONF_LOCATION_ID]),
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Case 31: token parsing


@pytest.mark.parametrize(
    "wrap",
    [
        "{}",
        "  {}\n",
        '"{}"',
        "'{}'",
        ' " {} " ',
    ],
)
def test_parse_personal_token(wrap: str) -> None:
    """Whitespace and quotes are stripped; ``ecapi_`` is a personal token."""

    token = personal_token()
    parsed = parse_token(wrap.format(token))
    assert parsed.token == token
    assert parsed.kind is TokenKind.PERSONAL
    assert parsed.exp is None


@pytest.mark.parametrize(
    "wrap",
    [
        "{}",
        "  {}\t",
        '"{}"',
        "portal.jwtToken={}",
        "portal.jwtToken={}; Path=/; HttpOnly",
        '"portal.jwtToken={}; Path=/"',
        "other=1; portal.jwtToken={}; Path=/",
        'portal.jwtToken="{}"',
    ],
)
def test_parse_session_jwt(wrap: str) -> None:
    """Raw JWT and cookie strings give the JWT and its ``exp``."""

    token = session_jwt(10)
    parsed = parse_token(wrap.format(token))
    assert parsed.token == token
    assert parsed.kind is TokenKind.SESSION
    assert parsed.exp == jwt.decode(token, options={"verify_signature": False})["exp"]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "ecapi_",
        "ecapi_UPPER",
        "ecapi_has-dash",
        "not a token",
        "a.b",
        "a.b.c.d",
        "portal.jwtToken=",
        "abc.!!!.def",
    ],
)
def test_parse_invalid(value: str) -> None:
    """Anything else is a format error, without the value in the message."""

    with pytest.raises(TokenFormatError) as err:
        parse_token(value)
    if value.strip():
        assert value.strip() not in str(err.value)


def test_parse_jwt_without_exp() -> None:
    """A JWT-shaped value whose payload has no ``exp`` is rejected."""

    token = jwt.encode({"sub": "1"}, secrets.token_hex(32), algorithm="HS256")
    with pytest.raises(TokenFormatError):
        parse_token(token)


def test_parsed_token_repr_hides_token() -> None:
    """The token never appears in ``repr``."""

    token = personal_token()
    assert token not in repr(parse_token(token))


# ---------------------------------------------------------------------------
# Case 31/33: happy paths


async def test_user_flow_session_jwt(hass: HomeAssistant, fake_client) -> None:
    """One member, one location: token → site → communities → entry."""

    token = session_jwt(30)
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert "readme_url" in result["description_placeholders"]

    result = await submit(hass, result, token=f"portal.jwtToken={token}; Path=/")
    assert result["step_id"] == "site"
    schema = result["data_schema"].schema
    assert {str(k) for k in schema} == {"metering_points", "site_slug"}

    result = await submit(
        hass, result, metering_points=["1001", "1002"], site_slug="home"
    )
    assert result["step_id"] == "communities"
    placeholder = result["description_placeholders"]["communities"]
    assert "Example BEG" in placeholder
    assert "Example EEG" in placeholder
    assert "2026-01-01" in placeholder
    defaults = {
        str(k): k.default() for k in result["data_schema"].schema if "slug" in str(k)
    }
    assert defaults == {"slug_1": "beg", "slug_2": "eeg"}

    result = await submit(
        hass, result, include_1=True, slug_1="beg", include_2=True, slug_2="eeg"
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "EG Austria Home"
    data = result["data"]
    assert data[CONF_TOKEN] == token
    assert data[CONF_TOKEN_KIND] == "session"
    assert data[CONF_TOKEN_EXP] == FakeClient.instances[0].token_exp
    assert data[CONF_MEMBER_NUMBER] == MEMBER
    assert data[CONF_LOCATION_ID] == "5001"
    assert data[CONF_MP_IDS] == [1001, 1002]
    assert data[CONF_COMMUNITIES] == [
        {"id": 2001, "slug": "beg", "name": "Example BEG"},
        {"id": 2002, "slug": "eeg", "name": "Example EEG"},
    ]
    assert data[CONF_SITE_SLUG] == "home"
    assert data[CONF_RETIRED_SLUGS] == []
    assert 180 <= data[CONF_RUN_MINUTE] <= 359
    assert result["options"] == cf.default_options()
    entry = result["result"]
    assert entry.unique_id == cf.entry_unique_id(MEMBER, "5001")
    assert MEMBER not in entry.unique_id
    # Nothing personal beyond what the spec stores.
    dumped = json.dumps(data)
    for secret in ("Example Member", "Example street", "example.invalid", "AT0000"):
        assert secret not in dumped
    # The short-lived session is closed after each step that used it.
    assert fake_client.close.await_count == 1


async def test_user_flow_personal_token(hass: HomeAssistant) -> None:
    """A personal token asks for its expiry; default is today + 365 days."""

    token = personal_token()
    result = await submit(hass, await start(hass), token=f' "{token}" ')
    assert result["step_id"] == "token_expiry"
    (key,) = result["data_schema"].schema
    default = dt_util.now().date() + timedelta(days=365)
    assert key.default() == default.isoformat()
    assert not FakeClient.instances  # no request before the expiry is known

    day = dt_util.now().date() + timedelta(days=200)
    result = await submit(hass, result, token_expires=day.isoformat())
    assert result["step_id"] == "site"
    assert FakeClient.instances[0].kind is TokenKind.PERSONAL
    assert FakeClient.instances[0].token_exp == cf.end_of_day_timestamp(day)

    result = await submit(hass, result, metering_points=["1001"], site_slug="home")
    result = await submit(
        hass, result, include_1=True, slug_1="beg", include_2=False, slug_2="eeg"
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOKEN] == token
    assert result["data"][CONF_TOKEN_KIND] == "personal"
    assert result["data"][CONF_TOKEN_EXP] == cf.end_of_day_timestamp(day)
    assert result["data"][CONF_MP_IDS] == [1001]
    assert result["data"][CONF_COMMUNITIES] == [
        {"id": 2001, "slug": "beg", "name": "Example BEG"}
    ]


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ((dt_util.now().date() - timedelta(days=1)).isoformat(), "token_expired"),
    ],
)
async def test_personal_token_expiry_must_be_future(
    hass: HomeAssistant, value: str, error: str
) -> None:
    """A past or invalid expiry date is refused before any request."""

    result = await submit(hass, await start(hass), token=personal_token())
    result = await submit(hass, result, token_expires=value)
    assert result["step_id"] == "token_expiry"
    assert result["errors"] == {"token_expires": error}
    assert not FakeClient.instances


async def test_personal_token_online_error_on_expiry_step(hass: HomeAssistant) -> None:
    """Online errors after the expiry step are shown on that step."""

    FakeClient.session_error = AuthError("401")
    result = await submit(hass, await start(hass), token=personal_token())
    day = dt_util.now().date() + timedelta(days=30)
    result = await submit(hass, result, token_expires=day.isoformat())
    assert result["step_id"] == "token_expiry"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_session_jwt_expiring_soon_notice(hass: HomeAssistant) -> None:
    """Less than 7 days left: a notice step, then the flow continues."""

    result = await submit(hass, await start(hass), token=session_jwt(3))
    assert result["step_id"] == "expiring_soon"
    assert result["description_placeholders"]["days"] in {"2", "3"}
    result = await submit(hass, result)
    assert result["step_id"] == "site"


# ---------------------------------------------------------------------------
# Case 32: errors


@pytest.mark.parametrize(
    ("token", "error"),
    [
        ("garbage", "invalid_token_format"),
        ("ecapi_", "invalid_token_format"),
        (None, "token_expired"),
    ],
)
async def test_local_token_errors(
    hass: HomeAssistant, token: str | None, error: str
) -> None:
    """Format and expiry are checked locally, with no request sent."""

    result = await submit(hass, await start(hass), token=token or session_jwt(-1))
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    assert not FakeClient.instances


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (AuthError("401"), "invalid_auth"),
        (ForbiddenError("403"), "invalid_auth"),
        (PortalUnavailableError("503", status=503), "cannot_connect"),
        (RateLimitedError("429"), "cannot_connect"),
        (ApiChangedError("404", status=404), "api_changed"),
        (SchemaError("S2", "members", "missing"), "api_changed"),
        (RuntimeError("boom"), "unknown"),
    ],
)
@pytest.mark.parametrize("where", ["session", "mp"])
async def test_online_errors(
    hass: HomeAssistant, exc: Exception, error: str, where: str
) -> None:
    """Client errors map to flow errors on the token step."""

    if where == "session":
        FakeClient.session_error = exc
    else:
        FakeClient.mp_error = exc
    result = await submit(hass, await start(hass), token=session_jwt(30))
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}

    # Recovers once the portal answers.
    FakeClient.session_error = FakeClient.mp_error = None
    result = await submit(hass, result, token=session_jwt(30))
    assert result["step_id"] == "site"


async def test_unknown_error_logs_no_token(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An unexpected error logs its type only, never the token."""

    token = session_jwt(30)
    FakeClient.session_error = RuntimeError(token)
    await submit(hass, await start(hass), token=token)
    assert "RuntimeError" in caplog.text
    assert token not in caplog.text


async def test_no_member(hass: HomeAssistant) -> None:
    """A session with no members is an error."""

    FakeClient.session = parse_session({"email": "x", "members": []})
    result = await submit(hass, await start(hass), token=session_jwt(30))
    assert result["errors"] == {"base": "no_member"}


async def test_two_members_select(hass: HomeAssistant) -> None:
    """Several members: a select labelled by display name; terms notice."""

    FakeClient.session = parse_session(load_fixture("session_two_members.json"))
    result = await submit(hass, await start(hass), token=session_jwt(30))
    assert result["step_id"] == "member"
    selector = result["data_schema"].schema["member"]
    labels = [o["label"] for o in selector.config["options"]]
    assert labels == ["Example Member", "Example Company"]

    # The second member has not accepted the terms; the portal refuses.
    FakeClient.mp_error = ForbiddenError("403")
    result = await submit(hass, result, member="1")
    assert result["errors"] == {"base": "terms_not_accepted"}

    # The portal accepts anyway: notice, then continue.
    FakeClient.mp_error = None
    result = await submit(hass, result, member="1")
    assert result["step_id"] == "accept_terms"
    calls = FakeClient.instances[-1].calls
    assert calls == [("get_metering_points", "9000002")]
    result = await submit(hass, result)
    assert result["step_id"] == "site"
    result = await submit(hass, result, metering_points=["1001"], site_slug="home")
    result = await submit(
        hass, result, include_1=True, slug_1="beg", include_2=True, slug_2="eeg"
    )
    assert result["data"][CONF_MEMBER_NUMBER] == "9000002"
    assert "Example Company" not in json.dumps(result["data"])


async def test_member_error_shown_on_member_step(hass: HomeAssistant) -> None:
    """Online errors after the member choice stay on the member step."""

    FakeClient.session = parse_session(load_fixture("session_two_members.json"))
    result = await submit(hass, await start(hass), token=session_jwt(30))
    FakeClient.mp_error = PortalUnavailableError("timeout")
    result = await submit(hass, result, member="0")
    assert result["step_id"] == "member"
    assert result["errors"] == {"base": "cannot_connect"}


# ---------------------------------------------------------------------------
# Case 33: location, metering points, slugs


async def test_two_locations_select(hass: HomeAssistant) -> None:
    """Two locations: select by street label, then that location's points."""

    FakeClient.mp = parse_metering_points(
        load_fixture("metering_point_two_locations.json")
    )
    result = await submit(hass, await start(hass), token=session_jwt(30))
    assert result["step_id"] == "location"
    options = result["data_schema"].schema["location"].config["options"]
    assert [o["value"] for o in options] == ["5001", "5002"]
    assert all("Example" in o["label"] for o in options)

    result = await submit(hass, result, location="5002")
    assert result["step_id"] == "site"
    mp_options = result["data_schema"].schema["metering_points"].config["options"]
    assert [o["value"] for o in mp_options] == ["1003"]
    assert mp_options[0]["label"].endswith("1003")
    result = await submit(hass, result, metering_points=["1003"], site_slug="cabin")
    assert result["step_id"] == "communities"
    result = await submit(hass, result, include_1=True, slug_1="beg")
    assert result["data"][CONF_LOCATION_ID] == "5002"
    assert result["data"][CONF_SITE_SLUG] == "cabin"
    assert result["result"].unique_id == cf.entry_unique_id(MEMBER, "5002")


async def test_metering_point_defaults_and_labels(hass: HomeAssistant) -> None:
    """Active points are preselected; labels are direction + last 4."""

    raw = mp_raw()
    raw[0]["memberMeteringpoints"][1]["activations"][0]["end"] = "2026-01-31"
    raw[0]["memberMeteringpoints"][1]["activations"][1]["status"] = "Inactive"
    FakeClient.mp = parse_metering_points(raw)
    hass.config.language = "de"
    result = await submit(hass, await start(hass), token=session_jwt(30))
    key = next(k for k in result["data_schema"].schema if str(k) == "metering_points")
    assert key.default() == ["1001"]
    labels = [o["label"] for o in result["data_schema"].schema[key].config["options"]]
    assert labels == ["Bezug …1001", "Einspeisung …1002"]


@pytest.mark.parametrize(
    ("data", "errors"),
    [
        (
            {"metering_points": [], "site_slug": "home"},
            {"metering_points": "no_metering_point"},
        ),
        (
            {"metering_points": ["1001"], "site_slug": "Home"},
            {"site_slug": "invalid_slug"},
        ),
        (
            {"metering_points": ["1001"], "site_slug": "1home"},
            {"site_slug": "invalid_slug"},
        ),
        (
            {"metering_points": ["1001"], "site_slug": "a" * 21},
            {"site_slug": "invalid_slug"},
        ),
        (
            {"metering_points": ["1001"], "site_slug": "ho-me"},
            {"site_slug": "invalid_slug"},
        ),
    ],
)
async def test_site_validation(
    hass: HomeAssistant, data: dict[str, Any], errors: dict[str, str]
) -> None:
    """At least one metering point; the slug pattern is enforced."""

    result = await submit(hass, await start(hass), token=session_jwt(30))
    result = await submit(hass, result, **data)
    assert result["step_id"] == "site"
    assert result["errors"] == errors


async def test_site_slug_in_use(hass: HomeAssistant) -> None:
    """A site slug already used by another entry is refused (global ids)."""

    make_entry(hass, **{CONF_LOCATION_ID: "5002"})
    result = await submit(hass, await start(hass), token=session_jwt(30))
    result = await submit(hass, result, metering_points=["1001"], site_slug="home")
    assert result["errors"] == {"site_slug": "slug_in_use"}


async def test_site_without_community(hass: HomeAssistant) -> None:
    """Selected points without activations: no community to import."""

    raw = mp_raw()
    raw[0]["memberMeteringpoints"][0]["activations"] = []
    FakeClient.mp = parse_metering_points(raw)
    result = await submit(hass, await start(hass), token=session_jwt(30))
    result = await submit(hass, result, metering_points=["1001"], site_slug="home")
    assert result["errors"] == {"base": "no_community"}


@pytest.mark.parametrize(
    ("data", "errors"),
    [
        (
            {"include_1": True, "slug_1": "beg", "include_2": True, "slug_2": "beg"},
            {"slug_2": "duplicate_slug"},
        ),
        (
            {"include_1": True, "slug_1": "BEG", "include_2": True, "slug_2": "eeg"},
            {"slug_1": "invalid_slug"},
        ),
        (
            {"include_1": False, "slug_1": "beg", "include_2": False, "slug_2": "eeg"},
            {"base": "no_community"},
        ),
    ],
)
async def test_community_validation(
    hass: HomeAssistant, data: dict[str, Any], errors: dict[str, str]
) -> None:
    """Slugs unique within the entry, pattern enforced, one community needed."""

    result = await submit(hass, await start(hass), token=session_jwt(30))
    result = await submit(hass, result, metering_points=["1001"], site_slug="home")
    result = await submit(hass, result, **data)
    assert result["step_id"] == "communities"
    assert result["errors"] == errors


async def test_community_excluded_slug_not_checked(hass: HomeAssistant) -> None:
    """An excluded row's slug is ignored."""

    result = await submit(hass, await start(hass), token=session_jwt(30))
    result = await submit(hass, result, metering_points=["1001"], site_slug="home")
    result = await submit(
        hass, result, include_1=True, slug_1="beg", include_2=False, slug_2="BAD"
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


def test_default_slugs() -> None:
    """``beg``, ``eeg``, ``eeg2``; unknown types get ``ec``."""

    assert cf.default_slug("CC", set()) == "beg"
    assert cf.default_slug("RC_R", set()) == "eeg"
    assert cf.default_slug("RC_R", {"eeg"}) == "eeg2"
    assert cf.default_slug("RC_L", {"eeg", "eeg2"}) == "eeg3"
    assert cf.default_slug("XX", set()) == "ec"


async def test_no_location_aborts(hass: HomeAssistant) -> None:
    """An empty metering-point list aborts."""

    FakeClient.mp = parse_metering_points([])
    result = await submit(hass, await start(hass), token=session_jwt(30))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_location"


async def test_location_without_points_aborts(hass: HomeAssistant) -> None:
    """A location with no metering points aborts."""

    raw = mp_raw()
    raw[0]["memberMeteringpoints"] = []
    FakeClient.mp = parse_metering_points(raw)
    result = await submit(hass, await start(hass), token=session_jwt(30))
    assert result["reason"] == "no_metering_points"


# ---------------------------------------------------------------------------
# Case 34: duplicates


async def test_duplicate_entry_aborts(hass: HomeAssistant) -> None:
    """The same member and location can't be added twice."""

    make_entry(hass)
    result = await submit(hass, await start(hass), token=session_jwt(30))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_second_location_allowed(hass: HomeAssistant) -> None:
    """Another location of the same member is a separate entry."""

    make_entry(hass)
    FakeClient.mp = parse_metering_points(
        load_fixture("metering_point_two_locations.json")
    )
    result = await submit(hass, await start(hass), token=session_jwt(30))
    result = await submit(hass, result, location="5002")
    assert result["step_id"] == "site"


# ---------------------------------------------------------------------------
# Case 35: reauth


async def test_reauth_session_to_personal(hass: HomeAssistant) -> None:
    """Reauth stores the new token, kind and expiry and clears the warning."""

    entry = make_entry(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"token_expiring_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="token_expiring",
    )
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    token = personal_token()
    result = await submit(hass, result, token=token)
    assert result["step_id"] == "token_expiry"
    day = dt_util.now().date() + timedelta(days=365)
    result = await submit(hass, result, token_expires=day.isoformat())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == token
    assert entry.data[CONF_TOKEN_KIND] == "personal"
    assert entry.data[CONF_TOKEN_EXP] == cf.end_of_day_timestamp(day)
    assert entry.data[CONF_COMMUNITIES] == [
        {"id": 2001, "slug": "beg", "name": "Example BEG"}
    ]
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"token_expiring_{entry.entry_id}") is None
    assert FakeClient.instances[0].calls == [
        ("get_session",),
        ("get_metering_points", MEMBER),
    ]


async def test_reauth_personal_to_session(hass: HomeAssistant) -> None:
    """A session JWT works for reauth too (no expiry step)."""

    entry = make_entry(
        hass, **{CONF_TOKEN: personal_token(), CONF_TOKEN_KIND: "personal"}
    )
    token = session_jwt(25)
    result = await entry.start_reauth_flow(hass)
    result = await submit(hass, result, token=token)
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == token
    assert entry.data[CONF_TOKEN_KIND] == "session"
    assert entry.data[CONF_TOKEN_EXP] == parse_token(token).exp


async def test_reauth_wrong_account_member(hass: HomeAssistant) -> None:
    """The stored member is not in the new token's members."""

    entry = make_entry(hass)
    FakeClient.session = parse_session(
        {"members": [{"memberId": "9000002", "mustAcceptPlatformTerms": False}]}
    )
    old = entry.data[CONF_TOKEN]
    result = await entry.start_reauth_flow(hass)
    result = await submit(hass, result, token=session_jwt(20))
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "wrong_account"}
    assert entry.data[CONF_TOKEN] == old


async def test_reauth_wrong_account_forbidden(hass: HomeAssistant) -> None:
    """A 403 on the stored member's metering points is the wrong account."""

    entry = make_entry(hass)
    FakeClient.mp_error = ForbiddenError("403")
    result = await entry.start_reauth_flow(hass)
    result = await submit(hass, result, token=session_jwt(20))
    assert result["errors"] == {"base": "wrong_account"}


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (AuthError("Access token revoked"), "invalid_auth"),
        (PortalUnavailableError("timeout"), "cannot_connect"),
    ],
)
async def test_reauth_errors(hass: HomeAssistant, exc: Exception, error: str) -> None:
    """A revoked token (401) or an outage keeps the reauth form open."""

    entry = make_entry(hass)
    FakeClient.session_error = exc
    result = await entry.start_reauth_flow(hass)
    result = await submit(hass, result, token=session_jwt(20))
    assert result["errors"] == {"base": error}
    result = await submit(hass, result, token="garbage")
    assert result["errors"] == {"base": "invalid_token_format"}


# ---------------------------------------------------------------------------
# Case 36: reconfigure


def _add_community(raw: list[dict[str, Any]], cid: int, name: str, kind: str) -> None:
    for mp in raw[0]["memberMeteringpoints"]:
        act = copy.deepcopy(mp["activations"][0])
        act.update(
            energyCommunityId=cid,
            energyCommunityName=name,
            energyCommunityType=kind,
            begin="2026-06-01",
        )
        mp["activations"].append(act)


async def test_reconfigure_adds_community(hass: HomeAssistant) -> None:
    """A newly joined community gets a new slug; stored slugs stay."""

    entry = make_entry(hass)
    old_token = entry.data[CONF_TOKEN]
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await submit(hass, result)  # keep the stored token
    assert FakeClient.instances[0].token == old_token
    assert result["step_id"] == "reconfigure_site"
    assert result["description_placeholders"]["site_slug"] == "home"
    assert "site_slug" not in {str(k) for k in result["data_schema"].schema}

    result = await submit(hass, result, metering_points=["1001", "1002"])
    assert result["step_id"] == "reconfigure_communities"
    placeholders = result["description_placeholders"]
    assert "`beg`" in placeholders["kept"]
    assert "Example EEG" in placeholders["new"]
    keys = {str(k) for k in result["data_schema"].schema}
    assert keys == {"include_1", "slug_1"}  # only the new community is editable

    # A stored slug can't be taken by the new community.
    result = await submit(hass, result, include_1=True, slug_1="beg")
    assert result["errors"] == {"slug_1": "duplicate_slug"}
    result = await submit(hass, result, include_1=True, slug_1="eeg")
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_COMMUNITIES] == [
        {"id": 2001, "slug": "beg", "name": "Example BEG"},
        {"id": 2002, "slug": "eeg", "name": "Example EEG"},
    ]
    assert entry.data[CONF_TOKEN] == old_token
    assert entry.data[CONF_SITE_SLUG] == "home"


async def test_reconfigure_changes_metering_points(hass: HomeAssistant) -> None:
    """Nothing new to decide: the selection is saved directly."""

    entry = make_entry(
        hass,
        **{
            CONF_COMMUNITIES: [
                {"id": 2001, "slug": "beg", "name": "Old name"},
                {"id": 2002, "slug": "eeg", "name": "Example EEG"},
            ]
        },
    )
    result = await entry.start_reconfigure_flow(hass)
    result = await submit(hass, result)
    key = next(k for k in result["data_schema"].schema if str(k) == "metering_points")
    assert key.default() == ["1001", "1002"]
    result = await submit(hass, result, metering_points=["1002"])
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_MP_IDS] == [1002]
    # Names follow the portal; slugs don't change.
    assert entry.data[CONF_COMMUNITIES][0] == {
        "id": 2001,
        "slug": "beg",
        "name": "Example BEG",
    }


async def test_reconfigure_remaps_changed_community_id(hass: HomeAssistant) -> None:
    """A community whose portal id changed keeps its slug and history."""

    raw = mp_raw()
    for mp in raw[0]["memberMeteringpoints"]:
        mp["activations"] = [
            a for a in mp["activations"] if a["energyCommunityId"] == 2001
        ]
        mp["activations"][0]["energyCommunityId"] = 2101
    FakeClient.mp = parse_metering_points(raw)
    entry = make_entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await submit(hass, result)
    result = await submit(hass, result, metering_points=["1001", "1002"])
    assert result["step_id"] == "reconfigure_communities"
    assert "`beg`" in result["description_placeholders"]["orphans"]
    slug_key = next(k for k in result["data_schema"].schema if str(k) == "slug_1")
    assert slug_key.default() == "beg"  # same name: suggests the old slug
    result = await submit(hass, result, include_1=True, slug_1="beg")
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_COMMUNITIES] == [
        {"id": 2101, "slug": "beg", "name": "Example BEG"}
    ]
    assert entry.data[CONF_RETIRED_SLUGS] == []


async def test_reconfigure_retires_unmapped_slug(hass: HomeAssistant) -> None:
    """A gone community whose slug is not reused is retired, never reused."""

    raw = mp_raw()
    _add_community(raw, 2003, "Another EEG", "RC_R")
    FakeClient.mp = parse_metering_points(raw)
    entry = make_entry(
        hass,
        **{
            CONF_COMMUNITIES: [
                {"id": 2001, "slug": "beg", "name": "Example BEG"},
                {"id": 2999, "slug": "old", "name": "Gone EEG"},
            ]
        },
    )
    result = await entry.start_reconfigure_flow(hass)
    result = await submit(hass, result)
    result = await submit(hass, result, metering_points=["1001", "1002"])
    defaults = {
        str(k): k.default() for k in result["data_schema"].schema if "slug" in str(k)
    }
    assert defaults == {"slug_1": "eeg", "slug_2": "eeg2"}
    result = await submit(
        hass, result, include_1=True, slug_1="eeg", include_2=False, slug_2="eeg2"
    )
    assert result["reason"] == "reconfigure_successful"
    assert [c["slug"] for c in entry.data[CONF_COMMUNITIES]] == ["beg", "eeg"]
    assert entry.data[CONF_RETIRED_SLUGS] == ["old"]


async def test_reconfigure_restores_retired_slug(hass: HomeAssistant) -> None:
    """A retired slug may be given back to a community (history continues)."""

    entry = make_entry(hass, **{CONF_RETIRED_SLUGS: ["eeg"]})
    result = await entry.start_reconfigure_flow(hass)
    result = await submit(hass, result)
    result = await submit(hass, result, metering_points=["1001", "1002"])
    slug_key = next(k for k in result["data_schema"].schema if str(k) == "slug_1")
    assert slug_key.default() == "eeg2"
    result = await submit(hass, result, include_1=True, slug_1="eeg")
    assert entry.data[CONF_RETIRED_SLUGS] == []
    assert entry.data[CONF_COMMUNITIES][1]["slug"] == "eeg"


async def test_reconfigure_new_token(hass: HomeAssistant) -> None:
    """A new token pasted in reconfigure is checked like reauth and stored."""

    entry = make_entry(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"token_expiring_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="token_expiring",
    )
    token = personal_token()
    result = await entry.start_reconfigure_flow(hass)
    result = await submit(hass, result, token=token)
    assert result["step_id"] == "token_expiry"
    day = dt_util.now().date() + timedelta(days=300)
    result = await submit(hass, result, token_expires=day.isoformat())
    assert result["step_id"] == "reconfigure_site"
    result = await submit(hass, result, metering_points=["1001"])
    result = await submit(hass, result, include_1=False, slug_1="eeg")
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_TOKEN] == token
    assert entry.data[CONF_TOKEN_KIND] == "personal"
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"token_expiring_{entry.entry_id}") is None


async def test_reconfigure_wrong_account(hass: HomeAssistant) -> None:
    """A token of another member is refused in reconfigure as well."""

    entry = make_entry(hass)
    FakeClient.session = parse_session(
        {"members": [{"memberId": "9000002", "mustAcceptPlatformTerms": False}]}
    )
    result = await entry.start_reconfigure_flow(hass)
    result = await submit(hass, result, token=session_jwt(20))
    assert result["errors"] == {"base": "wrong_account"}


async def test_reconfigure_location_remap(hass: HomeAssistant) -> None:
    """The stored location id is gone: choose the current one."""

    entry = make_entry(hass, **{CONF_LOCATION_ID: "4999"})
    result = await entry.start_reconfigure_flow(hass)
    result = await submit(hass, result)
    assert result["step_id"] == "reconfigure_location"
    result = await submit(hass, result, location="5001")
    assert result["step_id"] == "reconfigure_site"
    result = await submit(hass, result, metering_points=["1001"])
    result = await submit(hass, result, include_1=False, slug_1="eeg")
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_LOCATION_ID] == "5001"
    assert entry.unique_id == cf.entry_unique_id(MEMBER, "5001")


async def test_reconfigure_no_metering_point(hass: HomeAssistant) -> None:
    """At least one metering point stays required."""

    entry = make_entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await submit(hass, result)
    result = await submit(hass, result, metering_points=[])
    assert result["errors"] == {"metering_points": "no_metering_point"}


# ---------------------------------------------------------------------------
# Options


async def test_options_flow(hass: HomeAssistant) -> None:
    """Options default to the spec values and are stored as entered."""

    entry = make_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults == {
        "max_hourly_kwh": 50,
        "daily_window_days": 62,
        "long_refetch_months": 13,
        "import_history": True,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "max_hourly_kwh": 30,
            "daily_window_days": 90,
            "long_refetch_months": 12,
            "import_history": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_MAX_HOURLY_KWH] == 30.0
    assert entry.options[CONF_IMPORT_HISTORY] is False


# ---------------------------------------------------------------------------
# Strings and translations


def _flatten(obj: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out |= _flatten(value, path)
        else:
            out[path] = value
    return out


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_translations_match_strings() -> None:
    """The English file equals strings.json; German has the same keys."""

    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    en = json.loads((COMPONENT / "translations/en.json").read_text(encoding="utf-8"))
    de = json.loads((COMPONENT / "translations/de.json").read_text(encoding="utf-8"))
    assert en == strings
    flat_en, flat_de = _flatten(en), _flatten(de)
    assert flat_en.keys() == flat_de.keys()
    for key, text in flat_en.items():
        assert _placeholders(text) == _placeholders(flat_de[key]), key
        assert "http" not in text, key  # URLs go through placeholders
        assert not re.search(r"<[A-Za-z/]", text), key  # hassfest: no HTML
        assert not re.search(r"<[A-Za-z/]", flat_de[key]), key


def test_strings_cover_flow_steps_and_errors() -> None:
    """Every step, error and abort used in the code has a string."""

    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    config = strings["config"]
    for step in (
        "user",
        "token_expiry",
        "expiring_soon",
        "member",
        "accept_terms",
        "location",
        "site",
        "communities",
        "reauth_confirm",
        "reconfigure",
        "reconfigure_location",
        "reconfigure_site",
        "reconfigure_communities",
    ):
        assert f'step_id="{step}"' in source
        assert step in config["step"]
    for error in config["error"]:
        assert f'"{error}"' in source
    for i in range(1, cf.MAX_COMMUNITIES + 1):
        assert f"slug_{i}" in config["step"]["communities"]["data"]
