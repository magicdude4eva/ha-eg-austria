"""Service actions of EG Austria (spec 06).

Registered once in ``async_setup``, independent of config entries; each call
names the entry it acts on.
"""

from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Final

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import probatio as vol

from .const import DOMAIN
from .coordinator import EgAustriaConfigEntry, EgAustriaCoordinator, RunRequest
from .export import DEFAULT_DAYS, MAX_DAYS, async_write_export
from .importer import MAX_MANUAL_DAYS, RunType, local_today

_LOGGER = logging.getLogger(__name__)

SERVICE_REFETCH: Final = "refetch"
SERVICE_RELEASE_HELD: Final = "release_held"
SERVICE_DEBUG_EXPORT: Final = "debug_export"
ATTR_DAYS: Final = "days"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_START_DATE: Final = "start_date"
ATTR_END_DATE: Final = "end_date"
ATTR_STATISTIC_KEY: Final = "statistic_key"
ATTR_DATE: Final = "date"

REFETCH_SCHEMA: Final = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_START_DATE): cv.date,
        vol.Optional(ATTR_END_DATE): cv.date,
    }
)
RELEASE_SCHEMA: Final = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_STATISTIC_KEY): cv.string,
        vol.Optional(ATTR_DATE): cv.date,
    }
)

EXPORT_SCHEMA: Final = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_DAYS, default=DEFAULT_DAYS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_DAYS)
        ),
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the service actions of the integration."""

    hass.services.async_register(
        DOMAIN, SERVICE_REFETCH, _async_refetch, schema=REFETCH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RELEASE_HELD, _async_release_held, schema=RELEASE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DEBUG_EXPORT,
        _async_debug_export,
        schema=EXPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _coordinator(hass: HomeAssistant, entry_id: str) -> EgAustriaCoordinator:
    entry: EgAustriaConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_found"
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )
    return entry.runtime_data


async def _async_refetch(call: ServiceCall) -> None:
    """``eg_austria.refetch``: a manual run over a date range (spec 03).

    Without dates the window is the daily window; without ``end_date`` it
    ends today. The run starts in the background.
    """

    coordinator = _coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    today = local_today(dt_util.utcnow())
    end: date = call.data.get(ATTR_END_DATE) or today
    start: date | None = call.data.get(ATTR_START_DATE)
    if end > today or (start is not None and start > end):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_range"
        )
    if start is not None and end - start >= timedelta(days=MAX_MANUAL_DAYS):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="range_too_long",
            translation_placeholders={"days": str(MAX_MANUAL_DAYS)},
        )
    if not coordinator.async_trigger(RunRequest(RunType.MANUAL, start, end)):
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="run_in_progress"
        )


async def _async_release_held(call: ServiceCall) -> None:
    """``eg_austria.release_held``: clear held days so the next run writes them."""

    coordinator = _coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    count = coordinator.async_release(
        call.data.get(ATTR_STATISTIC_KEY), call.data.get(ATTR_DATE)
    )
    _LOGGER.info("Released %d held day(s)", count)


async def _async_debug_export(call: ServiceCall) -> ServiceResponse:
    """``eg_austria.debug_export``: write the redacted export (issue #84)."""

    coordinator = _coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    if coordinator.running:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="run_in_progress"
        )
    result = await async_write_export(call.hass, coordinator, call.data[ATTR_DAYS])
    _LOGGER.info(
        "Debug export written to %s (%d responses, %d failed steps)",
        result["path"],
        result["responses"],
        result["failed_steps"],
    )
    return result
