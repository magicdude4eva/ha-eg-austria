"""Generated "Energy community" dashboard (issues #52 and #61, spike #49).

The integration ships a Lovelace dashboard strategy (``frontend/``): a small
script, served from a static path and loaded on every frontend start with
``add_extra_js_url``. It registers ``custom:eg-austria`` in the *Add
dashboard* dialog. When the dashboard loads, the strategy asks the
``eg_austria/dashboard_config`` websocket command which statistics, sources
and sensors each entry has, and builds its cards from that, so option changes
show on the next load.

The integration never creates or changes dashboards itself; the user adds it
once. Nothing here writes anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from homeassistant.components import websocket_api
from homeassistant.components.recorder.statistics import async_list_statistic_ids
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import (
    async_response,
    websocket_command,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
import probatio as vol

from .const import CONF_COMMUNITIES, CONF_SITE_SLUG, DOMAIN, VERSION
from .sources import configured_sources
from .statistics import Quantity
from .summary import Period

URL_BASE: Final = f"/{DOMAIN}_frontend"
SCRIPT: Final = "eg-austria-dashboard.js"
SCRIPT_URL: Final = f"{URL_BASE}/{SCRIPT}?v={VERSION}"
FRONTEND_DIR: Final = Path(__file__).parent / "frontend"
WS_TYPE: Final = f"{DOMAIN}/dashboard_config"

SITE_QUANTITIES: Final = tuple(Quantity)
COMMUNITY_QUANTITIES: Final = (
    Quantity.CONSUMPTION_FROM_COMMUNITY,
    Quantity.GENERATION_TO_COMMUNITY,
)
DIAGNOSTIC_KEYS: Final = (
    "data_complete_until",
    "meter_data_until",
    "last_import",
    "last_run_status",
    "token_expires",
)


async def async_setup_dashboard(hass: HomeAssistant) -> None:
    """Serve and load the strategy script; register the websocket command.

    The script is only registered when the frontend and http are loaded
    (both are after-dependencies, so they are set up first when present).
    """

    websocket_api.async_register_command(hass, websocket_dashboard_config)
    if "frontend" not in hass.config.components or hass.http is None:
        return
    # Imported here: the frontend component is optional (tests, minimal setups).
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http.server import StaticPathConfig

    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(FRONTEND_DIR), cache_headers=False)]
    )
    add_extra_js_url(hass, SCRIPT_URL)


@websocket_command({vol.Required("type"): WS_TYPE})
@async_response
async def websocket_dashboard_config(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return what the dashboard strategy needs for every loaded entry."""

    connection.send_result(msg["id"], {"entries": await async_dashboard_config(hass)})


async def async_dashboard_config(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Statistics that exist, sources and sensor entity ids per loaded entry."""

    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    candidates = {
        stat_id for entry in entries for stat_id in _statistic_ids(entry.data).values()
    } | {
        stat_id
        for entry in entries
        for community in _community_statistic_ids(entry.data).values()
        for stat_id in community.values()
    }
    existing: set[str] = set()
    if candidates:
        listed = await async_list_statistic_ids(hass, statistic_ids=candidates)
        existing = {str(item["statistic_id"]) for item in listed}
    registry = er.async_get(hass)
    return [_entry_config(entry, existing, registry) for entry in entries]


def _statistic_ids(data: Any) -> dict[str, str]:
    site = data[CONF_SITE_SLUG]
    return {str(q): f"{DOMAIN}:{site}_{q}" for q in SITE_QUANTITIES}


def _community_statistic_ids(data: Any) -> dict[str, dict[str, str]]:
    site = data[CONF_SITE_SLUG]
    return {
        c["slug"]: {
            str(q): f"{DOMAIN}:{site}_{c['slug']}_{q}" for q in COMMUNITY_QUANTITIES
        }
        for c in data[CONF_COMMUNITIES]
    }


@callback
def _entity_ids(
    registry: er.EntityRegistry, entry_id: str, keys: list[str]
) -> dict[str, str]:
    found: dict[str, str] = {}
    for key in keys:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry_id}_{key}")
        if entity_id is None:
            continue
        entity = registry.async_get(entity_id)
        if entity is not None and entity.disabled_by is None:
            found[key] = entity_id
    return found


@callback
def _entry_config(
    entry: Any, existing: set[str], registry: er.EntityRegistry
) -> dict[str, Any]:
    data = entry.data
    coordinator = entry.runtime_data
    summary_keys = [
        f"{d.key}_{period}" for d in coordinator.summary_defs for period in Period
    ]
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "site": data[CONF_SITE_SLUG],
        "communities": [
            {"slug": c["slug"], "name": c.get("name") or c["slug"]}
            for c in data[CONF_COMMUNITIES]
        ],
        "statistics": {
            key: stat_id
            for key, stat_id in _statistic_ids(data).items()
            if stat_id in existing
        },
        "community_statistics": {
            slug: {k: s for k, s in stats.items() if s in existing}
            for slug, stats in _community_statistic_ids(data).items()
        },
        "sources": {
            key.removeprefix("source_"): value
            for key, value in configured_sources(entry).items()
        },
        "entities": _entity_ids(
            registry, entry.entry_id, [*DIAGNOSTIC_KEYS, *summary_keys]
        ),
    }
