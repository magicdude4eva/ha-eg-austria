"""Diagnostics download and redaction (test plan case 39, spec 06).

The entry holds a session JWT that carries the synthetic email address; the
portal double answers with fixtures that contain every sensitive key, plus an
injected error message and unknown keys with sensitive values. None of the
synthetic sensitive strings may appear anywhere in the output.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
import json
import secrets
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import jwt
import pytest

from custom_components.eg_austria.coordinator import RunRequest
from custom_components.eg_austria.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)
from custom_components.eg_austria.exceptions import ApiChangedError
from custom_components.eg_austria.importer import RunType
from custom_components.eg_austria.redact import (
    REDACTED,
    TO_REDACT,
    redact_tree,
    schema_fingerprint,
)

from .conftest import async_setup_fake_entry

NOW = datetime(2026, 6, 8, 4, 0, tzinfo=UTC)
EMAIL = "member@example.invalid"
MP_NUMBER = "AT0000000000000000000000000001001"
SENSITIVE = (
    EMAIL,
    "9000001",  # member number
    "5001",  # location id
    "1001",  # metering point ids
    "1002",
    "2001",  # community ids
    "2002",
    MP_NUMBER,
    "Example BEG",  # community names
    "Example EEG",
    "Example Member",
    "Example street",
    "Example town",
)


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before ``hass`` (see test_statistics.py)."""


@pytest.fixture
def no_writes() -> Iterator[AsyncMock]:
    """Skip the statistics write."""

    with patch(
        "custom_components.eg_austria.importer.async_import_window",
        AsyncMock(return_value={}),
    ) as mock:
        yield mock


@pytest.fixture
async def env(
    hass: HomeAssistant, recorder_mock: Any, freezer: FrozenDateTimeFactory
) -> FrozenDateTimeFactory:
    """Frozen time in Vienna with a recorder."""

    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Vienna")
    return freezer


def session_jwt(days: float) -> tuple[str, int, int]:
    """Return a synthetic session JWT carrying the email, its iat and exp."""

    iat = int(NOW.timestamp()) - 86400
    exp = int(NOW.timestamp() + days * 86400)
    payload = {"email": EMAIL, "sub": "9000001", "iat": iat, "exp": exp}
    return jwt.encode(payload, secrets.token_hex(32), algorithm="HS256"), iat, exp


def assert_clean(output: Any, token: str) -> str:
    """No sensitive string anywhere in the JSON text of ``output``."""

    text = json.dumps(output, default=str)
    assert token not in text
    for part in token.split("."):
        assert part not in text
    assert "eyJ" not in text
    assert "ecapi_" not in text
    for value in SENSITIVE:
        assert value not in text, value
    return text


async def test_diagnostics_redacted(
    hass: HomeAssistant,
    env: Any,
    no_writes: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Full download after a run with every sensitive key and pattern."""

    token, iat, exp = session_jwt(20)
    loaded = await async_setup_fake_entry(
        hass, token_exp=exp, token=token, token_kind="session"
    )
    client = loaded.client
    client.maintenance_raw = [
        {
            "id": 7,
            "start": "2026-07-01T01:00:00.000Z",
            "end": "2026-07-01T03:00:00.000Z",
            "title": f"Maintenance for {EMAIL}",
        }
    ]

    def mutate(raw: Any, agg: str, start: date, end: date, cid: int) -> Any:
        # Unknown keys with sensitive values end up in schema_extra (names
        # only) and must not leak their values.
        raw[f"note_{cid}"] = f"{MP_NUMBER} {EMAIL}"
        return raw

    client.mutate = mutate
    await loaded.coordinator.async_run(RunRequest(RunType.DAILY))
    await hass.async_block_till_done()
    output = await async_get_config_entry_diagnostics(hass, loaded.entry)
    text = assert_clean(output, token)

    entry = output["entry"]
    assert entry["site_slug"] == "home"
    assert entry["community_slugs"] == ["beg", "eeg"]
    assert entry["metering_points"] == 2
    assert entry["directions"] == ["Consumption", "Generation"]
    assert entry["data"]["token"] == REDACTED
    assert entry["data"]["member_number"] == REDACTED
    assert entry["data"]["mp_ids"] == REDACTED
    assert entry["data"]["communities"][0] == {
        "id": REDACTED,
        "slug": "beg",
        "name": REDACTED,
    }
    assert output["token"] == {
        "kind": "session",
        "expires": datetime.fromtimestamp(exp, UTC).isoformat(),
        "days_left": 20,
        "created_by_flow": False,
        "issued_at": datetime.fromtimestamp(iat, UTC).isoformat(),
    }
    assert output["store"]["last_success"] == NOW.isoformat()
    run = output["last_run"]
    assert run["status"] == "ok"
    assert run["run_type"] == "daily"
    assert run["requests"] > 0
    assert run["window"][1] == "2026-06-08"
    assert output["portal"]["git_commit"]
    assert output["portal"]["maintenance"] == [
        {"start": "2026-07-01T01:00:00+00:00", "end": "2026-07-01T03:00:00+00:00"}
    ]
    assert "Maintenance for" not in text  # portal free text is left out
    assert "note_2001" not in text  # key names are scrubbed too
    # Dates survive although they contain id-like digits.
    assert "2026-06-08" in text
    # Nor in the logs of setup, run and download.
    logs = caplog.text
    assert token not in logs
    for value in (EMAIL, MP_NUMBER, "9000001"):
        assert value not in logs, value


async def test_diagnostics_error_message_redacted(
    hass: HomeAssistant, env: Any
) -> None:
    """A redacted error that still had raw values in it is scrubbed again."""

    loaded = await async_setup_fake_entry(hass, token_exp=int(NOW.timestamp()) + 9e6)
    # The client already redacts; simulate a message that slipped through.
    loaded.client.errors["get_version"] = ApiChangedError(
        f"HTTP 404 for member 9000001, location 5001, {MP_NUMBER}, {EMAIL}"
    )
    with patch(
        "custom_components.eg_austria.coordinator.redact_text", lambda t, s=(): t
    ):
        await loaded.coordinator.async_run(RunRequest(RunType.DAILY))
    await hass.async_block_till_done()
    assert "9000001" in (loaded.coordinator.data.error or "")
    output = await async_get_config_entry_diagnostics(hass, loaded.entry)
    assert_clean(output, loaded.entry.data["token"])
    assert output["last_run"]["status"] == "api_changed"
    # Issue #78: the store keeps the (scrubbed) error for after a restart.
    assert "HTTP 404" in output["store"]["last_run_error"]
    assert output["token"]["kind"] == "personal"
    assert "issued_at" not in output["token"]
    assert output["schema_fingerprint"] == {}


async def test_diagnostics_statistics_ranges(hass: HomeAssistant, env: Any) -> None:
    """First and last stored hour per statistic, no sums."""

    loaded = await async_setup_fake_entry(hass, token_exp=int(NOW.timestamp()) + 9e6)
    await loaded.coordinator.async_run(RunRequest(RunType.MANUAL, NOW.date(), None))
    await hass.async_block_till_done()
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    await async_wait_recording_done(hass)
    output = await async_get_config_entry_diagnostics(hass, loaded.entry)
    stats = output["statistics"]
    assert "eg_austria:home_consumption" in stats
    row = stats["eg_austria:home_consumption"]
    assert set(row) == {"first_hour", "last_hour"}
    assert row["first_hour"] <= row["last_hour"]
    assert row["first_hour"].startswith("2026-06-0")
    assert_clean(output, loaded.entry.data["token"])


async def test_diagnostics_not_loaded(hass: HomeAssistant, env: Any) -> None:
    """An entry that is not loaded still gives redacted entry and token parts."""

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import entry_data

    entry = MockConfigEntry(
        domain="eg_austria",
        title="EG Austria Home",
        data=entry_data(int(NOW.timestamp()) + 86400 * 10),
    )
    entry.add_to_hass(hass)
    output = await async_get_config_entry_diagnostics(hass, entry)
    assert output["store"] is None
    assert output["last_run"] is None
    assert output["token"]["days_left"] == 10
    assert_clean(output, entry.data["token"])


async def test_diagnostics_created_token(hass: HomeAssistant, env: Any) -> None:
    """A token the flow created: flagged, its id redacted (ADR 0007)."""

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import entry_data

    token_id = "token-id-" + secrets.token_hex(4)
    data = entry_data(int(NOW.timestamp()) + 86400 * 300) | {"token_id": token_id}
    entry = MockConfigEntry(domain="eg_austria", title="EG Austria Home", data=data)
    entry.add_to_hass(hass)
    output = await async_get_config_entry_diagnostics(hass, entry)
    assert output["token"]["created_by_flow"] is True
    assert output["entry"]["data"]["token_id"] == REDACTED
    assert token_id not in json.dumps(output)
    assert_clean(output, entry.data["token"])


# ---------------------------------------------------------------------------
# Helpers


def test_redact_tree_keys_and_patterns() -> None:
    """Keys of TO_REDACT at any depth; patterns and ids in other strings."""

    token, _, _ = session_jwt(5)
    personal = "ecapi_" + secrets.token_hex(16)
    data = {
        "memberId": "9000001",
        "nested": [{"meteringPointNumber": MP_NUMBER, "count": 1001}],
        "message": f"{token} {personal} {MP_NUMBER} {EMAIL} member 9000001",
        "location 5001": "x",
        "day": "2026-05-01",
        "stamp": "2026-05-01T05:00:00+00:00",
        "ids": {2001, 2002},
    }
    out = redact_tree(data, ["9000001", "5001", "05", "2001", "2002"])
    assert out["memberId"] == REDACTED
    assert out["nested"][0] == {"meteringPointNumber": REDACTED, "count": 1001}
    assert out["message"] == " ".join([REDACTED] * 4 + ["member", REDACTED])
    assert f"location {REDACTED}" in out
    assert out["day"] == "2026-05-01"  # "05" not touched inside a date
    assert out["stamp"] == "2026-05-01T05:00:00+00:00"
    assert out["ids"] == [2001, 2002]  # numbers are kept (ids sit under keys)
    assert {"token", "email", "name", "displayName", "iban"} <= TO_REDACT


def test_schema_fingerprint() -> None:
    """Types only, recursively, first list element only."""

    raw = {
        "b": [{"x": 1.5, "y": None}, {"x": "other"}],
        "a": True,
        "c": [],
        "d": {"e": "secret", "f": 3},
    }
    assert schema_fingerprint(raw) == {
        "a": "bool",
        "b": [{"x": "float", "y": "null"}],
        "c": [],
        "d": {"e": "str", "f": "int"},
    }
    assert "secret" not in json.dumps(schema_fingerprint(raw))


def test_jwt_issued_at() -> None:
    """``iat`` only; None for anything that isn't a JWT with a numeric iat."""

    from custom_components.eg_austria.token import jwt_issued_at

    token, iat, _ = session_jwt(3)
    assert jwt_issued_at(token) == iat
    assert jwt_issued_at("not-a-jwt") is None
    no_iat = jwt.encode({"exp": 1}, secrets.token_hex(32), algorithm="HS256")
    assert jwt_issued_at(no_iat) is None


async def test_device_diagnostics_same_as_entry(
    hass: HomeAssistant, env: Any, no_writes: AsyncMock
) -> None:
    """The device page download is the redacted entry download."""

    token, _, exp = session_jwt(20)
    loaded = await async_setup_fake_entry(
        hass, token_exp=exp, token=token, token_kind="session"
    )
    await loaded.coordinator.async_run(RunRequest(RunType.DAILY))
    await hass.async_block_till_done()
    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), loaded.entry.entry_id
    )
    assert len(devices) == 1
    output = await async_get_device_diagnostics(hass, loaded.entry, devices[0])
    assert output == await async_get_config_entry_diagnostics(hass, loaded.entry)
    assert_clean(output, token)


async def test_diagnostics_after_restart_keep_setup_and_shapes(
    hass: HomeAssistant, env: Any
) -> None:
    """Issue #78: last run, setup shape and response shapes survive a restart."""

    from custom_components.eg_austria.exceptions import SchemaError

    loaded = await async_setup_fake_entry(hass, token_exp=int(NOW.timestamp()) + 9e6)
    client = loaded.client
    client.schema_fingerprints = {
        "analysis-data": schema_fingerprint(
            {"totalConsumption": 1.5, "consumptionDataPoints": [], "x": None}
        )
    }
    client.errors["get_analysis"] = SchemaError(
        "S2", "totalGeneration", "required key missing"
    )
    await loaded.coordinator.async_run(RunRequest(RunType.DAILY))
    await hass.async_block_till_done()
    # A restart: nothing in memory any more, only the store.
    loaded.coordinator.data = None
    client.schema_fingerprints = {}
    loaded.coordinator.site = None
    output = await async_get_config_entry_diagnostics(hass, loaded.entry)
    text = assert_clean(output, loaded.entry.data["token"])
    assert output["last_run"]["status"] == "api_changed"
    assert output["last_run"]["validation"]["findings"][0]["rule"] == "S2"
    assert "totalGeneration" in output["last_run"]["error"]
    assert output["schema_fingerprint"]["analysis-data"]["totalConsumption"] == (
        "float"
    )
    setup = output["setup"]
    assert setup["location_found"] is True
    assert setup["selected_found"] == len(setup["selected"]) > 0
    assert setup["selected_missing"] == 0
    assert {p["direction"] for p in setup["selected"]} <= {"Consumption", "Generation"}
    for point in setup["selected"]:
        for act in point["activations"]:
            assert act["community"] in {"beg", "eeg", "other"}
            assert act["from"] is None or len(act["from"]) == 7  # YYYY-MM
    # Not repeated inside the store section.
    for key in ("last_run", "setup", "schema_fingerprints", "settlement_digests"):
        assert key not in output["store"]
    assert "Example" not in text


def test_setup_shape_other_community_and_months() -> None:
    """Unselected communities show as ``other``; no ids, names or numbers."""

    from custom_components.eg_austria.models import parse_metering_points
    from custom_components.eg_austria.snapshot import setup_shape

    from .conftest import load_fixture

    info = parse_metering_points(load_fixture("metering_point_single.json"))
    location = info.locations[0]
    mp_ids = [mp.id for mp in location.metering_points]
    first = location.metering_points[0].activations[0].community_id
    shape = setup_shape(info, location.id, mp_ids, {first: "beg"})
    text = json.dumps(shape)
    for value in SENSITIVE:
        assert value not in text, value
    communities = {a["community"] for p in shape["selected"] for a in p["activations"]}
    assert "beg" in communities
    assert communities <= {"beg", "other"}
    assert shape["selected_found"] == len(mp_ids)
    assert sum(shape["location_points"].values()) == len(mp_ids)
    assert setup_shape(info, "no-such-location", mp_ids, {})["location_found"] is (
        False
    )


async def test_diagnostics_setup_right_after_setup(
    hass: HomeAssistant, env: Any
) -> None:
    """Issue #84: the startup check fills ``setup`` before any run."""

    loaded = await async_setup_fake_entry(hass, token_exp=int(NOW.timestamp()) + 9e6)
    assert loaded.coordinator.data is None
    output = await async_get_config_entry_diagnostics(hass, loaded.entry)
    assert output["setup"] is not None
    assert output["setup"]["selected_found"] == 2
    assert_clean(output, loaded.entry.data["token"])
