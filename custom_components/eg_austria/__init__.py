"""EG Austria energy-community data for Home Assistant (unofficial, read-only).

Setup creates the API client, the persistent store and the coordinator; the
coordinator schedules the imports (spec 03). Repairs and the diagnostic
sensors follow the coordinator (spec 06). The services of spec 06 are
registered once in ``async_setup``, independent of entries.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import EgAustriaClient, get_portal_session
from .const import (
    CONF_MEMBER_NUMBER,
    CONF_TOKEN,
    CONF_TOKEN_EXP,
    CONF_TOKEN_KIND,
    DOMAIN,
)
from .coordinator import (
    MAINTENANCE_MARGIN,
    EgAustriaCoordinator,
    RunRequest,
    entry_store,
)
from .exceptions import (
    ApiChangedError,
    AuthError,
    EgAustriaError,
    PortalUnavailableError,
    RateLimitedError,
)
from .importer import MAX_MANUAL_DAYS, RunType, local_today
from .repairs import EntryRepairs, async_delete_all
from .token import TokenKind

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA: Final = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS: Final = [Platform.SENSOR]

SERVICE_REFETCH: Final = "refetch"
SERVICE_RELEASE_HELD: Final = "release_held"
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
LABEL_PREFIX: Final = f"component.{DOMAIN}.selector.statistic_name.options."
# Upper bound for the setup check (ADR 0013); the client alone may take two
# 60 s attempts.
SETUP_CHECK_TIMEOUT: Final = 30.0

type EgAustriaConfigEntry = ConfigEntry[EgAustriaCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services."""

    hass.services.async_register(
        DOMAIN, SERVICE_REFETCH, _async_refetch, schema=REFETCH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RELEASE_HELD, _async_release_held, schema=RELEASE_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EgAustriaConfigEntry) -> bool:
    """Set up an entry: client, store, coordinator, schedule.

    One request checks the portal and the token (ADR 0013); the import
    itself starts in the background after Home Assistant has started
    (spec 03).
    """

    exp = entry.data.get(CONF_TOKEN_EXP)
    if isinstance(exp, int | float) and dt_util.utcnow().timestamp() >= exp:
        # ADR 0007: past the stored expiry nothing is sent; start reauth.
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="token_expired"
        )

    client = EgAustriaClient(
        get_portal_session(hass),
        entry.data[CONF_TOKEN],
        TokenKind(entry.data[CONF_TOKEN_KIND]),
        token_exp=exp,
    )
    await _async_check_portal(client, str(entry.data[CONF_MEMBER_NUMBER]))
    coordinator = EgAustriaCoordinator(
        hass, entry, client, entry_store(hass, entry.entry_id)
    )
    await _async_load_labels(hass, coordinator)
    # Stored dates first: the sensors read them when they are added (#26).
    await coordinator.async_load()
    entry.runtime_data = coordinator
    entry.async_on_unload(EntryRepairs(hass, entry, coordinator).async_start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EgAustriaConfigEntry) -> bool:
    """Stop the schedule and let a running import stop.

    The portal session stays open: Home Assistant owns it (issue #17).
    """

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = entry.runtime_data
    await coordinator.async_stop()
    await coordinator.async_flush()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the entry's store and issues; statistics stay (ADR 0012)."""

    async_delete_all(hass, entry.entry_id)
    await entry_store(hass, entry.entry_id).async_remove()


async def _async_check_portal(client: EgAustriaClient, member: str) -> None:
    """Test before setup (ADR 0013): one GET of the member's metering points.

    Unreachable, 5xx, 429 or an announced maintenance → ``ConfigEntryNotReady``
    (Home Assistant retries the setup); 401/403 → ``ConfigEntryAuthFailed``
    (reauth). Any other answer (changed API, schema) lets setup go on: the
    run reports it as ``api_changed`` with a repair, which a setup retry
    could not.
    """

    try:
        async with asyncio.timeout(SETUP_CHECK_TIMEOUT):
            try:
                await client.get_metering_points(member)
            except ApiChangedError as err:
                if err.status is not None:
                    raise
                # Non-JSON body: an HTML maintenance page if a window is
                # announced (spec 01 "Errors"), else a changed API.
                now = dt_util.utcnow()
                margin = MAINTENANCE_MARGIN.total_seconds()
                if any(
                    w.active_at(now, margin) for w in await client.get_maintenance()
                ):
                    raise ConfigEntryNotReady(
                        translation_domain=DOMAIN,
                        translation_key="portal_maintenance",
                    ) from err
                raise
    except AuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except (PortalUnavailableError, RateLimitedError, TimeoutError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="portal_unavailable"
        ) from err
    except EgAustriaError:
        # Changed API or schema: the run raises the api_changed repair.
        return


async def _async_load_labels(
    hass: HomeAssistant, coordinator: EgAustriaCoordinator
) -> None:
    """Load the translated statistic labels (spec 04); English if missing."""

    strings = await async_get_translations(
        hass, hass.config.language, "selector", {DOMAIN}
    )
    labels = {
        k.removeprefix(LABEL_PREFIX): v
        for k, v in strings.items()
        if k.startswith(LABEL_PREFIX)
    }
    coordinator.site_label = labels.pop("site", None)
    coordinator.labels = labels


# ---------------------------------------------------------------------------
# Services


def _coordinator(hass: HomeAssistant, entry_id: str) -> EgAustriaCoordinator:
    entry = hass.config_entries.async_get_entry(entry_id)
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
