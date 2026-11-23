"""Opt-in redacted debug export (issue #84, ADR 0016).

The export keeps energy values but must not keep any original id, number,
name, address, email or token. The fixtures use the synthetic test ids
(member 9000001, location 5001, ...), which are also the export's own
synthetic values, so the tests move the export's bases elsewhere: any
original id left in the output is then a leak.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.eg_austria import export
from custom_components.eg_austria.const import DOMAIN
from custom_components.eg_austria.export import (
    Pseudonyms,
    key_presence,
    redact_export,
    window,
)
from custom_components.eg_austria.models import (
    parse_analysis,
    parse_day_completeness,
    parse_metering_points,
    parse_session,
)

from .conftest import async_setup_fake_entry, load_fixture

NOW = datetime(2026, 6, 8, 4, 0, tzinfo=UTC)
EMAIL = "member@example.invalid"
MP_NUMBERS = ("AT0000000000000000000000000001001", "AT0000000000000000000000000001002")
TEXT_SENSITIVE = (
    EMAIL,
    *MP_NUMBERS,
    "Example BEG",
    "Example EEG",
    "Example Member",
    "Example street",
    "Example town",
)
ORIGINAL_IDS = {9000001, 5001, 1001, 1002, 2001, 2002}
TEST_BASES = {"member": 4000001, "location": 7001, "mp": 3001, "community": 8001}
ID_KEYS = {
    "id",
    "memberId",
    "locationId",
    "energyCommunityId",
    "communityId",
    "meteringPointId",
}


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before ``hass`` (see test_statistics.py)."""


@pytest.fixture(autouse=True)
def other_bases() -> Iterator[None]:
    """Synthetic values that differ from the fixtures' ids."""

    with patch.dict(export._BASES, TEST_BASES):
        yield


@pytest.fixture
async def env(
    hass: HomeAssistant, recorder_mock: Any, freezer: FrozenDateTimeFactory
) -> FrozenDateTimeFactory:
    """Frozen time in Vienna with a recorder."""

    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Vienna")
    return freezer


def _walk(value: Any, key: str | None = None) -> Iterator[tuple[str | None, Any]]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, k
            yield from _walk(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, key)
    else:
        yield key, value


def assert_no_originals(document: Any, token: str = "") -> None:
    """No original id, number, name, email or token anywhere."""

    text = json.dumps(document)
    for value in TEXT_SENSITIVE:
        assert value not in text, value
    assert "ecapi_" not in text
    if token:
        assert token not in text
    for key, value in _walk(document):
        is_id = key in ID_KEYS or key in {"mpIds", "energyCommunityIds"}
        if is_id and isinstance(value, int | str) and str(value).isdigit():
            assert int(value) not in ORIGINAL_IDS, (key, value)
        if isinstance(value, str) and value.isdigit():
            assert int(value) not in ORIGINAL_IDS, (key, value)


async def _export(hass: HomeAssistant, loaded: Any, **data: Any) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN,
        "debug_export",
        {"config_entry_id": loaded.entry.entry_id, **data},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    path = Path(str(response["path"]))
    document = json.loads(await hass.async_add_executor_job(path.read_text))
    await hass.async_add_executor_job(path.unlink)
    return {"response": response, "document": document}


async def test_export_written_redacted_and_parses(
    hass: HomeAssistant, env: Any
) -> None:
    """One file with every response; ids replaced consistently; it parses."""

    loaded = await async_setup_fake_entry(hass, token_exp=int(NOW.timestamp()) + 9e6)
    result = await _export(hass, loaded, days=3)
    response, doc = result["response"], result["document"]
    assert response["failed_steps"] == 0
    assert response["responses"] == len(doc["responses"])
    assert_no_originals(doc, loaded.entry.data["token"])
    assert doc["window"] == ["2026-06-06", "2026-06-08"]
    assert doc["entry"]["metering_points"] == [3001, 3002]
    assert [c["id"] for c in doc["entry"]["communities"]] == [8001, 8002]
    assert "energy and money values" in doc["notice"]

    by_endpoint: dict[str, list[dict[str, Any]]] = {}
    for item in doc["responses"]:
        by_endpoint.setdefault(item["endpoint"], []).append(item)
    # version, maintenance, session, metering-point, completeness, and per
    # community Day + Hour + Day per metering point.
    assert len(by_endpoint["analysis-data"]) == 2 * (2 + 2)

    session = parse_session(by_endpoint["portal-session"][0]["raw"])
    assert session.members[0].member_id == "4000001"
    info = parse_metering_points(by_endpoint["metering-point"][0]["raw"])
    location = info.location("7001")
    assert location is not None
    assert {mp.id for mp in location.metering_points} == {3001, 3002}
    assert {act.community_id for _, act in info.iter_activations("7001")} <= {
        8001,
        8002,
    }
    parse_day_completeness(by_endpoint["day-completeness"][0]["raw"])
    for item in by_endpoint["analysis-data"]:
        parse_analysis(item["raw"])
        params = dict(map(tuple, item["params"]))
        assert params["energyCommunityIds"] in {"8001", "8002"}
        assert item["key_presence"]["Consumption"]["missing"] == []
    per_mp = [
        i
        for i in by_endpoint["analysis-data"]
        if sum(p[0] == "mpIds" for p in i["params"]) == 1
    ]
    assert {dict(map(tuple, i["params"]))["mpIds"] for i in per_mp} == {"3001", "3002"}
    assert doc["setup"]["selected_found"] == 2
    # Energy values are kept (the point of the export).
    point = by_endpoint["analysis-data"][0]["raw"]["consumptionDataPoints"][0]
    assert isinstance(point["consumption"], float | int)


async def test_export_keeps_going_after_schema_error(
    hass: HomeAssistant, env: Any
) -> None:
    """Issue #78 shape: the failing response is kept with its key presence."""

    loaded = await async_setup_fake_entry(hass, token_exp=int(NOW.timestamp()) + 9e6)

    def drop_generation_total(raw: Any, *_: Any) -> Any:
        raw.pop("totalGeneration", None)
        return raw

    loaded.client.mutate = drop_generation_total
    result = await _export(hass, loaded, days=1)
    doc = result["document"]
    failed = [s for s in doc["steps"] if s["result"] != "ok"]
    assert failed
    assert all("S2 totalGeneration" in s["result"] for s in failed)
    assert result["response"]["failed_steps"] == len(failed)
    item = doc["responses"][failed[0]["responses"][0]]
    assert item["key_presence"]["Generation"]["missing"] == ["totalGeneration"]
    assert item["key_presence"]["Generation"]["points"] > 0
    assert_no_originals(doc)


async def test_export_refused_during_run(hass: HomeAssistant, env: Any) -> None:
    """No export while a run holds the lock."""

    loaded = await async_setup_fake_entry(hass, token_exp=int(NOW.timestamp()) + 9e6)
    async with loaded.coordinator.run_lock:
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "debug_export",
                {"config_entry_id": loaded.entry.entry_id},
                blocking=True,
                return_response=True,
            )


def test_pseudonyms_stable_and_typed() -> None:
    """Same original, same synthetic value; str stays str."""

    p = Pseudonyms()
    assert p.get("mp", 1234567) == 3001
    assert p.get("mp", 7654321) == 3002
    assert p.get("mp", "1234567") == "3001"
    assert p.get("location", "abc") == "7001"
    assert p.number(MP_NUMBERS[0]) == "AT" + "1".zfill(31)
    assert p.get("member", None) is None


def test_redact_export_metering_points_fixture() -> None:
    """Every sensitive field of the fixture goes; the shape stays parseable."""

    raw = load_fixture("metering_point_two_locations.json")
    out = redact_export(raw, Pseudonyms(), ["9000001"])
    assert_no_originals(out)
    info = parse_metering_points(out)
    assert info.locations
    assert all(not loc.label or "Example" not in loc.label for loc in info.locations)


def test_key_presence() -> None:
    """Point counts and missing totals / flags per direction."""

    raw = load_fixture("analysis_hour_normal_beg.json")
    full = key_presence(raw)
    assert full is not None
    assert full["Consumption"]["missing"] == []
    raw.pop("totalGenerationToCommunity")
    raw["generationDataPoints"] = []
    part = key_presence(raw)
    assert part is not None
    assert part["Generation"] == {
        "points": 0,
        "missing": ["totalGenerationToCommunity"],
    }
    assert key_presence([]) is None


def test_window_limits() -> None:
    """1..31 days, ending today."""

    today = date(2026, 6, 8)
    assert window(today, 1) == (today, today)
    assert window(today, 0) == (today, today)
    assert window(today, 99)[0] == date(2026, 5, 9)


async def test_export_stops_on_announced_maintenance(
    hass: HomeAssistant, env: Any
) -> None:
    """An active maintenance window stops the export; no file."""

    loaded = await async_setup_fake_entry(hass, token_exp=int(NOW.timestamp()) + 9e6)
    loaded.client.maintenance_raw = [
        {"start": "2026-06-08T03:00:00Z", "end": "2026-06-08T05:00:00Z"}
    ]
    with pytest.raises(HomeAssistantError, match="maintenance"):
        await hass.services.async_call(
            DOMAIN,
            "debug_export",
            {"config_entry_id": loaded.entry.entry_id},
            blocking=True,
            return_response=True,
        )
    assert loaded.client.raw_sink is None
    assert not [c for c, *_ in loaded.client.calls if c == "get_analysis"]
