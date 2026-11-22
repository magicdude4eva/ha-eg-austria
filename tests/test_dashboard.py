"""Generated dashboard: script registration and websocket config (issue #52)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.eg_austria import dashboard
from custom_components.eg_austria.const import CONF_SOURCE_PV, VERSION
from custom_components.eg_austria.statistics import Quantity, StatisticDef, day_hours

from .conftest import async_setup_fake_entry

NOW = datetime(2026, 6, 8, 4, 0, tzinfo=UTC)
EXP = int(NOW.timestamp()) + 200 * 86400


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before ``hass`` (see test_statistics.py)."""


async def _setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory, **kw: Any) -> Any:
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Vienna")
    return await async_setup_fake_entry(
        hass,
        token_exp=EXP,
        store={"initial_done": True, "last_attempt": NOW.isoformat()},
        **kw,
    )


def test_script_is_shipped() -> None:
    """The strategy script exists and registers the dashboard type."""

    script = Path(dashboard.FRONTEND_DIR) / dashboard.SCRIPT
    source = script.read_text(encoding="utf-8")
    assert "ll-strategy-dashboard-${STRATEGY_TYPE}" in source
    assert 'const STRATEGY_TYPE = "eg-austria";' in source
    assert dashboard.WS_TYPE in source
    assert (
        f"/eg_austria_frontend/{dashboard.SCRIPT}?v={VERSION}" == dashboard.SCRIPT_URL
    )


async def test_setup_without_frontend(hass: HomeAssistant) -> None:
    """No frontend (tests, minimal setups): only the websocket command."""

    with patch.object(dashboard.websocket_api, "async_register_command") as register:
        await dashboard.async_setup_dashboard(hass)
    register.assert_called_once_with(hass, dashboard.websocket_dashboard_config)


async def test_setup_with_frontend(hass: HomeAssistant) -> None:
    """With the frontend: static path for the folder, script loaded as module."""

    hass.config.components.add("frontend")
    http = MagicMock(async_register_static_paths=AsyncMock())
    with (
        patch.object(hass, "http", http),
        patch("homeassistant.components.frontend.add_extra_js_url") as add_js,
    ):
        await dashboard.async_setup_dashboard(hass)
    (paths,) = http.async_register_static_paths.await_args.args
    assert [(p.url_path, p.path, p.cache_headers) for p in paths] == [
        ("/eg_austria_frontend", str(dashboard.FRONTEND_DIR), False)
    ]
    add_js.assert_called_once_with(hass, dashboard.SCRIPT_URL)


def _write(hass: HomeAssistant, statistic_id: str) -> None:
    meta = dict(
        StatisticDef("home_generation", Quantity.GENERATION, "Synthetic").metadata()
    ) | {"statistic_id": statistic_id, "name": statistic_id}
    hour = day_hours(date(2026, 6, 1))[0]
    async_add_external_statistics(
        hass, meta, [{"start": hour, "state": 1.0, "sum": 1.0}]
    )


async def test_websocket_config(
    hass: HomeAssistant,
    recorder_mock: Any,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Existing statistics, sources and enabled sensors of the loaded entry."""

    loaded = await _setup(hass, freezer, options={CONF_SOURCE_PV: "example:pv"})
    for stat_id in (
        "eg_austria:home_generation",
        "eg_austria:home_generation_to_community",
        "eg_austria:home_beg_generation_to_community",
    ):
        _write(hass, stat_id)
    await async_wait_recording_done(hass)
    # A disabled sensor is left out.
    registry = er.async_get(hass)
    disabled = registry.async_get_entity_id(
        "sensor", "eg_austria", f"{loaded.entry.entry_id}_pv_to_surplus_last_month"
    )
    assert disabled is not None
    registry.async_update_entity(disabled, disabled_by=er.RegistryEntryDisabler.USER)

    connection = MagicMock()
    dashboard.websocket_dashboard_config(
        hass, connection, {"id": 7, "type": dashboard.WS_TYPE}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    msg_id, result = connection.send_result.call_args.args
    assert msg_id == 7
    (entry,) = result["entries"]
    assert entry["entry_id"] == loaded.entry.entry_id
    assert entry["title"] == "EG Austria Home"
    assert entry["site"] == "home"
    assert entry["communities"] == [
        {"slug": "beg", "name": "Example BEG"},
        {"slug": "eeg", "name": "Example EEG"},
    ]
    assert entry["statistics"] == {
        "generation": "eg_austria:home_generation",
        "generation_to_community": "eg_austria:home_generation_to_community",
    }
    assert entry["community_statistics"] == {
        "beg": {
            "generation_to_community": "eg_austria:home_beg_generation_to_community"
        },
        "eeg": {},
    }
    assert entry["sources"] == {
        "feed_in": None,
        "grid_consumption": None,
        "pv": "example:pv",
    }
    entities = entry["entities"]
    assert entities["data_complete_until"].startswith("sensor.")
    assert entities["meter_data_until"].startswith("sensor.")
    assert "beg_feed_in_share_month_so_far" in entities
    assert "pv_self_consumed_last_month" in entities
    assert "pv_to_surplus_last_month" not in entities
    assert not any("deviation" in key for key in entities)


async def test_websocket_config_skips_unloaded(
    hass: HomeAssistant,
    recorder_mock: Any,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An unloaded entry isn't listed; no statistics lookup without entries."""

    loaded = await _setup(hass, freezer)
    assert await hass.config_entries.async_unload(loaded.entry.entry_id)
    with patch.object(dashboard, "async_list_statistic_ids") as listing:
        assert await dashboard.async_dashboard_config(hass) == []
    listing.assert_not_called()
