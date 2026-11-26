"""Fetch, validate and write one run's window (spec 03, ADR 0004 and 0006).

Two layers:

* pure planning helpers: run window, month-aligned chunks cut at activation
  boundaries, autumn DST days, what the selected metering points say about
  the entry (``SiteInfo``);
* ``async_import()``: the request loop for one window (``Hour`` per chunk
  and community active in it, ``Day`` for the autumn DST day), S3 validation of every
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
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
import itertools
import logging
import math
from typing import Any, Final

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util

from .exceptions import ApiChangedError, EgAustriaError, SchemaError
from .models import (
    Aggregation,
    AnalysisData,
    DayCompleteness,
    Direction,
    MeteringPointInfo,
)
from .prices import PriceSource, async_hourly_prices, first_unpriced
from .settlement import (
    Key,
    MonthFactor,
    MonthTotal,
    Settlements,
    apply_settlements,
    member_totals,
    month_factors,
    shared_requests,
)
from .statistics import (
    PORTAL_TZ,
    CommunityRef,
    Quantity,
    StatisticDef,
    WriteResult,
    async_import_window,
    build_statistic_defs,
    day_hours,
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
from .zero import (
    ZeroEstimate,
    ZeroRate,
    derive_rates,
    estimate,
    latest_rates,
    rate_lookup,
    reprice,
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
    *,
    horizon: date | None = None,
    exempt: Mapping[Direction, date] | None = None,
) -> date | None:
    """Last date of the contiguous complete run (spec 06 ``data_complete_until``).

    The walk starts at ``start`` (the first fetched day), never before
    ``horizon`` (the first day a daily run checks): older gaps are never
    re-checked, so they must not hold the value back (issue #98). If the
    stored value ends before the walk but inside the horizon, the days in
    between were not re-checked and the stored value stays. ``exempt`` maps a
    direction to the latest activation start of the selected communities in
    it; that direction's flag does not count up to and including that day,
    since the portal reports days before a community's activation (and a
    partial activation day) as incomplete (issue #98). Returns ``None`` when
    nothing is known to be complete.
    """

    walk = start if horizon is None else max(start, horizon)
    if walk > end:
        return previous
    if (
        previous is not None
        and previous < walk - timedelta(days=1)
        and (horizon is None or previous >= horizon - timedelta(days=1))
    ):
        return previous
    if exempt:
        cons = exempt.get(Direction.CONSUMPTION, date.min)
        gen = exempt.get(Direction.GENERATION, date.min)
        entries = [
            replace(
                e,
                consumption_complete=e.consumption_complete or e.day <= cons,
                generation_complete=e.generation_complete or e.day <= gen,
            )
            for e in entries
        ]
    stop = first_incomplete(entries, walk, end)
    if stop is None:
        return end
    if stop > walk:
        return stop - timedelta(days=1)
    # The first walked day is incomplete: the run ends the day before, as far
    # as an earlier run saw complete days up to there.
    return walk - timedelta(days=1) if previous is not None else None


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
    plan: Sequence[PlannedChunk] | Sequence[tuple[date, date]],
    communities: int | None = None,
    *,
    fixed: int = 5,
) -> int:
    """Return the request count of a run (spec 03 "Run sequence").

    Maintenance, version, metering-point, transactions (issue #74) and
    completeness, then one ``Hour``
    per planned chunk and community active in it, and one ``Day`` per autumn
    DST day and community active on it.

    ``plan`` is ``plan_chunks()`` output; plain ``(first, last)`` chunks with
    ``communities`` count every community in every chunk. ``fixed`` counts
    the per-run requests (a completeness range over 13 months adds one, see
    the client).
    """

    total = fixed
    for chunk in plan:
        if isinstance(chunk, PlannedChunk):
            first, last, count = chunk.first, chunk.last, len(chunk.communities)
        else:
            (first, last), count = chunk, communities or 0
        total += count * (1 + len(autumn_dst_days(first, last)))
    return total


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
    surplus_price: PriceSource | None = None  # ADR 0015
    supplier_price: PriceSource | None = None  # ADR 0015, issue #73

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


DateRange = tuple[date, date | None]  # first and last active day; None = open


def merge_ranges(ranges: Iterable[DateRange]) -> tuple[DateRange, ...]:
    """Merge overlapping or adjacent activation ranges (both ends inclusive)."""

    out: list[DateRange] = []
    for begin, end in sorted(ranges, key=lambda r: r[0]):
        if out:
            last_begin, last_end = out[-1]
            if last_end is None:
                continue
            if begin <= last_end + timedelta(days=1):
                merged = None if end is None else max(last_end, end)
                out[-1] = (last_begin, merged)
                continue
        out.append((begin, end))
    return tuple(out)


def _in_ranges(ranges: Iterable[DateRange], day: date) -> bool:
    return any(b <= day and (e is None or day <= e) for b, e in ranges)


@dataclass(frozen=True, slots=True)
class SiteInfo:
    """What ``metering-point`` says about the selected metering points."""

    directions: frozenset[Direction]
    earliest_activation: date | None
    names: Mapping[int, str]  # community id -> current portal name
    all_communities: bool  # the entry selects every community ever active (P4)
    missing_mp_ids: tuple[int, ...] = ()
    # Community id -> merged activation ranges over the selected metering
    # points, for every community they are or were in (selected or not).
    # Empty: no activation data (tests); every community counts as active.
    activations: Mapping[int, tuple[DateRange, ...]] = field(default_factory=dict)
    # Issue #89: days with an own-site Zero plan (sending and receiving point
    # both selected), merged. Empty: no Zero estimate.
    zero_ranges: tuple[DateRange, ...] = ()
    # Issue #98: direction -> latest first activation of the selected
    # communities with a selected metering point in it (``complete_until``).
    complete_from: Mapping[Direction, date] = field(default_factory=dict)

    def own_zero_on(self, day: date) -> bool:
        """Return True when an own-site Zero plan is active on ``day``."""

        return _in_ranges(self.zero_ranges, day)

    def active_on(self, community_id: int, day: date) -> bool:
        """Return True when the community is active on ``day`` (spec 03)."""

        if not self.activations:
            return True
        return _in_ranges(self.activations.get(community_id, ()), day)

    def active_between(self, community_id: int, first: date, last: date) -> bool:
        """Return True when the community is active on any day of the range."""

        if not self.activations:
            return True
        return any(
            b <= last and (e is None or first <= e)
            for b, e in self.activations.get(community_id, ())
        )


def site_info(
    info: MeteringPointInfo,
    location_id: str,
    mp_ids: Collection[int],
    community_ids: Collection[int],
) -> SiteInfo:
    """Read directions, activation ranges and names for the selected points.

    A community's range is the union over the selected metering points: it
    is active on a day when at least one of them is active in it.

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
    ranges: dict[int, list[DateRange]] = defaultdict(list)
    starts: list[date] = []
    first: dict[tuple[int, Direction], date] = {}
    for mp in points:
        for act in mp.activations:
            if act.active_to is not None and act.active_to < act.active_from:
                continue  # never active (withdrawn before the start)
            ranges[act.community_id].append((act.active_from, act.active_to))
            if act.community_id in selected:
                names[act.community_id] = act.community_name
                starts.append(act.active_from)
                key = (act.community_id, mp.direction)
                first[key] = min(first.get(key, act.active_from), act.active_from)
    complete_from: dict[Direction, date] = {}
    for (_, direction), begin in first.items():
        complete_from[direction] = max(complete_from.get(direction, begin), begin)
    numbers = {mp.number for mp in points if mp.number}
    zero = [
        (plan.active_from, plan.active_to)
        for mp in points
        for plan in mp.zero_plans
        if plan.from_number in numbers
        and plan.to_number in numbers
        and plan.from_number != plan.to_number
        and (plan.active_to is None or plan.active_to >= plan.active_from)
    ]
    return SiteInfo(
        zero_ranges=merge_ranges(zero) if zero else (),
        directions=directions,
        earliest_activation=min(starts) if starts else None,
        names=names,
        all_communities=set(ranges) <= selected,
        missing_mp_ids=tuple(sorted(set(mp_ids) - found)),
        activations={cid: merge_ranges(r) for cid, r in ranges.items()},
        complete_from=complete_from,
    )


@dataclass(frozen=True, slots=True)
class PlannedChunk:
    """One request range and the selected communities active in all of it."""

    first: date
    last: date
    communities: tuple[CommunityConf, ...]
    all_communities: bool  # every community active in the range is selected

    @property
    def slugs(self) -> list[str]:
        """Slugs of the communities requested for this chunk."""

        return [c.slug for c in self.communities]


def plan_chunks(
    setup: EntrySetup, site: SiteInfo, start: date, end: date
) -> list[PlannedChunk]:
    """Split ``start..end`` into request chunks (spec 03 "Chunks", issue #16).

    Month-aligned chunks are cut again where a selected community's
    activation begins or ends, so the set of active communities is the same
    on every day of a chunk and each community is requested only inside its
    own activation. Chunks where no selected community is active are left
    out (nothing to request; those days stay unknown).
    """

    cuts: set[date] = set()
    for community in setup.communities:
        for begin, last in site.activations.get(community.id, ()):
            cuts.add(begin)
            if last is not None:
                cuts.add(last + timedelta(days=1))
    plan: list[PlannedChunk] = []
    for first, last in chunk_window(start, end):
        edges = sorted({first, *(c for c in cuts if first < c <= last)})
        bounds = [*edges, last + timedelta(days=1)]
        for lo, hi in itertools.pairwise(bounds):
            hi_day = hi - timedelta(days=1)
            active = tuple(c for c in setup.communities if site.active_on(c.id, lo))
            if not active:
                continue
            selected = {c.id for c in active}
            others = {
                cid
                for cid in site.activations
                if cid not in selected and site.active_between(cid, lo, hi_day)
            }
            plan.append(
                PlannedChunk(
                    lo,
                    hi_day,
                    active,
                    all_communities=site.all_communities
                    if not site.activations
                    else not others,
                )
            )
    return plan


def statistic_defs(setup: EntrySetup, site: SiteInfo) -> list[StatisticDef]:
    """Return the entry's statistics with current names and labels."""

    refs = [
        CommunityRef(c.slug, site.names.get(c.id) or c.name) for c in setup.communities
    ]
    kwargs: dict[str, Any] = {
        "labels": dict(setup.labels),
        "surplus": setup.surplus_price is not None,
        "supplier": setup.supplier_price is not None,
    }
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
    price_missing: date | None = None  # first overdue day without surplus price
    supplier_price_missing: date | None = None  # the same for the supplier price
    # Issue #74: how each settled community, direction and month was scaled.
    settlement: dict[Key, MonthFactor] = field(default_factory=dict)
    # Issue #89: latest derived Zero rate per community and direction, and the
    # months with an estimated Zero (no kWh).
    zero_rates: dict[tuple[str, Direction], ZeroRate] = field(default_factory=dict)
    zero_months: list[str] = field(default_factory=list)
    # Issue #115: member-total requests for shared months (ok / failed).
    shared_requests: dict[str, int] = field(default_factory=dict)

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
    settlements: Settlements | None = None,
    zero_rates: Mapping[tuple[str, Direction], ZeroRate] | None = None,
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
    plan = plan_chunks(setup, site, start, end)
    hourly: dict[str, list[AnalysisData]] = {c.slug: [] for c in setup.communities}
    day_values: dict[str, list[AnalysisData]] = {c.slug: [] for c in setup.communities}
    active_days: dict[str, set[date]] = {c.slug: set() for c in setup.communities}
    fetched: list[dict[str, AnalysisData]] = []

    async def fetch(
        chunk: PlannedChunk, aggregation: Aggregation, first: date, last: date
    ) -> dict[str, AnalysisData]:
        responses: dict[str, AnalysisData] = {}
        for community in chunk.communities:
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
            all_communities=chunk.all_communities,
            max_hourly_kwh=setup.max_hourly_kwh,
            sensitive=sensitive,
        )
        result.merge(checked)
        if checked.schema_failed:
            raise RunAborted(result)
        return responses

    for chunk in plan:
        responses = await fetch(chunk, Aggregation.HOUR, chunk.first, chunk.last)
        fetched.append(responses)
        for slug, data in responses.items():
            hourly[slug].append(data)
            active_days[slug].update(days_between(chunk.first, chunk.last))
        for day in autumn_dst_days(chunk.first, chunk.last):
            for slug, data in (await fetch(chunk, Aggregation.DAY, day, day)).items():
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
        days = [
            d for d in window if any(result.is_blocked(k, d) for k in sdef.gate_keys)
        ]
        if days:
            blocked[sdef.key] = days

    # Issue #74: settled months' amounts scaled to the invoices, per
    # community and direction, before anything is written (site sums and the
    # total compensation follow from the scaled community values).
    # Issue #89: own-site Zero estimate per day, Zero rates derived from the
    # settled months (from the portal's amounts, before any repricing), then
    # each hour's Zero kWh repriced to the Zero rate.
    portal_hourly = hourly
    zero = (
        estimate(hourly, site.own_zero_on) if site.zero_ranges else ZeroEstimate({}, {})
    )
    derived = derive_rates(hourly, zero, settlements) if settlements else {}
    rates = latest_rates(zero_rates or {}, derived)
    lookup = rate_lookup(derived, rates)
    hourly = reprice(hourly, zero, lookup)
    day_values = reprice(day_values, zero, lookup)

    factors: dict[Key, MonthFactor] = {}
    shared: dict[str, int] = {}
    if settlements is not None:
        # Issue #115: a month whose invoice also covers unselected metering
        # points is scaled by the member's total over all of them, one
        # ``Month`` request per community. A failed request (the portal may
        # refuse points of another location) leaves those months provisional.
        totals: dict[Key, MonthTotal] = {}
        ids = {c.slug: c.id for c in setup.communities}
        for req in shared_requests(settlements, portal_hourly, setup.mp_ids):
            check_stop()
            try:
                data = await client.get_analysis(
                    setup.member,
                    Aggregation.MONTH,
                    req.first,
                    req.last,
                    [ids[req.slug]],
                    list(req.mp_ids),
                )
            except EgAustriaError as err:
                _LOGGER.info(
                    "Member total for a shared month not available (%s); "
                    "it stays provisional",
                    type(err).__name__,
                )
                shared["failed"] = shared.get("failed", 0) + 1
                continue
            shared["ok"] = shared.get("ok", 0) + 1
            totals.update(member_totals(req.slug, data))
        factors = month_factors(hourly, settlements, totals, portal_hourly)
        hourly = apply_settlements(hourly, factors)
        day_values = apply_settlements(day_values, factors)

    hours = [h for d in window for h in day_hours(d)]
    prices: dict[datetime, float] | None = None
    if setup.surplus_price is not None:
        prices = (await async_hourly_prices(hass, setup.surplus_price, hours)).prices
    supplier_prices: dict[datetime, float] | None = None
    if setup.supplier_price is not None:
        supplier_prices = (
            await async_hourly_prices(hass, setup.supplier_price, hours)
        ).prices

    writes = await async_import_window(
        hass,
        defs,
        hourly,
        completeness,
        start,
        end,
        day_values=day_values,
        held_days=blocked,
        active_days=active_days,
        prices=prices,
        supplier_prices=supplier_prices,
    )
    today = local_today(dt_util.utcnow())

    def missing(
        quantity: Quantity,
        source: PriceSource | None,
        known: Mapping[datetime, float] | None,
    ) -> date | None:
        write = writes.get(f"{setup.site_slug}_{quantity}")
        if source is None or write is None:
            return None
        return first_unpriced(
            write.unpriced,
            {},
            source,
            today,
            # No repair for hours before the source's first price.
            covered_from=min(known) if known else None,
        )

    price_missing = missing(Quantity.SURPLUS_EUR, setup.surplus_price, prices)
    # Issue #75: unpriced supplier kWh count as well as unpriced savings.
    supplier_days = [
        first
        for quantity in (Quantity.IMPORT_SAVINGS_EUR, Quantity.SUPPLIER_EUR)
        if (first := missing(quantity, setup.supplier_price, supplier_prices))
        is not None
    ]
    supplier_missing = min(supplier_days) if supplier_days else None
    return ImportResult(
        validation=result,
        writes=writes,
        chunks=len(plan),
        blocked=blocked,
        price_missing=price_missing,
        supplier_price_missing=supplier_missing,
        settlement=factors,
        shared_requests=shared,
        zero_rates=rates,
        zero_months=sorted(
            f"{slug}/{y:04d}-{m:02d}" for slug, (y, m) in zero.month_kwh
        ),
    )
