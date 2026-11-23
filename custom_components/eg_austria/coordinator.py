"""Coordinator, scheduler and persistent store (spec 03, ADR 0004/0006/0007).

One ``EgAustriaCoordinator`` per config entry. It is a ``DataUpdateCoordinator``
without an update interval: runs start from a daily time listener, once at
startup when the last success is old, from a one-shot listener after a
maintenance window, and from the ``refetch`` service. ``data`` holds the
``RunSummary`` of the last finished run.

Hooks for S6 (repairs, diagnostic sensors, diagnostics), see also
``docs/spec/03-coordinator-schedule.md`` "Hooks":

* ``coordinator.data`` (``RunSummary``) and ``coordinator.state``
  (``StoreData``) are updated after every run; ``async_add_listener()``
  callbacks run then and after ``release_held``;
* ``RunSummary.issues`` (``IssueHints``) says per repair whether this run
  raises it (True), clears it (False) or says nothing (None);
* auth failures start the core reauth flow here; the other repairs are
  raised and cleared by ``repairs.py`` (S6) from these hooks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
import random
import re
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_time_change,
)
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COMMUNITIES,
    CONF_DAILY_WINDOW_DAYS,
    CONF_IMPORT_HISTORY,
    CONF_LOCATION_ID,
    CONF_LONG_REFETCH_MONTHS,
    CONF_MAX_HOURLY_KWH,
    CONF_MEMBER_NUMBER,
    CONF_MP_IDS,
    CONF_RUN_MINUTE,
    CONF_SITE_SLUG,
    CONF_SUPPLIER_PRICE,
    CONF_SURPLUS_PRICE,
    CONF_TOKEN_EXP,
    DEFAULT_DAILY_WINDOW_DAYS,
    DEFAULT_IMPORT_HISTORY,
    DEFAULT_LONG_REFETCH_MONTHS,
    DEFAULT_MAX_HOURLY_KWH,
    DOMAIN,
)
from .exceptions import (
    ApiChangedError,
    AuthError,
    EgAustriaError,
    PortalUnavailableError,
    RateLimitedError,
    SchemaError,
    StaleClientError,
)
from .importer import (
    COMPLETENESS_MONTHS,
    CommunityConf,
    EntrySetup,
    RunAborted,
    RunCancelled,
    RunStatus,
    RunType,
    SiteInfo,
    add_months,
    async_fetch_completeness,
    async_import,
    complete_until,
    daily_window_start,
    days_between,
    local_today,
    site_info,
)
from .models import Maintenance, PortalVersion
from .prices import PriceSource
from .redact import redact_text
from .settlement import (
    build_settlements,
    changed_months,
    diagnostics as settlement_diagnostics,
    settled_months,
    settled_window_start,
)
from .snapshot import run_snapshot, setup_shape, slug_map
from .sources import configured_sources
from .statistics import Quantity
from .summary import (
    CommunityName,
    MoneyDef,
    Period,
    PeriodSummary,
    SummaryDef,
    async_compute,
    build_money_defs,
    build_summary_defs,
    meter_data_until,
)
from .validation import ValidationResult, schema_failure
from .zero import rates_from_store, rates_to_store

_LOGGER = logging.getLogger(__name__)

STORE_VERSION: Final = 1
CATCHUP_AFTER: Final = timedelta(hours=26)  # ADR 0004
# No second startup run within this time of the last attempt (restart loops).
CATCHUP_MIN_ATTEMPT_AGE: Final = timedelta(hours=1)
STARTUP_DELAY: Final = (60, 300)  # seconds, random within; never at boot
MAINTENANCE_MARGIN: Final = timedelta(minutes=30)
LONG_RUN_DAY: Final = 3  # first daily run on or after the 3rd of the month
UNREACHABLE_DAYS: Final = 3
UNLOAD_TIMEOUT: Final = 60.0
RATE_LIMIT_SPACING_FACTOR: Final = 2.0


# ---------------------------------------------------------------------------
# Persistent state


def _dt(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


def _d(value: Any) -> date | None:
    return dt_util.parse_date(value) if isinstance(value, str) else None


def _status(value: Any) -> RunStatus | None:
    try:
        return RunStatus(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _text(value: Any) -> str | None:
    return value[:500] if isinstance(value, str) and value else None


def _days_map(value: Any) -> dict[str, list[date]]:
    out: dict[str, list[date]] = {}
    if isinstance(value, dict):
        for key, days in value.items():
            parsed = sorted({d for d in map(_d, days or []) if d is not None})
            if parsed:
                out[str(key)] = parsed
    return out


_MONTH_KEY = re.compile(r"^\d{4}-\d{2}$")


def _months_map(value: Any) -> dict[str, list[str]]:
    """``{"<slug>/<direction>": ["YYYY-MM", ...]}``; anything else is dropped."""

    out: dict[str, list[str]] = {}
    if isinstance(value, dict):
        for key, months in value.items():
            if not isinstance(months, list):
                continue
            parsed = sorted(
                {m for m in months if isinstance(m, str) and _MONTH_KEY.match(m)}
            )
            if parsed:
                out[str(key)] = parsed
    return out


@dataclass(slots=True)
class StoreData:
    """The persistent state of spec 03 (dates and counters only, no values)."""

    last_success: datetime | None = None
    last_attempt: datetime | None = None
    last_long_success: datetime | None = None
    portal_git_commit: str | None = None
    consecutive_failure_days: int = 0
    last_failure_day: date | None = None
    held_days: dict[str, list[date]] = field(default_factory=dict)
    released: dict[str, list[date]] = field(default_factory=dict)
    complete_until: date | None = None
    meter_until: date | None = None  # issue #51: meter side, may run ahead
    initial_done: bool = False
    rate_limited: bool = False
    last_run_status: RunStatus | None = None
    # Redacted error of the last finished run (issue #78), for diagnostics
    # after a restart; None when that run had none.
    last_run_error: str | None = None
    # Value-free snapshots for diagnostics after a restart (issue #78,
    # snapshot.py): last run section, setup shape, response shapes.
    last_run: dict[str, Any] | None = None
    setup: dict[str, Any] | None = None
    schema_fingerprints: dict[str, Any] = field(default_factory=dict)
    # Issue #74: value-free digest per "<slug>/<direction>/<YYYY-MM>" of the
    # settled amount, to re-fetch a month whose settlement changed.
    settlement_digests: dict[str, str] = field(default_factory=dict)
    # Issue #89: latest derived Zero rate per "<slug>/<direction>".
    zero_rates: dict[str, Any] = field(default_factory=dict)
    # Issue #75: settled months ("YYYY-MM") per "<slug>/<direction>".
    settled_months: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> StoreData:
        """Read the stored JSON; unknown or broken values fall back to defaults."""

        raw = raw or {}
        failures = raw.get("consecutive_failure_days")
        return cls(
            last_success=_dt(raw.get("last_success")),
            last_attempt=_dt(raw.get("last_attempt")),
            last_long_success=_dt(raw.get("last_long_success")),
            portal_git_commit=raw.get("portal_git_commit") or None,
            consecutive_failure_days=failures if isinstance(failures, int) else 0,
            last_failure_day=_d(raw.get("last_failure_day")),
            held_days=_days_map(raw.get("held_days")),
            released=_days_map(raw.get("released")),
            complete_until=_d(raw.get("complete_until")),
            meter_until=_d(raw.get("meter_until")),
            initial_done=bool(raw.get("initial_done")),
            rate_limited=bool(raw.get("rate_limited")),
            last_run_status=_status(raw.get("last_run_status")),
            last_run_error=_text(raw.get("last_run_error")),
            last_run=_mapping(raw.get("last_run")),
            setup=_mapping(raw.get("setup")),
            schema_fingerprints=_mapping(raw.get("schema_fingerprints")) or {},
            settlement_digests={
                str(k): str(v)
                for k, v in (_mapping(raw.get("settlement_digests")) or {}).items()
            },
            zero_rates=rates_to_store(rates_from_store(raw.get("zero_rates"))),
            settled_months=_months_map(raw.get("settled_months")),
        )

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form (also used by diagnostics in S6)."""

        def iso(value: date | None) -> str | None:
            return value.isoformat() if value is not None else None

        def days(m: dict[str, list[date]]) -> dict[str, list[str]]:
            return {k: [d.isoformat() for d in v] for k, v in sorted(m.items()) if v}

        return {
            "last_success": iso(self.last_success),
            "last_attempt": iso(self.last_attempt),
            "last_long_success": iso(self.last_long_success),
            "portal_git_commit": self.portal_git_commit,
            "consecutive_failure_days": self.consecutive_failure_days,
            "last_failure_day": iso(self.last_failure_day),
            "held_days": days(self.held_days),
            "released": days(self.released),
            "complete_until": iso(self.complete_until),
            "meter_until": iso(self.meter_until),
            "initial_done": self.initial_done,
            "rate_limited": self.rate_limited,
            "last_run_status": (
                self.last_run_status.value if self.last_run_status else None
            ),
            "last_run_error": self.last_run_error,
            "last_run": self.last_run,
            "setup": self.setup,
            "schema_fingerprints": dict(self.schema_fingerprints),
            "settlement_digests": dict(self.settlement_digests),
            "zero_rates": dict(self.zero_rates),
            "settled_months": {k: list(v) for k, v in self.settled_months.items()},
        }


def entry_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """``eg_austria.<entry_id>`` in ``.storage``."""

    return Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}")


# ---------------------------------------------------------------------------
# Run summary (coordinator data)


@dataclass(frozen=True, slots=True)
class IssueHints:
    """What one run says about each repair of spec 06.

    ``True``: raise, ``False``: clear, ``None``: this run says nothing (for
    example a skipped or unreachable run says nothing about ``api_changed``).
    """

    api_changed: bool | None = None
    implausible_data: bool | None = None
    portal_unreachable: bool | None = None
    price_missing: bool | None = None
    supplier_price_missing: bool | None = None  # issue #73


@dataclass(slots=True)
class RunSummary:
    """Outcome of one run; read by diagnostic entities and diagnostics (S6).

    No energy values: counts, dates, rule ids and statistic keys only.
    """

    run_type: RunType
    status: RunStatus
    started: datetime
    finished: datetime
    window: tuple[date, date] | None = None
    chunks: int = 0
    requests: int = 0
    portal_git_commit: str | None = None
    portal_version_changed: bool = False
    maintenance: list[Maintenance] = field(default_factory=list)
    validation: ValidationResult | None = None
    written_rows: dict[str, int] = field(default_factory=dict)
    kept_stored_hours: dict[str, int] = field(default_factory=dict)
    blocked: dict[str, list[date]] = field(default_factory=dict)
    error: str | None = None  # redacted message
    stale_client: bool = False  # api_changed came from a "portal updated" 400
    rescheduled_for: datetime | None = None  # after maintenance
    price_missing_from: date | None = None  # first overdue day without a price
    supplier_price_missing_from: date | None = None  # the same, supplier price
    settlement: dict[str, Any] | None = None  # value-free summary (issue #74)
    issues: IssueHints = field(default_factory=IssueHints)

    @property
    def duration(self) -> float:
        """Seconds the run took."""

        return (self.finished - self.started).total_seconds()

    @property
    def success(self) -> bool:
        """Return True when the run wrote (``ok`` or ``partial``)."""

        return self.status in (RunStatus.OK, RunStatus.PARTIAL)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """A requested run; ``start``/``end`` only for manual runs."""

    run_type: RunType
    start: date | None = None
    end: date | None = None


# Translation keys for statistic display names (``selector.statistic_name``).
_LABEL_KEYS: Final = {
    **{f"site.{q}": f"site_{q}" for q in Quantity},
    **{f"community.{q}": f"community_{q}" for q in Quantity},
}


# ---------------------------------------------------------------------------
# Coordinator


type EgAustriaConfigEntry = ConfigEntry[EgAustriaCoordinator]


class EgAustriaCoordinator(DataUpdateCoordinator[RunSummary | None]):
    """Schedules and runs imports for one config entry."""

    config_entry: EgAustriaConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EgAustriaConfigEntry,
        client: Any,
        store: Store[dict[str, Any]],
    ) -> None:
        """Set up; nothing is scheduled until ``async_start()``."""

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.entry_id}",
            update_interval=None,
        )
        self.client = client
        self._store = store
        self.state = StoreData()
        self._loaded = False
        self._lock = asyncio.Lock()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._maintenance_unsub: CALLBACK_TYPE | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._base_interval = getattr(client, "min_interval", None)
        self.labels: dict[str, str] = {}
        self.site_label: str | None = None
        # Last portal answers kept for diagnostics (S6); no energy values.
        self.portal_version: PortalVersion | None = None
        self.site: SiteInfo | None = None
        # Log-once state for an unreachable portal (issue #36).
        self._portal_lost = False
        # Summary values (ADR 0014); computed after startup and every run.
        self.summary_defs = self._summary_defs()
        self.money_defs = self._money_defs()  # issue #75
        self.summary: dict[Period, PeriodSummary] = {}

    # ------------------------------------------------------------------
    # Lifecycle

    async def async_load(self) -> None:
        """Load the store (before the platforms, so sensors start with it, #26)."""

        self.state = StoreData.from_dict(await self._store.async_load())
        self._loaded = True

    async def async_start(self) -> None:
        """Register the daily listener and the startup run (store loaded first)."""

        if not self._loaded:
            await self.async_load()
        minute = int(self.config_entry.data.get(CONF_RUN_MINUTE, 240))
        self._unsubs.append(
            async_track_time_change(
                self.hass,
                self._async_daily_time,
                hour=minute // 60,
                minute=minute % 60,
                second=0,
            )
        )
        self._unsubs.append(async_at_started(self.hass, self._async_started))

    async def async_stop(self) -> None:
        """Cancel listeners; let a running import stop at its next request."""

        self._stopping = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._maintenance_unsub is not None:
            self._maintenance_unsub()
            self._maintenance_unsub = None
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), UNLOAD_TIMEOUT)
            except TimeoutError:
                task.cancel()

    async def _async_update_data(self) -> RunSummary | None:
        """Never fetch on a generic refresh; runs are scheduled (spec 03)."""

        return self.data

    # ------------------------------------------------------------------
    # Triggers

    @property
    def run_lock(self) -> asyncio.Lock:
        """The lock runs hold; the debug export takes it too (issue #84)."""

        return self._lock

    def entry_setup(self) -> EntrySetup:
        """Return what a run needs from the entry (also for the debug export)."""

        return self._entry_setup()

    @property
    def running(self) -> bool:
        """Return True while a run holds the lock."""

        return self._lock.locked()

    @callback
    def _async_started(self, _hass: HomeAssistant) -> None:
        """After Home Assistant started: maybe one delayed catch-up run."""

        self.config_entry.async_create_background_task(
            self.hass, self.async_refresh_summary(), f"{DOMAIN} summary"
        )
        run_type = self.startup_run_type(dt_util.utcnow())
        if run_type is None:
            return
        delay = random.uniform(*STARTUP_DELAY)  # noqa: S311 - jitter only
        _LOGGER.debug("Startup %s run in %.0f s", run_type, delay)

        @callback
        def _fire(_now: datetime) -> None:
            self.async_trigger(RunRequest(run_type))

        self._unsubs.append(async_call_later(self.hass, delay, _fire))

    def startup_run_type(self, now: datetime) -> RunType | None:
        """Return the startup run: initial import, catch-up or none (spec 03).

        At most one run per startup, and none if a run was attempted within
        the last hour, so restart loops don't turn into request storms.
        """

        attempt = self.state.last_attempt
        if attempt is not None and now - attempt < CATCHUP_MIN_ATTEMPT_AGE:
            return None
        if self._initial_pending():
            return RunType.INITIAL
        success = self.state.last_success
        if success is None or now - success > CATCHUP_AFTER:
            return self.scheduled_run_type(now)
        return None

    def scheduled_run_type(self, now: datetime) -> RunType:
        """Type of a scheduled run: initial until done, else long or daily."""

        if self._initial_pending():
            return RunType.INITIAL
        today = local_today(now)
        long_done = self.state.last_long_success
        if today.day >= LONG_RUN_DAY and (
            long_done is None
            or (local_today(long_done).year, local_today(long_done).month)
            != (today.year, today.month)
        ):
            return RunType.LONG
        return RunType.DAILY

    def _initial_pending(self) -> bool:
        return not self.state.initial_done and bool(self._options[CONF_IMPORT_HISTORY])

    @callback
    def _async_daily_time(self, now: datetime) -> None:
        self.async_trigger(RunRequest(self.scheduled_run_type(dt_util.utcnow())))

    @callback
    def async_trigger(self, request: RunRequest) -> bool:
        """Start a run in the background; False if one is running already."""

        if self._stopping:
            return False
        if self.running or (self._task is not None and not self._task.done()):
            _LOGGER.debug("Run %s dropped: another run is in progress", request)
            return False
        self._task = self.config_entry.async_create_background_task(
            self.hass, self._async_run_task(request), f"{DOMAIN} {request.run_type} run"
        )
        return True

    async def _async_run_task(self, request: RunRequest) -> None:
        """Run in the background task; the summary is stored, not returned."""

        await self.async_run(request)

    # ------------------------------------------------------------------
    # Services

    @callback
    def async_release(self, statistic_key: str | None, day: date | None) -> int:
        """Release held days (``release_held``); returns how many were released."""

        count = 0
        for key in list(self.state.held_days):
            if statistic_key is not None and key != statistic_key:
                continue
            days = self.state.held_days[key]
            chosen = [d for d in days if day is None or d == day]
            if not chosen:
                continue
            count += len(chosen)
            rest = [d for d in days if d not in chosen]
            if rest:
                self.state.held_days[key] = rest
            else:
                del self.state.held_days[key]
            released = set(self.state.released.get(key, [])) | set(chosen)
            self.state.released[key] = sorted(released)
        if count:
            self._async_save()
            self.async_update_listeners()
        return count

    # ------------------------------------------------------------------
    # Run

    @property
    def _options(self) -> dict[str, Any]:
        defaults = {
            CONF_MAX_HOURLY_KWH: DEFAULT_MAX_HOURLY_KWH,
            CONF_DAILY_WINDOW_DAYS: DEFAULT_DAILY_WINDOW_DAYS,
            CONF_LONG_REFETCH_MONTHS: DEFAULT_LONG_REFETCH_MONTHS,
            CONF_IMPORT_HISTORY: DEFAULT_IMPORT_HISTORY,
        }
        return defaults | dict(self.config_entry.options)

    def _entry_setup(self) -> EntrySetup:
        data = self.config_entry.data
        labels = {k: self.labels[v] for k, v in _LABEL_KEYS.items() if v in self.labels}
        return EntrySetup(
            member=str(data[CONF_MEMBER_NUMBER]),
            location_id=str(data[CONF_LOCATION_ID]),
            site_slug=data[CONF_SITE_SLUG],
            mp_ids=tuple(int(i) for i in data[CONF_MP_IDS]),
            communities=tuple(
                CommunityConf(int(c["id"]), c["slug"], c.get("name") or c["slug"])
                for c in data[CONF_COMMUNITIES]
            ),
            max_hourly_kwh=float(self._options[CONF_MAX_HOURLY_KWH]),
            site_label=self.site_label,
            labels=labels,
            surplus_price=PriceSource.from_options(
                self.config_entry.options.get(CONF_SURPLUS_PRICE)
            ),
            supplier_price=PriceSource.from_options(
                self.config_entry.options.get(CONF_SUPPLIER_PRICE)
            ),
        )

    def _summary_defs(self) -> list[SummaryDef]:
        data = self.config_entry.data
        return build_summary_defs(
            data[CONF_SITE_SLUG],
            [
                CommunityName(c["slug"], c.get("name") or c["slug"])
                for c in data[CONF_COMMUNITIES]
            ],
            configured_sources(self.config_entry),
        )

    def _money_defs(self) -> list[MoneyDef]:
        data = self.config_entry.data
        options = self.config_entry.options
        return build_money_defs(
            data[CONF_SITE_SLUG],
            [
                CommunityName(c["slug"], c.get("name") or c["slug"])
                for c in data[CONF_COMMUNITIES]
            ],
            surplus=PriceSource.from_options(options.get(CONF_SURPLUS_PRICE))
            is not None,
            supplier=PriceSource.from_options(options.get(CONF_SUPPLIER_PRICE))
            is not None,
        )

    async def async_refresh_summary(self, *, wait_for_recorder: bool = False) -> None:
        """Recompute the summary values (ADR 0014) and update the sensors.

        After a run, waits until the recorder has written the imported rows.
        A failure keeps the previous values; it never fails a run.
        """

        try:
            if wait_for_recorder:
                await get_instance(self.hass).async_block_till_done()
            self.summary = await async_compute(
                self.hass,
                self.summary_defs,
                local_today(dt_util.utcnow()),
                self.state.complete_until,
                self.money_defs,
                self.state.settled_months,
            )
        except Exception:
            _LOGGER.warning("Summary values could not be computed", exc_info=True)
            return
        # Own signal, not async_update_listeners(): a coordinator update
        # without a new run means "held days released" to the repairs.
        async_dispatcher_send(self.hass, self.summary_signal)

    @property
    def summary_signal(self) -> str:
        """Dispatcher signal sent when the summary values change."""

        return f"{DOMAIN}_{self.config_entry.entry_id}_summary"

    def _check_stop(self) -> None:
        if self._stopping:
            raise RunCancelled

    def _sensitive(self) -> list[object]:
        return self._entry_setup().sensitive()

    def _token_expired(self, now: datetime) -> bool:
        exp = self.config_entry.data.get(CONF_TOKEN_EXP)
        return isinstance(exp, int | float) and now.timestamp() >= exp

    async def async_run(self, request: RunRequest) -> RunSummary | None:
        """Run once (one at a time); store and publish the summary.

        Returns ``None`` when the trigger was dropped (another run active) or
        the entry unloaded during the run.
        """

        if self._lock.locked():
            _LOGGER.debug("Run %s dropped: another run is in progress", request)
            return None
        async with self._lock:
            started = dt_util.utcnow()
            self.state.last_attempt = started
            requests_before = getattr(self.client, "request_count", 0)
            if self.state.rate_limited and self._base_interval is not None:
                self.client.min_interval = (
                    self._base_interval * RATE_LIMIT_SPACING_FACTOR
                )
            summary = RunSummary(request.run_type, RunStatus.OK, started, started)
            try:
                try:
                    await self._async_execute(request, summary)
                finally:
                    if self._base_interval is not None:
                        self.client.min_interval = self._base_interval
            except RunCancelled:
                _LOGGER.debug("Run %s stopped: entry unloading", request.run_type)
                return None
            except ConfigEntryAuthFailed:
                summary.status = RunStatus.AUTH_FAILED
                summary.error = "authentication failed"
            summary.finished = dt_util.utcnow()
            summary.requests = (
                getattr(self.client, "request_count", 0) - requests_before
            )
            self._finish(summary)
        self.async_set_updated_data(summary)
        if summary.success:
            await self.async_refresh_summary(wait_for_recorder=True)
        if summary.status is RunStatus.AUTH_FAILED:
            # Same effect as raising ConfigEntryAuthFailed from a refresh.
            self.config_entry.async_start_reauth(self.hass)
        return summary

    async def _async_execute(self, request: RunRequest, summary: RunSummary) -> None:
        """Run the sequence of spec 03 and fill ``summary`` in place."""

        now = dt_util.utcnow()
        if self._token_expired(now):
            # ADR 0007: past the stored expiry, no request is made.
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="token_expired"
            )
        setup = self._entry_setup()
        try:
            await self._async_execute_inner(request, summary, setup, now)
        except AuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except RunAborted as err:
            summary.status = RunStatus.API_CHANGED
            summary.validation = err.validation
            summary.error = "; ".join(f.message for f in err.validation.failures)[:500]
            summary.issues = IssueHints(api_changed=True)
        except SchemaError as err:
            summary.status = RunStatus.API_CHANGED
            summary.validation = schema_failure(err, sensitive=setup.sensitive())
            summary.error = self._redact(err)
            summary.issues = IssueHints(api_changed=True)
        except ApiChangedError as err:
            summary.status = RunStatus.API_CHANGED
            summary.error = self._redact(err)
            summary.stale_client = isinstance(err, StaleClientError)
            summary.issues = IssueHints(api_changed=True)
        except RateLimitedError as err:
            summary.status = RunStatus.RATE_LIMITED
            summary.error = self._redact(err)
        except PortalUnavailableError as err:
            summary.status = RunStatus.UNAVAILABLE
            summary.error = self._redact(err)
        except EgAustriaError as err:  # pragma: no cover - all subclasses above
            summary.status = RunStatus.UNAVAILABLE
            summary.error = self._redact(err)

    async def _async_execute_inner(
        self,
        request: RunRequest,
        summary: RunSummary,
        setup: EntrySetup,
        now: datetime,
    ) -> None:
        client = self.client
        today = local_today(now)

        # 1. Maintenance.
        self._check_stop()
        windows = await client.get_maintenance()
        summary.maintenance = list(windows)
        margin = MAINTENANCE_MARGIN.total_seconds()
        active = [w for w in windows if w.active_at(now, margin)]
        if active:
            resume = max(w.end for w in active) + MAINTENANCE_MARGIN
            summary.status = RunStatus.SKIPPED_MAINTENANCE
            summary.rescheduled_for = resume
            self._schedule_after_maintenance(request, resume)
            _LOGGER.info("Portal maintenance announced; run moved to %s", resume)
            return

        # 2. Version.
        self._check_stop()
        version = await client.get_version()
        self.portal_version = version
        summary.portal_git_commit = version.git_commit
        previous = self.state.portal_git_commit
        if previous is not None and previous != version.git_commit:
            summary.portal_version_changed = True
            _LOGGER.info(
                "Portal build changed (%s -> %s)",
                previous[:7],
                version.git_commit[:7],
            )
        self.state.portal_git_commit = version.git_commit

        # Metering points: directions, activation start, community names.
        self._check_stop()
        mp_info = await client.get_metering_points(setup.member)
        self.state.setup = setup_shape(
            mp_info, setup.location_id, setup.mp_ids, slug_map(setup.communities)
        )
        site = site_info(
            mp_info,
            setup.location_id,
            setup.mp_ids,
            setup.community_ids,
        )
        self.site = site
        if site.missing_mp_ids:
            _LOGGER.warning(
                "%d selected metering point(s) not found; reconfigure the entry",
                len(site.missing_mp_ids),
            )
        self._refresh_names(site.names)

        # Settlements (issue #74, ADR 0008): one request per run.
        self._check_stop()
        names = {c.slug: site.names.get(c.id) or c.name for c in setup.communities}
        settlements = build_settlements(
            await client.get_transactions(setup.member), names
        )
        digests = settlements.digests()
        changed = changed_months(self.state.settlement_digests, digests)

        # 3. Window and day completeness.
        start, end = self._window_bounds(request, today, site.earliest_activation)
        # Whole settled months, and months whose settlement changed.
        start = settled_window_start(
            start, end, settlements, changed, site.earliest_activation
        )
        comp_start = start
        if request.run_type is RunType.DAILY:
            # 13 months back, inclusive: one completeness request.
            comp_start = min(
                start, add_months(today, -COMPLETENESS_MONTHS) + timedelta(days=1)
            )
        self._check_stop()
        completeness, validation = await async_fetch_completeness(
            client, setup, comp_start, end
        )
        if request.run_type is RunType.DAILY:
            start = max(
                daily_window_start(
                    today, completeness, int(self._options[CONF_DAILY_WINDOW_DAYS])
                ),
                site.earliest_activation or date.min,
            )
            start = max(
                min(
                    start,
                    settled_window_start(
                        start, end, settlements, changed, site.earliest_activation
                    ),
                ),
                comp_start,
            )
        summary.window = (start, end)

        # 4.-8. Chunks, validation, statistics.
        released = [(k, d) for k, days in self.state.released.items() for d in days]
        result = await async_import(
            self.hass,
            client,
            setup,
            site,
            start,
            end,
            completeness,
            released=released,
            validation=validation,
            check_stop=self._check_stop,
            settlements=settlements,
            zero_rates=rates_from_store(self.state.zero_rates),
        )
        summary.settlement = {
            **settlement_diagnostics(result.settlement, settlements),
            "zero_rates": rates_to_store(result.zero_rates),
            "zero_months": result.zero_months,
        }
        summary.chunks = result.chunks
        summary.validation = result.validation
        summary.blocked = result.blocked
        summary.written_rows = {k: w.rows for k, w in result.writes.items()}
        summary.kept_stored_hours = {
            k: w.kept_stored_hours for k, w in result.writes.items()
        }
        summary.status = RunStatus.PARTIAL if result.partial else RunStatus.OK
        summary.price_missing_from = result.price_missing
        summary.supplier_price_missing_from = result.supplier_price_missing
        summary.issues = IssueHints(
            api_changed=False,
            implausible_data=result.partial,
            portal_unreachable=False,
            price_missing=result.price_missing is not None,
            supplier_price_missing=result.supplier_price_missing is not None,
        )

        # 9. Store bookkeeping.
        self.state.settlement_digests = digests
        self.state.zero_rates = rates_to_store(result.zero_rates)
        self.state.settled_months = settled_months(settlements, result.settlement)
        window = set(days_between(start, end))
        held = {
            k: [d for d in days if d not in window]
            for k, days in self.state.held_days.items()
        }
        for key, days in result.validation.held_by_statistic().items():
            held[key] = sorted(set(held.get(key, [])) | set(days))
        self.state.held_days = {k: v for k, v in held.items() if v}
        self.state.released = {
            k: rest
            for k, days in self.state.released.items()
            if (rest := [d for d in days if d not in window])
        }
        self.state.complete_until = complete_until(
            completeness, comp_start, end, self.state.complete_until
        )
        meter = meter_data_until(
            {k: w.newest_hour for k, w in result.writes.items()}, setup.site_slug
        )
        if meter is not None:
            self.state.meter_until = meter
        if request.run_type in (RunType.INITIAL, RunType.LONG):
            self.state.last_long_success = summary.started
        if request.run_type is RunType.INITIAL:
            self.state.initial_done = True

    def _window_bounds(
        self, request: RunRequest, today: date, activation: date | None
    ) -> tuple[date, date]:
        """Return the requested window (a daily start is refined later)."""

        floor = activation or date.min
        match request.run_type:
            case RunType.MANUAL:
                end = request.end or today
                start = request.start or end - timedelta(
                    days=int(self._options[CONF_DAILY_WINDOW_DAYS])
                )
                return max(start, floor), end
            case RunType.INITIAL:
                return (activation or today), today
            case RunType.LONG:
                months = int(self._options[CONF_LONG_REFETCH_MONTHS])
                return max(add_months(today, -months), floor), today
            case _:
                window = int(self._options[CONF_DAILY_WINDOW_DAYS])
                return max(today - timedelta(days=window), floor), today

    def _finish(self, summary: RunSummary) -> None:
        """Update counters after a run (inside the lock)."""

        today = local_today(summary.started)
        self.state.rate_limited = summary.status is RunStatus.RATE_LIMITED
        self.state.last_run_status = summary.status
        self.state.last_run_error = summary.error[:500] if summary.error else None
        if summary.success:
            self.state.last_success = summary.finished
            self.state.consecutive_failure_days = 0
            self.state.last_failure_day = None
        elif summary.status is RunStatus.UNAVAILABLE:
            if self.state.last_failure_day != today:
                self.state.consecutive_failure_days += 1
                self.state.last_failure_day = today
            summary.issues = IssueHints(
                portal_unreachable=self.state.consecutive_failure_days
                >= UNREACHABLE_DAYS
            )
        level = logging.INFO if summary.success else logging.WARNING
        if summary.status is RunStatus.SKIPPED_MAINTENANCE:
            level = logging.INFO
        if summary.status is RunStatus.UNAVAILABLE:
            # Logged once at info below, not as a warning on every run.
            level = logging.DEBUG
            if not self._portal_lost:
                self._portal_lost = True
                _LOGGER.info("EG Austria portal is unreachable")
        elif summary.success and self._portal_lost:
            self._portal_lost = False
            _LOGGER.info("EG Austria portal is reachable again")
        _LOGGER.log(
            level,
            "%s run %s: window %s, %d chunk(s), %d request(s)%s",
            summary.run_type,
            summary.status,
            "-".join(d.isoformat() for d in summary.window or ()) or "none",
            summary.chunks,
            summary.requests,
            f", {summary.error}" if summary.error else "",
        )
        self.state.last_run = run_snapshot(summary)
        self.state.schema_fingerprints = {
            **self.state.schema_fingerprints,
            **dict(getattr(self.client, "schema_fingerprints", {}) or {}),
        }
        self._async_save()

    @callback
    def _async_save(self) -> None:
        self._store.async_delay_save(self.state.as_dict, 1)

    async def async_flush(self) -> None:
        """Write the store now (tests, unload)."""

        await self._store.async_save(self.state.as_dict())

    def _schedule_after_maintenance(self, request: RunRequest, when: datetime) -> None:
        if self._maintenance_unsub is not None:
            self._maintenance_unsub()

        @callback
        def _fire(_now: datetime) -> None:
            self._maintenance_unsub = None
            self.async_trigger(request)

        self._maintenance_unsub = async_track_point_in_utc_time(self.hass, _fire, when)

    def _refresh_names(self, names: Mapping[int, str]) -> None:
        """Keep ``communities[].name`` equal to the portal name (spec 02)."""

        communities = [dict(c) for c in self.config_entry.data[CONF_COMMUNITIES]]
        changed = False
        for c in communities:
            name = names.get(int(c["id"]))
            if name and name != c.get("name"):
                c["name"] = name
                changed = True
        if changed:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_COMMUNITIES: communities},
            )

    def _redact(self, err: Exception) -> str:
        return redact_text(str(err), self._sensitive())[:500]
