"""Comparison sources: the user's own feed-in, grid and PV statistics (issue #50).

Optional energy statistics from the user's live meter or inverter, picked in
the options flow. Later features (summary sensors, the generated dashboard,
live supplier statistics) compare them with the portal data.

Defaults come from the Energy dashboard preferences, which are only read,
never written (ADR 0011).
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any, Final

from homeassistant.components.energy.data import async_get_manager
from homeassistant.components.recorder.statistics import async_list_statistic_ids
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    CONF_SOURCE_FEED_IN,
    CONF_SOURCE_GRID_CONSUMPTION,
    CONF_SOURCE_PV,
    DOMAIN,
    SOURCE_KEYS,
)

_LOGGER = logging.getLogger(__name__)

# Errors of the options step, keyed by field (strings.json "options.error").
ERR_NOT_STATISTIC: Final = "source_not_statistic"
ERR_NOT_ENERGY: Final = "source_not_energy"
ERR_OWN_STATISTIC: Final = "source_own_statistic"
ERR_DUPLICATE: Final = "source_duplicate"


def configured_sources(entry: ConfigEntry) -> dict[str, str | None]:
    """Return the entry's comparison sources (``None`` where none is set)."""

    return {key: entry.options.get(key) or None for key in SOURCE_KEYS}


def sources_configured(options: Mapping[str, Any]) -> bool:
    """Whether the sources step was ever saved (then defaults don't apply)."""

    return any(key in options for key in SOURCE_KEYS)


def energy_defaults(prefs: Mapping[str, Any] | None) -> dict[str, str | None]:
    """Pick defaults from Energy dashboard preferences.

    First grid source with an export statistic for feed-in, first with an
    import statistic for grid consumption, first solar source for PV. Reads
    the unified grid format and, defensively, the older ``flow_from`` /
    ``flow_to`` lists.
    """

    found: dict[str, str | None] = dict.fromkeys(SOURCE_KEYS)
    sources = (prefs or {}).get("energy_sources") or []
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        kind = source.get("type")
        if kind == "grid":
            _set_once(found, CONF_SOURCE_FEED_IN, _grid_stat(source, "to"))
            _set_once(found, CONF_SOURCE_GRID_CONSUMPTION, _grid_stat(source, "from"))
        elif kind == "solar":
            _set_once(found, CONF_SOURCE_PV, source.get("stat_energy_from"))
    return found


def _grid_stat(source: Mapping[str, Any], direction: str) -> str | None:
    stat = source.get(f"stat_energy_{direction}")
    if stat:
        return str(stat)
    flows = source.get(f"flow_{direction}") or []
    for flow in flows:
        if isinstance(flow, Mapping) and flow.get(f"stat_energy_{direction}"):
            return str(flow[f"stat_energy_{direction}"])
    return None


def _set_once(found: dict[str, str | None], key: str, value: Any) -> None:
    if found[key] is None and value:
        found[key] = str(value)


async def async_energy_defaults(hass: HomeAssistant) -> dict[str, str | None]:
    """Defaults from the Energy preferences; empty if they can't be read."""

    try:
        manager = await async_get_manager(hass)
    except Exception:
        _LOGGER.debug("Energy preferences not readable", exc_info=True)
        return dict.fromkeys(SOURCE_KEYS)
    return energy_defaults(manager.data)


async def async_validate_sources(
    hass: HomeAssistant, values: Mapping[str, str | None]
) -> dict[str, str]:
    """Check each set source: a sum statistic in an energy unit, not our own.

    Returns errors keyed by field; empty when everything is valid.
    """

    errors: dict[str, str] = {}
    picked = {key: value for key, value in values.items() if value}
    seen: dict[str, str] = {}
    for key, stat_id in picked.items():
        if stat_id.startswith(f"{DOMAIN}:"):
            errors[key] = ERR_OWN_STATISTIC
        elif stat_id in seen:
            errors[key] = ERR_DUPLICATE
        seen.setdefault(stat_id, key)

    to_check = {stat_id for key, stat_id in picked.items() if key not in errors}
    if not to_check:
        return errors
    listed = await async_list_statistic_ids(hass, statistic_ids=to_check)
    meta = {str(item["statistic_id"]): item for item in listed}
    for key, stat_id in picked.items():
        if key in errors:
            continue
        item = meta.get(stat_id)
        if item is None or not item.get("has_sum"):
            errors[key] = ERR_NOT_STATISTIC
        elif item.get("statistics_unit_of_measurement") not in (
            EnergyConverter.VALID_UNITS
        ):
            errors[key] = ERR_NOT_ENERGY
    return errors
