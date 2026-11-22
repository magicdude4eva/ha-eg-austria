"""Config entry and device diagnostics (spec 06 "Diagnostics").

Everything goes through ``redact_tree()`` at the end: key-based redaction
with ``TO_REDACT`` plus ``redact_text()`` (tokens, metering point numbers,
emails, the entry's ids) on every string. On top of that the sections are
built from dates, counts, rule ids, statistic keys and slugs only, so no
energy or money value and no free text from the portal is included.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder.db_schema import Statistics, StatisticsMeta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.recorder import get_instance, session_scope
from homeassistant.util import dt as dt_util
from sqlalchemy import func, select

from .const import (
    CONF_COMMUNITIES,
    CONF_LOCATION_ID,
    CONF_MEMBER_NUMBER,
    CONF_MP_IDS,
    CONF_RETIRED_SLUGS,
    CONF_SITE_SLUG,
    CONF_TOKEN,
    CONF_TOKEN_ID,
    CONF_TOKEN_KIND,
)
from .models import Direction
from .redact import redact_tree
from .repairs import token_expiry
from .snapshot import run_snapshot
from .statistics import CommunityRef, build_statistic_defs
from .token import TokenKind, jwt_issued_at

if TYPE_CHECKING:
    from .coordinator import EgAustriaConfigEntry, EgAustriaCoordinator


_SNAPSHOT_KEYS = ("last_run", "setup", "schema_fingerprints")


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _sensitive(entry: ConfigEntry) -> list[object]:
    data = entry.data
    return [
        data.get(CONF_MEMBER_NUMBER),
        data.get(CONF_LOCATION_ID),
        *(data.get(CONF_MP_IDS) or []),
        *(c.get("id") for c in data.get(CONF_COMMUNITIES) or []),
    ]


def _entry_section(
    entry: ConfigEntry, coordinator: EgAustriaCoordinator | None
) -> dict[str, Any]:
    # Ids and display names go via TO_REDACT; the slugs stand in for them.
    data = dict(entry.data)
    site = coordinator.site if coordinator is not None else None
    return {
        "version": f"{entry.version}.{entry.minor_version}",
        "options": dict(entry.options),
        "site_slug": data.get(CONF_SITE_SLUG),
        "community_slugs": [c.get("slug") for c in data.get(CONF_COMMUNITIES) or []],
        "retired_slugs": list(data.get(CONF_RETIRED_SLUGS) or []),
        "metering_points": len(data.get(CONF_MP_IDS) or []),
        "directions": (
            sorted(d.value for d in site.directions) if site is not None else None
        ),
        "missing_metering_points": (
            len(site.missing_mp_ids) if site is not None else None
        ),
        "data": data,  # redacted by key below
    }


def _token_section(entry: ConfigEntry) -> dict[str, Any]:
    """Kind, stored expiry, days left; for a session JWT also ``iat``."""

    kind = entry.data.get(CONF_TOKEN_KIND)
    exp = token_expiry(entry)
    section: dict[str, Any] = {
        "kind": kind,
        "expires": _iso(exp),
        "days_left": (exp - dt_util.utcnow()).days if exp is not None else None,
        # ADR 0007: the token was created by the config flow (id never shown).
        "created_by_flow": bool(entry.data.get(CONF_TOKEN_ID)),
    }
    if kind == TokenKind.SESSION:
        iat = jwt_issued_at(str(entry.data.get(CONF_TOKEN, "")))
        section["issued_at"] = (
            _iso(datetime.fromtimestamp(iat, UTC)) if iat is not None else None
        )
    return section


def _portal_section(coordinator: EgAustriaCoordinator | None) -> dict[str, Any]:
    if coordinator is None:
        return {}
    version = coordinator.portal_version
    summary = coordinator.data
    return {
        "git_commit": version.git_commit if version else None,
        "build_time": version.build_time if version else None,
        "maintenance": [
            {"start": _iso(w.start), "end": _iso(w.end)}
            for w in (summary.maintenance if summary is not None else [])
        ],
    }


def _stored_ranges(
    hass: HomeAssistant, statistic_ids: list[str]
) -> dict[str, dict[str, str | None]]:
    """First and last stored hour per statistic id (no sums, no states)."""

    stmt = (
        select(
            StatisticsMeta.statistic_id,
            func.min(Statistics.start_ts),
            func.max(Statistics.start_ts),
        )
        .join(Statistics, Statistics.metadata_id == StatisticsMeta.id)
        .where(StatisticsMeta.statistic_id.in_(statistic_ids))
        .group_by(StatisticsMeta.statistic_id)
    )
    with session_scope(hass=hass, read_only=True) as session:
        rows = session.execute(stmt).all()
    return {
        sid: {
            "first_hour": _iso(datetime.fromtimestamp(first, UTC)),
            "last_hour": _iso(datetime.fromtimestamp(last, UTC)),
        }
        for sid, first, last in sorted(rows)
    }


async def _statistics_section(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, dict[str, str | None]]:
    slug = entry.data.get(CONF_SITE_SLUG)
    if not slug:
        return {}
    communities = [
        CommunityRef(c["slug"], c["slug"])
        for c in entry.data.get(CONF_COMMUNITIES) or []
        if c.get("slug")
    ]
    ids = [
        d.statistic_id for d in build_statistic_defs(slug, communities, set(Direction))
    ]
    return await get_instance(hass).async_add_executor_job(_stored_ranges, hass, ids)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EgAustriaConfigEntry
) -> dict[str, Any]:
    """Return the redacted diagnostics of one entry."""

    coordinator: EgAustriaCoordinator | None = getattr(entry, "runtime_data", None)
    client = coordinator.client if coordinator is not None else None
    state = coordinator.state if coordinator is not None else None
    store = state.as_dict() if state is not None else None
    if store is not None:
        # Shown as their own sections below, not twice.
        for key in _SNAPSHOT_KEYS:
            store.pop(key, None)
    # Live values from this run, else the snapshots stored by the last run
    # (issue #78: diagnostics taken after a restart still show them).
    live_prints = dict(getattr(client, "schema_fingerprints", {}) or {})
    result = {
        "entry": _entry_section(entry, coordinator),
        "token": _token_section(entry),
        "store": store,
        "last_run": (
            run_snapshot(coordinator.data)
            if coordinator is not None and coordinator.data is not None
            else (state.last_run if state is not None else None)
        ),
        "setup": state.setup if state is not None else None,
        "portal": _portal_section(coordinator),
        "schema_fingerprint": {
            **(state.schema_fingerprints if state is not None else {}),
            **live_prints,
        },
        "statistics": await _statistics_section(hass, entry),
    }
    # Section names are ours (one is called "token"); redact inside them.
    sensitive = _sensitive(entry)
    return {k: redact_tree(v, sensitive) for k, v in result.items()}


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: EgAustriaConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return the entry diagnostics for its one service device.

    Lets the device page offer "Download diagnostics" too; the entry has one
    device, so the content is the same.
    """

    return await async_get_config_entry_diagnostics(hass, entry)
