"""Diagnostic sensors (test plan case 38, spec 06)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.eg_austria.const import DOMAIN
from custom_components.eg_austria.coordinator import RunRequest
from custom_components.eg_austria.exceptions import PortalUnavailableError
from custom_components.eg_austria.importer import RunType

from .conftest import LoadedEntry, async_setup_fake_entry

NOW = datetime(2026, 6, 8, 4, 0, tzinfo=UTC)
EXP = int(NOW.timestamp()) + 200 * 86400
KEYS = (
    "token_expires",
    "data_complete_until",
    "last_import",
    "last_run_status",
    "portal_version",
)


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before ``hass`` (see test_statistics.py)."""


@pytest.fixture(autouse=True)
def no_writes() -> Iterator[AsyncMock]:
    """Skip the statistics write (the test recorder needs ~5 ms per row)."""

    with patch(
        "custom_components.eg_austria.importer.async_import_window",
        AsyncMock(return_value={}),
    ) as mock:
        yield mock


@pytest.fixture
async def loaded(
    hass: HomeAssistant, recorder_mock: Any, freezer: FrozenDateTimeFactory
) -> LoadedEntry:
    """Return a set-up entry with an empty store (no run yet)."""

    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Vienna")
    return await async_setup_fake_entry(
        hass,
        token_exp=EXP,
        # Initial import done long ago; no startup run is due within the test.
        store={"initial_done": True, "last_attempt": NOW.isoformat()},
    )


def entity_ids(hass: HomeAssistant, loaded: LoadedEntry) -> dict[str, str]:
    """Key → entity id via the unique ids."""

    registry = er.async_get(hass)
    out = {}
    for key in KEYS:
        eid = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{loaded.entry.entry_id}_{key}"
        )
        assert eid is not None, key
        out[key] = eid
    return out


async def test_entities_and_device(hass: HomeAssistant, loaded: LoadedEntry) -> None:
    """Diagnostic category, one service device, portal_version disabled."""

    registry = er.async_get(hass)
    ids = entity_ids(hass, loaded)
    device_ids = set()
    for key, eid in ids.items():
        entity = registry.async_get(eid)
        assert entity is not None
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.has_entity_name
        assert entity.translation_key == key
        device_ids.add(entity.device_id)
        if key == "portal_version":
            assert entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            assert hass.states.get(eid) is None
        else:
            assert entity.disabled_by is None
    assert len(device_ids) == 1
    device = dr.async_get(hass).async_get(device_ids.pop() or "")
    assert device is not None
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert device.name == "EG Austria Home"
    assert device.manufacturer == "EG Austria (unofficial)"


async def test_values_before_and_after_run(
    hass: HomeAssistant, loaded: LoadedEntry
) -> None:
    """Token expiry from the entry; the rest from the store and the run."""

    ids = entity_ids(hass, loaded)
    state = hass.states.get(ids["token_expires"])
    assert state is not None
    assert state.state == datetime.fromtimestamp(EXP, UTC).isoformat()
    for key in ("data_complete_until", "last_import", "last_run_status"):
        state = hass.states.get(ids[key])
        assert state is not None
        assert state.state == STATE_UNKNOWN, key

    summary = await loaded.coordinator.async_run(RunRequest(RunType.DAILY))
    await hass.async_block_till_done()
    assert summary is not None
    complete = loaded.coordinator.state.complete_until
    assert complete is not None
    assert hass.states.get(ids["data_complete_until"]).state == complete.isoformat()
    assert hass.states.get(ids["last_import"]).state == NOW.isoformat()
    status = hass.states.get(ids["last_run_status"])
    assert status.state == "ok"
    assert "api_changed" in status.attributes["options"]

    loaded.client.errors["get_maintenance"] = PortalUnavailableError("timeout")
    await loaded.coordinator.async_run(RunRequest(RunType.DAILY))
    await hass.async_block_till_done()
    assert hass.states.get(ids["last_run_status"]).state == "unavailable"
    # last_import keeps the last success.
    assert hass.states.get(ids["last_import"]).state == NOW.isoformat()


async def test_portal_version_value(hass: HomeAssistant, loaded: LoadedEntry) -> None:
    """When enabled it shows the short git commit (value read directly)."""

    from custom_components.eg_austria.sensor import SENSORS, EgDiagnosticSensor

    description = next(d for d in SENSORS if d.key == "portal_version")
    sensor = EgDiagnosticSensor(loaded.coordinator, description)
    assert sensor.native_value is None
    await loaded.coordinator.async_run(RunRequest(RunType.DAILY))
    await hass.async_block_till_done()
    commit = loaded.coordinator.state.portal_git_commit
    assert commit
    assert sensor.native_value == commit[:7]


async def test_stored_values_right_after_setup(
    hass: HomeAssistant, recorder_mock: Any, freezer: FrozenDateTimeFactory
) -> None:
    """After a restart the sensors show the stored dates before any run (#26)."""

    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Vienna")
    last_success = datetime(2026, 6, 7, 3, 30, tzinfo=UTC)
    loaded = await async_setup_fake_entry(
        hass,
        token_exp=EXP,
        store={
            "initial_done": True,
            "last_attempt": NOW.isoformat(),
            "last_success": last_success.isoformat(),
            "complete_until": "2026-06-05",
        },
    )
    ids = entity_ids(hass, loaded)
    assert not loaded.client.calls  # no run: the values come from the store
    assert hass.states.get(ids["data_complete_until"]).state == "2026-06-05"
    assert hass.states.get(ids["last_import"]).state == last_success.isoformat()
    # No run since the restart: the status stays unknown.
    assert hass.states.get(ids["last_run_status"]).state == STATE_UNKNOWN


async def test_run_status_survives_reload(
    hass: HomeAssistant, loaded: LoadedEntry
) -> None:
    """The stored status shows after a reload, before any new run (#29)."""

    ids = entity_ids(hass, loaded)
    loaded.client.errors["get_maintenance"] = PortalUnavailableError("timeout")
    await loaded.coordinator.async_run(RunRequest(RunType.DAILY))
    await loaded.coordinator.async_flush()
    calls = len(loaded.client.calls)
    with (
        patch(
            "custom_components.eg_austria.EgAustriaClient", return_value=loaded.client
        ),
        patch(
            "custom_components.eg_austria.get_portal_session",
            return_value=MagicMock(close=AsyncMock()),
        ),
    ):
        assert await hass.config_entries.async_reload(loaded.entry.entry_id)
        await hass.async_block_till_done()
    assert len(loaded.client.calls) == calls  # no run after the reload
    assert loaded.coordinator.data is None
    assert hass.states.get(ids["last_run_status"]).state == "unavailable"
