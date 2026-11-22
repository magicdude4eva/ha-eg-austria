"""Comparison sources: Energy defaults and validation (issue #50)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eg_austria import sources
from custom_components.eg_austria.const import (
    CONF_SOURCE_FEED_IN,
    CONF_SOURCE_GRID_CONSUMPTION,
    CONF_SOURCE_PV,
    DOMAIN,
)

EMPTY = {
    CONF_SOURCE_FEED_IN: None,
    CONF_SOURCE_GRID_CONSUMPTION: None,
    CONF_SOURCE_PV: None,
}


@pytest.mark.parametrize("prefs", [None, {}, {"energy_sources": None}])
def test_energy_defaults_empty(prefs: Any) -> None:
    """No preferences, no defaults."""

    assert sources.energy_defaults(prefs) == EMPTY


def test_energy_defaults_first_of_each() -> None:
    """First grid source per direction and first solar source win."""

    prefs = {
        "energy_sources": [
            "not a mapping",
            {"type": "gas", "stat_energy_from": "sensor.example_gas"},
            {
                "type": "grid",
                "stat_energy_from": None,
                "stat_energy_to": "sensor.a_out",
            },
            {"type": "grid", "stat_energy_from": "sensor.b_in", "stat_energy_to": "x"},
            {"type": "solar", "stat_energy_from": "sensor.pv_1"},
            {"type": "solar", "stat_energy_from": "sensor.pv_2"},
        ]
    }
    assert sources.energy_defaults(prefs) == {
        CONF_SOURCE_FEED_IN: "sensor.a_out",
        CONF_SOURCE_GRID_CONSUMPTION: "sensor.b_in",
        CONF_SOURCE_PV: "sensor.pv_1",
    }


def test_energy_defaults_legacy_flows() -> None:
    """The older flow_from / flow_to lists are read too."""

    prefs = {
        "energy_sources": [
            {
                "type": "grid",
                "flow_from": ["junk", {"stat_energy_from": "sensor.old_in"}],
                "flow_to": [
                    {"stat_energy_to": None},
                    {"stat_energy_to": "sensor.old_out"},
                ],
            },
            {"type": "grid", "flow_from": [], "flow_to": None},
        ]
    }
    assert sources.energy_defaults(prefs) == EMPTY | {
        CONF_SOURCE_FEED_IN: "sensor.old_out",
        CONF_SOURCE_GRID_CONSUMPTION: "sensor.old_in",
    }


async def test_async_energy_defaults_reads_manager(hass: HomeAssistant) -> None:
    """The manager's data is used as is."""

    manager = MagicMock(
        data={"energy_sources": [{"type": "solar", "stat_energy_from": "sensor.pv"}]}
    )
    with patch.object(sources, "async_get_manager", AsyncMock(return_value=manager)):
        assert await sources.async_energy_defaults(hass) == EMPTY | {
            CONF_SOURCE_PV: "sensor.pv"
        }


async def test_async_energy_defaults_real_manager(hass: HomeAssistant) -> None:
    """Without stored preferences the real manager gives empty defaults."""

    assert await sources.async_energy_defaults(hass) == EMPTY


def test_configured_sources() -> None:
    """Empty strings and missing keys read as None."""

    entry = MockConfigEntry(
        domain=DOMAIN, options={CONF_SOURCE_FEED_IN: "sensor.out", CONF_SOURCE_PV: ""}
    )
    assert sources.configured_sources(entry) == EMPTY | {
        CONF_SOURCE_FEED_IN: "sensor.out"
    }
    assert sources.sources_configured(entry.options)
    assert not sources.sources_configured({"max_hourly_kwh": 50})


async def test_validate_mixed(hass: HomeAssistant) -> None:
    """Own and duplicate ids are rejected without a recorder lookup."""

    listing = AsyncMock(
        return_value=[
            {
                "statistic_id": "sensor.out",
                "has_sum": True,
                "statistics_unit_of_measurement": "MWh",
            }
        ]
    )
    with patch.object(sources, "async_list_statistic_ids", listing):
        errors = await sources.async_validate_sources(
            hass,
            {
                CONF_SOURCE_FEED_IN: "sensor.out",
                CONF_SOURCE_GRID_CONSUMPTION: "sensor.out",
                CONF_SOURCE_PV: f"{DOMAIN}:home_generation",
            },
        )
    assert errors == {
        CONF_SOURCE_GRID_CONSUMPTION: sources.ERR_DUPLICATE,
        CONF_SOURCE_PV: sources.ERR_OWN_STATISTIC,
    }
    listing.assert_awaited_once_with(hass, statistic_ids={"sensor.out"})


async def test_validate_only_errors_skips_lookup(hass: HomeAssistant) -> None:
    """Nothing left to look up: the recorder isn't asked."""

    listing = AsyncMock()
    with patch.object(sources, "async_list_statistic_ids", listing):
        errors = await sources.async_validate_sources(
            hass,
            {CONF_SOURCE_PV: f"{DOMAIN}:home_generation", CONF_SOURCE_FEED_IN: None},
        )
    assert errors == {CONF_SOURCE_PV: sources.ERR_OWN_STATISTIC}
    listing.assert_not_called()
