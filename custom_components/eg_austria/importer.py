"""Fetch, validate and write one run's window (spec 03, ADR 0004 and 0006).

Two layers:

* pure planning helpers: run window, month-aligned chunks, autumn DST days,
  what the selected metering points say about the entry (``SiteInfo``);
* ``async_import()``: the request loop for one window (``Hour`` per
  community and chunk, ``Day`` for the autumn DST day), S3 validation of every
  chunk, the history guard against what the recorder holds, and the S4 write.
  Nothing is written unless shape validation passed for the whole run;
  plausibility and history findings hold back only the statistics and days
  they name.

The coordinator decides which window to run and turns the outcome into the
run summary, store updates and the hooks for repairs (S6).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
import logging
import math
from typing import Any, Final

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant

from .exceptions import ApiChangedError, SchemaError
from .models import (
    Aggregation,
    AnalysisData,
    DayCompleteness,
    Direction,
    MeteringPointInfo,
)
from .statistics import (
    PORTAL_TZ,
    CommunityRef,
    StatisticDef,
    WriteResult,
    async_import_window,
    build_statistic_defs,
    dst_gap_hour,
    local_date,
    local_day_start,
)
from .validation import (
    ValidationResult,
    check_history,
    schema_failure,
    validate_chunk,
    validate_completeness,
)

_LOGGER = logging.getLogger(__name__)

MAX_CHUNK_DAYS: Final = 31  # Hour requests (spec 01: at most 31 local days)
FIRST_INCOMPLETE_LOOKBACK_DAYS: Final = 120  # ADR 0004
COMPLETENESS_MONTHS: Final = 13  # day-completeness range of a daily run
MAX_MANUAL_DAYS: Final = 400  # service ``refetch``


class RunType(StrEnum):
    """Why a run was started (spec 03 "Run types")."""

    INITIAL = "initial"
    DAILY = "daily"
    LONG = "long"
    MANUAL = "manual"


class RunStatus(StrEnum):
    """Outcome of a run; the values of the ``last_run_status`` sensor (spec 06)."""

    OK = "ok"
    PARTIAL = "partial"
    SKIPPED_MAINTENANCE = "skipped_maintenance"
    AUTH_FAILED = "auth_failed"
    API_CHANGED = "api_changed"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"


class RunAborted(Exception):
    """Shape validation failed: the run stops and writes nothing."""

    def __init__(self, validation: ValidationResult) -> None:
        """Keep the validation result for the run summary."""
        super().__init__("shape validation failed")
        self.validation = validation


class RunCancelled(Exception):
    """The entry is unloading; the run stopped between two requests."""


# ---------------------------------------------------------------------------
# Dates


def local_today(now: datetime) -> date:
    """Return the Austrian local date of a UTC moment."""

    return now.astimezone(PORTAL_TZ).date()


def add_months(day: date, months: int) -> date:
    """Shift ``day`` by whole months (negative allowed), clamped to month end."""

    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    for dd in (day.day, 30, 29, 28):
        try:
            return date(year, month, dd)
        except ValueError:
            continue
    raise AssertionError("unreachable")  # pragma: no cover


def days_between(start: date, end: date) -> list[date]:
    """Return every date from ``start`` to ``end``, both inclusive."""

    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def first_incomplete(
    entries: Iterable[DayCompleteness], start: date, end: date
) -> date | None:
    """Earliest day in ``start..end`` not complete for both directions.

    A day missing from the list counts as not complete; so do null flags
    (future days), which the parser already turned into ``False``.
    """

    complete = {
        e.day for e in entries if e.consumption_complete and e.generation_complete
    }
    return next((d for d in days_between(start, end) if d not in complete), None)


def daily_window_start(
    today: date, entries: Iterable[DayCompleteness], window_days: int
) -> date:
    """``min(today - window_days, earliest incomplete in the last 120 days)``."""

    lookback = today - timedelta(days=FIRST_INCOMPLETE_LOOKBACK_DAYS)
    incomplete = first_incomplete(entries, lookback, today) or today
    return min(today - timedelta(days=window_days), incomplete)


def complete_until(
    entries: Iterable[DayCompleteness],
    start: date,
    end: date,
    previous: date | None,
) -> date | None:
    """Last date of the contiguous complete run (spec 06 ``data_complete_until``).

    The walk starts at ``start`` (the first fetched day). If the stored value
    ends before that, the days in between were not re-checked and the stored
    value stays. Returns ``None`` when nothing is known to be complete.
    """

    if previous is not None and previous < start - timedelta(days=1):
        return previous
    stop = first_incomplete(entries, start, end)
    if stop is None:
        return end
    if stop > start:
        return stop - timedelta(days=1)
    # The first fetched day is incomplete: the run ends the day before, as far
    # as an earlier run saw complete days up to there.
    return start - timedelta(days=1) if previous is not None else None


def chunk_window(start: date, end: date) -> list[tuple[date, date]]:
    """Split ``start..end`` into calendar-month chunks (each ≤ 31 days)."""

    chunks = []
    cursor = start
    while cursor <= end:
        next_month = add_months(cursor.replace(day=1), 1)
        chunk_end = min(end, next_month - timedelta(days=1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def autumn_dst_days(start: date, end: date) -> list[date]:
    """Days in ``start..end`` with the autumn DST gap hour (spec 04)."""

    out = []
    for year in range(start.year, end.year + 1):
        # The last Sunday of October is between the 25th and the 31st.
        for dd in range(25, 32):
            day = date(year, 10, dd)
            if start <= day <= end and dst_gap_hour(day) is not None:
                out.append(day)
    return out


def request_count(
    chunks: Sequence[tuple[date, date]], communities: int, *, fixed: int = 4
) -> int:
    """Return the request count of a run (spec 03 "Run sequence").

    Maintenance, version, metering-point and completeness, then one ``Hour``
    per community and chunk and one ``Day`` per community and DST day.

    ``fixed`` counts the per-run requests (a completeness range over 13
    months adds one, see the client).
    """

    dst = sum(len(autumn_dst_days(s, e)) for s, e in chunks)
    return fixed + communities * (len(chunks) + dst)


# ---------------------------------------------------------------------------
# Entry and metering points


@dataclass(frozen=True, slots=True)
class CommunityConf:
    """One selected community from the config entry (spec 02)."""

    id: int
    slug: str
    name: str


@dataclass(frozen=True, slots=True)
class EntrySetup:
    """What a run needs from the config entry and options."""

    member: str
    location_id: str
    site_slug: str
    mp_ids: tuple[int, ...]
    communities: tuple[CommunityConf, ...]
    max_hourly_kwh: float
    site_label: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def community_ids(self) -> list[int]:
        """Portal ids of the selected communities, in entry order."""

        return [c.id for c in self.communities]

    def sensitive(self) -> list[object]:
        """Ids to redact from validation messages."""

        return [
            self.member,
            self.location_id,
            *self.mp_ids,
            *(c.id for c in self.communities),
        ]


@dataclass(frozen=True, slots=True)
class SiteInfo:
    """What ``metering-point`` says about the selected metering points."""

    directions: frozenset[Direction]
    earliest_activation: date | None
    names: Mapping[int, str]  # community id -> current portal name
    all_communities: bool  # the entry selects every active community (P4)
    missing_mp_ids: tuple[int, ...] = ()


def site_info(
    info: MeteringPointInfo,
    location_id: str,
    mp_ids: Collection[int],
    community_ids: Collection[int],
) -> SiteInfo:
    """Read directions, activation start and names for the selected points.

    Raises ``ApiChangedError`` when none of the selected metering points is
    found: the portal ids changed (as on 2026-10-22) and nothing can be
    fetched safely until the entry is reconfigured.
    """

    location = info.location(location_id)
    points = (
        [mp for mp in location.metering_points if mp.id in mp_ids] if location else []
    )
    if not points:
        raise ApiChangedError("selected metering points not found")
    found = {mp.id for mp in points}
    directions = frozenset(mp.direction for mp in points)
    selected = set(community_ids)
    names: dict[int, str] = {}
    active: set[int] = set()
    starts: list[date] = []
    for mp in points:
        for act in mp.activations:
            active.add(act.community_id)
            if act.community_id in selected:
                names[act.community_id] = act.community_name
                starts.append(act.active_from)
    return SiteInfo(
        directions=directions,
        earliest_activation=min(starts) if starts else None,
        names=names,
        all_communities=active <= selected,
        missing_mp_ids=tuple(sorted(set(mp_ids) - found)),
    )


def statistic_defs(setup: EntrySetup, site: SiteInfo) -> list[StatisticDef]:
    """Return the entry's statistics with current names and labels."""

    refs = [
        CommunityRef(c.slug, site.names.get(c.id) or c.name) for c in setup.communities
    ]
    kwargs: dict[str, Any] = {"labels": dict(setup.labels)}
    if setup.site_label:
        kwargs["site_label"] = setup.site_label
    return build_statistic_defs(setup.site_slug, refs, site.directions, **kwargs)


# ---------------------------------------------------------------------------
# Recorder: stored day totals for the history guard


def _load_day_totals(
    hass: HomeAssistant, keys: Mapping[str, str], start: datetime, end: datetime
) -> dict[tuple[str, date], float]:
    """Sum stored hourly states per (statistic key, local day) (executor)."""

    rows = statistics_during_period(
        hass, start, end, set(keys), "hour", None, {"state"}
    )
    sums: dict[tuple[str, date], list[float]] = defaultdict(list)
    for statistic_id, stat_rows in rows.items():
        key = keys[statistic_id]
        for row in stat_rows:
            if (state := row.get("state")) is None:
                continue
            hour = datetime.fromtimestamp(row["start"], UTC)
            sums[key, local_date(hour)].append(state)
    return {k: math.fsum(v) for k, v in sums.items()}


async def async_load_day_totals(
    hass: HomeAssistant, defs: Sequence[StatisticDef], first: date, last: date
) -> dict[tuple[str, date], float]:
    """Return stored day totals in ``first..last`` for ``check_history()``."""

    keys = {d.statistic_id: d.key for d in defs}
    return await get_instance(hass).async_add_executor_job(
        _load_day_totals,
        hass,
        keys,
        local_day_start(first),
        local_day_start(last + timedelta(days=1)),
    )


# ---------------------------------------------------------------------------
# Pipeline


@dataclass(slots=True)
class ImportResult:
    """Outcome of ``async_import()``."""

    validation: ValidationResult
    writes: dict[str, WriteResult]
    chunks: int
    blocked: dict[str, list[date]]  # every day held back per statistic key

    @property
    def partial(self) -> bool:
        """Return True when a plausibility or history rule held data back."""

        return bool(self.validation.failures)


async def async_fetch_completeness(
    client: Any, setup: EntrySetup, start: date, end: date
) -> tuple[list[DayCompleteness], ValidationResult]:
    """``day-completeness`` for ``start..end`` with its S9 check.

    Raises ``RunAborted`` on a shape failure.
    """

    try:
        entries = await client.get_day_completeness(
            setup.member, start, end, setup.community_ids, list(setup.mp_ids)
        )
    except SchemaError as err:
        raise RunAborted(
            schema_failure(err, chunk=(start, end), sensitive=setup.sensitive())
        ) from err
    result = validate_completeness(entries, start, end, sensitive=setup.sensitive())
    if result.schema_failed:
        raise RunAborted(result)
    return entries, result


async def async_import(
    hass: HomeAssistant,
    client: Any,
    setup: EntrySetup,
    site: SiteInfo,
    start: date,
    end: date,
    completeness: Sequence[DayCompleteness],
    *,
    released: Iterable[tuple[str, date]] = (),
    validation: ValidationResult | None = None,
    check_stop: Callable[[], None] = lambda: None,
) -> ImportResult:
    """Fetch, validate and write ``start..end`` (local dates, inclusive).

    ``check_stop`` raises ``RunCancelled`` when the entry unloads; it is
    called before every request. Raises ``RunAborted`` on any shape failure,
    before anything is written. ``client`` is the S1 client (or a test double
    with the same methods).
    """

    result = validation if validation is not None else ValidationResult()
    sensitive = setup.sensitive()
    defs = statistic_defs(setup, site)
    chunks = chunk_window(start, end)
    hourly: dict[str, list[AnalysisData]] = {c.slug: [] for c in setup.communities}
    day_values: dict[str, list[AnalysisData]] = {c.slug: [] for c in setup.communities}
    fetched: list[dict[str, AnalysisData]] = []

    async def fetch(
        aggregation: Aggregation, first: date, last: date
    ) -> dict[str, AnalysisData]:
        responses: dict[str, AnalysisData] = {}
        for community in setup.communities:
            check_stop()
            try:
                responses[community.slug] = await client.get_analysis(
                    setup.member,
                    aggregation,
                    first,
                    last,
                    [community.id],
                    list(setup.mp_ids),
                )
            except SchemaError as err:
                result.merge(
                    schema_failure(err, chunk=(first, last), sensitive=sensitive)
                )
                raise RunAborted(result) from err
        checked = validate_chunk(
            first,
            last,
            responses,
            site=setup.site_slug,
            aggregation=aggregation,
            all_communities=site.all_communities,
            max_hourly_kwh=setup.max_hourly_kwh,
            sensitive=sensitive,
        )
        result.merge(checked)
        if checked.schema_failed:
            raise RunAborted(result)
        return responses

    for first, last in chunks:
        responses = await fetch(Aggregation.HOUR, first, last)
        fetched.append(responses)
        for slug, data in responses.items():
            hourly[slug].append(data)
        for day in autumn_dst_days(first, last):
            for slug, data in (await fetch(Aggregation.DAY, day, day)).items():
                day_values[slug].append(data)

    stored = await async_load_day_totals(hass, defs, start, end)
    released = list(released)
    for responses in fetched:
        result.merge(
            check_history(
                responses,
                completeness,
                stored,
                site=setup.site_slug,
                released=released,
                sensitive=sensitive,
            )
        )

    window = days_between(start, end)
    blocked: dict[str, list[date]] = {}
    for sdef in defs:
        days = [d for d in window if result.is_blocked(sdef.key, d)]
        if days:
            blocked[sdef.key] = days

    writes = await async_import_window(
        hass,
        defs,
        hourly,
        completeness,
        start,
        end,
        day_values=day_values,
        held_days=blocked,
    )
    return ImportResult(
        validation=result, writes=writes, chunks=len(chunks), blocked=blocked
    )
