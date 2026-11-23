"""External statistics for EG Austria (spec 04, ADR 0003/0005/0012).

Two layers:

* ``build_series()`` is pure: it maps parsed ``analysis-data`` responses
  (S1 models), ``day-completeness`` and the days validation held back into one
  hourly series per statistic. Unknown hours are absent from the series; they
  are never zero (ADR 0005).
* ``async_write_statistics()`` reads what the recorder already stores for each
  statistic, computes cumulative sums from the last stored sum before the
  window, lets stored rows win over unknown hours and imports one batch per
  statistic with ``async_add_external_statistics``.

``async_import_window()`` combines both; it is the entry point for the
coordinator (S5).

Statistic ids are built from slugs stored in the config entry only; nothing
from the portal (ids, names) goes into an id. Display names follow the portal
community name and are refreshed through the metadata on every import.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
import logging
from typing import Final
from zoneinfo import ZoneInfo

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import CURRENCY_EURO, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance

from .const import DOMAIN
from .models import AnalysisData, DayCompleteness, Direction, EnergyPoint

_LOGGER = logging.getLogger(__name__)

SOURCE: Final = DOMAIN
UNIT: Final = UnitOfEnergy.KILO_WATT_HOUR
UNIT_CLASS: Final = "energy"
MONEY_UNIT: Final = CURRENCY_EURO
# Money values are rounded to this many decimals (well below a cent per hour).
MONEY_DIGITS: Final = 6
# Dates in the portal (window, day-completeness, Day aggregation) are Austrian
# local dates, independent of the Home Assistant time zone.
PORTAL_TZ: Final = ZoneInfo("Europe/Vienna")
# Lookback for the last stored sum before the window (spec 04).
BASE_LOOKBACK: Final = timedelta(days=400)
_HOUR = timedelta(hours=1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

DEFAULT_SITE_LABEL: Final = "Home"


class Quantity(StrEnum):
    """Quantity keys (spec 04 "Mapping"); the ``*_eur`` ones are phase 2."""

    CONSUMPTION = "consumption"
    GENERATION = "generation"
    CONSUMPTION_FROM_COMMUNITY = "consumption_from_community"
    GENERATION_TO_COMMUNITY = "generation_to_community"
    CONSUMPTION_FROM_SUPPLIER = "consumption_from_supplier"
    GENERATION_TO_SURPLUS = "generation_to_surplus"
    FEED_IN_EUR = "feed_in_eur"
    CONSUMPTION_EUR = "consumption_eur"
    SURPLUS_EUR = "surplus_eur"
    FEED_IN_TOTAL_EUR = "feed_in_total_eur"
    IMPORT_SAVINGS_EUR = "import_savings_eur"  # issue #73
    SUPPLIER_EUR = "supplier_eur"  # issue #75


# ``attr`` of quantities computed from other statistics, not from a point.
DERIVED: Final = "derived"


@dataclass(frozen=True, slots=True)
class QuantityInfo:
    """How one quantity maps onto an ``EnergyPoint``."""

    direction: Direction
    attr: str  # EnergyPoint attribute: "value", "community", "excess", "money"
    gated: bool  # True: needs day-completeness of its side (community values)
    site_label: str  # default English label for site statistics
    community_label: str | None  # label for per-community statistics, or None
    unit: str = UNIT
    unit_class: str | None = UNIT_CLASS
    # kWh quantities whose held days also hold this one (money, ADR 0008).
    gates: tuple[str, ...] = ()
    # Derived quantities: which optional price they need (ADR 0015).
    price: str = "surplus"

    @property
    def derived(self) -> bool:
        """Return True for quantities computed from other statistics."""

        return self.attr == DERIVED

    @property
    def summed(self) -> bool:
        """Return True if site values are the sum over the communities."""

        return self.attr in ("community", "money")


QUANTITIES: Final[Mapping[Quantity, QuantityInfo]] = {
    Quantity.CONSUMPTION: QuantityInfo(
        Direction.CONSUMPTION, "value", False, "Consumption", None
    ),
    Quantity.GENERATION: QuantityInfo(
        Direction.GENERATION, "value", False, "Generation", None
    ),
    Quantity.CONSUMPTION_FROM_COMMUNITY: QuantityInfo(
        Direction.CONSUMPTION,
        "community",
        True,
        "Consumption from communities",
        "Consumption from community",
    ),
    Quantity.GENERATION_TO_COMMUNITY: QuantityInfo(
        Direction.GENERATION,
        "community",
        True,
        "Generation to communities",
        "Generation to community",
    ),
    Quantity.CONSUMPTION_FROM_SUPPLIER: QuantityInfo(
        Direction.CONSUMPTION, "excess", True, "Consumption from supplier", None
    ),
    Quantity.GENERATION_TO_SURPLUS: QuantityInfo(
        Direction.GENERATION, "excess", True, "Generation to surplus buyer", None
    ),
    Quantity.FEED_IN_EUR: QuantityInfo(
        Direction.GENERATION,
        "money",
        True,
        "Feed-in income from communities",
        "Feed-in income",
        MONEY_UNIT,
        None,
        (Quantity.GENERATION_TO_COMMUNITY,),
    ),
    Quantity.CONSUMPTION_EUR: QuantityInfo(
        Direction.CONSUMPTION,
        "money",
        True,
        "Cost of consumption from communities",
        "Cost of consumption",
        MONEY_UNIT,
        None,
        (Quantity.CONSUMPTION_FROM_COMMUNITY,),
    ),
    Quantity.SURPLUS_EUR: QuantityInfo(
        Direction.GENERATION,
        DERIVED,
        True,
        "Surplus income",
        None,
        MONEY_UNIT,
        None,
        (Quantity.GENERATION_TO_SURPLUS,),
    ),
    Quantity.FEED_IN_TOTAL_EUR: QuantityInfo(
        Direction.GENERATION,
        DERIVED,
        True,
        "Feed-in compensation",
        None,
        MONEY_UNIT,
        None,
        (Quantity.GENERATION_TO_COMMUNITY, Quantity.GENERATION_TO_SURPLUS),
    ),
    # Issue #73: community kWh at the supplier's energy price minus what the
    # community charged for them.
    Quantity.IMPORT_SAVINGS_EUR: QuantityInfo(
        Direction.CONSUMPTION,
        DERIVED,
        True,
        "Import savings",
        "Import savings",
        MONEY_UNIT,
        None,
        (Quantity.CONSUMPTION_FROM_COMMUNITY,),
        "supplier",
    ),
    # Issue #75: the import side's mirror of surplus income, the supplier kWh
    # at the supplier's energy price (no grid fees or base charge).
    Quantity.SUPPLIER_EUR: QuantityInfo(
        Direction.CONSUMPTION,
        DERIVED,
        True,
        "Supplier energy cost",
        None,
        MONEY_UNIT,
        None,
        (Quantity.CONSUMPTION_FROM_SUPPLIER,),
        "supplier",
    ),
}


@dataclass(frozen=True, slots=True)
class CommunityRef:
    """A selected community as stored in the config entry (spec 02)."""

    slug: str
    name: str  # last seen portal name; display only, never in an id


@dataclass(frozen=True, slots=True)
class StatisticDef:
    """One external statistic."""

    key: str  # "<site>_<quantity>" or "<site>_<community>_<quantity>"
    quantity: Quantity
    name: str
    community: str | None = None  # community slug; None for site statistics

    @property
    def statistic_id(self) -> str:
        """Return the external statistic id."""

        return f"{DOMAIN}:{self.key}"

    @property
    def info(self) -> QuantityInfo:
        """Return the quantity mapping."""

        return QUANTITIES[self.quantity]

    @property
    def gate_keys(self) -> tuple[str, ...]:
        """Statistic keys whose held days hold this statistic too.

        A money statistic is held whenever the kWh it is computed from is.
        """

        if not self.info.gates:
            return (self.key,)
        prefix = self.key[: -len(self.quantity)]
        return tuple(prefix + gate for gate in self.info.gates)

    def metadata(self) -> StatisticMetaData:
        """Return the recorder metadata (spec 04 table)."""

        return StatisticMetaData(
            source=SOURCE,
            statistic_id=self.statistic_id,
            name=self.name,
            unit_of_measurement=self.info.unit,
            unit_class=self.info.unit_class,
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
        )


def build_statistic_defs(
    site_slug: str,
    communities: Sequence[CommunityRef],
    directions: Collection[Direction],
    *,
    site_label: str = DEFAULT_SITE_LABEL,
    labels: Mapping[str, str] | None = None,
    surplus: bool = False,
    supplier: bool = False,
) -> list[StatisticDef]:
    """Return the statistics of one entry, site statistics first.

    ``directions`` are the directions of the selected metering points;
    quantities of a missing direction are not created. ``site_label`` and
    ``labels`` (keys ``site.<quantity>`` and ``community.<quantity>``) let the
    caller pass translated labels; the defaults are English. ``surplus`` adds
    the statistics that need a surplus price, ``supplier`` those that need a
    supplier price (ADR 0015, issue #73).
    """

    def wanted(info: QuantityInfo) -> bool:
        if not info.derived:
            return True
        return supplier if info.price == "supplier" else surplus

    labels = labels or {}
    defs: list[StatisticDef] = []
    for quantity, info in QUANTITIES.items():
        if info.direction not in directions or not wanted(info):
            continue
        label = labels.get(f"site.{quantity}", info.site_label)
        defs.append(
            StatisticDef(
                key=f"{site_slug}_{quantity}",
                quantity=quantity,
                name=f"{site_label} {label}",
            )
        )
    for community in communities:
        for quantity, info in QUANTITIES.items():
            if (
                info.community_label is None
                or info.direction not in directions
                or not wanted(info)
            ):
                continue
            label = labels.get(f"community.{quantity}", info.community_label)
            defs.append(
                StatisticDef(
                    key=f"{site_slug}_{community.slug}_{quantity}",
                    quantity=quantity,
                    name=f"{community.name} {label}",
                    community=community.slug,
                )
            )
    return defs


# ---------------------------------------------------------------------------
# Time helpers


def local_day_start(day: date) -> datetime:
    """Return the UTC start of an Austrian local date."""

    return datetime.combine(day, time(0), PORTAL_TZ).astimezone(UTC)


def local_date(hour: datetime) -> date:
    """Return the Austrian local date of a UTC hour."""

    return hour.astimezone(PORTAL_TZ).date()


def day_hours(day: date) -> list[datetime]:
    """Return the UTC hour starts of one local date (23, 24 or 25 hours)."""

    start = local_day_start(day)
    end = local_day_start(day + timedelta(days=1))
    return list(_hours(start, end))


def dst_gap_hour(day: date) -> datetime | None:
    """Return the hour the portal leaves out on the autumn DST day, else None.

    That is the first of the two local 02:00 hours (00:00 UTC in Austria): the
    hour whose local wall time repeats in the next hour.
    """

    hours = day_hours(day)
    if len(hours) != 25:
        return None
    for h in hours:
        this, after = h.astimezone(PORTAL_TZ), (h + _HOUR).astimezone(PORTAL_TZ)
        if after.fold == 1 and this.fold == 0 and this.hour == after.hour:
            return h
    return None  # pragma: no cover - a 25-hour day always has a repeated hour


def _hours(start: datetime, end: datetime) -> Iterable[datetime]:
    h = start
    while h < end:
        yield h
        h += _HOUR


# ---------------------------------------------------------------------------
# Series building (pure)


@dataclass(slots=True)
class Series:
    """Known hourly values of one statistic in the window."""

    values: dict[datetime, float] = field(default_factory=dict)
    dst_filled: list[datetime] = field(default_factory=list)
    dst_negative: list[date] = field(default_factory=list)
    # Derived money: hours whose kWh are known but whose price is not.
    unpriced: list[datetime] = field(default_factory=list)


_PointIndex = dict[Direction, dict[datetime, EnergyPoint]]


def _index(responses: Iterable[AnalysisData]) -> _PointIndex:
    index: _PointIndex = {d: {} for d in Direction}
    for response in responses:
        for direction in Direction:
            for point in response.points(direction):
                index[direction][point.start] = point
    return index


def _day_index(
    responses: Iterable[AnalysisData],
) -> dict[Direction, dict[date, EnergyPoint]]:
    index: dict[Direction, dict[date, EnergyPoint]] = {d: {} for d in Direction}
    for response in responses:
        for direction in Direction:
            for point in response.points(direction):
                index[direction][local_date(point.start)] = point
    return index


_warned_negative: set[tuple[str, date]] = set()


def build_series(
    defs: Sequence[StatisticDef],
    hourly: Mapping[str, Sequence[AnalysisData]],
    completeness: Sequence[DayCompleteness],
    first_day: date,
    last_day: date,
    *,
    day_values: Mapping[str, Sequence[AnalysisData]] | None = None,
    held_days: Mapping[str, Collection[date]] | None = None,
    active_days: Mapping[str, Collection[date]] | None = None,
) -> dict[str, Series]:
    """Build the known hourly values per statistic key (spec 04 "Hourly series").

    ``hourly`` maps community slug → the ``Hour`` responses of that community
    (one per chunk), in the entry's community order; site meter and residual
    values come from the first community, site community values are the sum
    over all communities. ``day_values`` maps community slug → ``Day``
    responses for autumn DST days. ``first_day``/``last_day`` are the window's
    local dates, both inclusive.

    ``active_days`` maps community slug → the days it was requested for (its
    activation, issue #16). On other days the community is not a member: it
    has no statistic value of its own, adds nothing to the site community
    sums and the site meter and residual come from the first community that
    is active. Without ``active_days`` every community counts as active on
    every day.
    """

    # The entry's communities in order (from the per-community statistics); a
    # community without a response makes its site sums unknown, not smaller.
    slugs = list(dict.fromkeys(d.community for d in defs if d.community)) or list(
        hourly
    )
    points = {slug: _index(hourly[slug]) for slug in hourly}
    days = {slug: _day_index((day_values or {}).get(slug, ())) for slug in hourly}
    flags = {c.day: c for c in completeness}
    held = held_days or {}

    window = list(
        _hours(
            local_day_start(first_day), local_day_start(last_day + timedelta(days=1))
        )
    )

    active = (
        {slug: set(active_days.get(slug, ())) for slug in slugs}
        if active_days is not None
        else None
    )

    result: dict[str, Series] = {}
    for sdef in defs:
        if sdef.info.derived:  # filled by derive_series()
            result[sdef.key] = Series()
            continue
        sources = [sdef.community] if sdef.community else slugs
        first_only = sdef.community is None and not sdef.info.summed
        if first_only:
            sources = sources if active is not None else slugs[:1]
        if not sources or any(s not in points for s in sources):
            result[sdef.key] = Series()
            continue

        def pick(
            d: date, sources: list[str] = sources, first_only: bool = first_only
        ) -> list[str]:
            chosen = sources
            if active is not None:
                chosen = [s for s in sources if d in active[s]]
            return chosen

        reader = _Reader(
            sdef,
            {s: points[s][sdef.info.direction] for s in sources},
            {s: days[s][sdef.info.direction] for s in sources},
            pick,
            first_only,
        )
        series = Series()
        held_here = set(held.get(sdef.key, ()))
        for h in window:
            value = reader.state(h, flags, held_here, series)
            if value is not None:
                series.values[h] = value
        result[sdef.key] = series
    return result


@dataclass(slots=True)
class _Reader:
    """Field values of one statistic, summed over its source communities."""

    sdef: StatisticDef
    hours: dict[str, dict[datetime, EnergyPoint]]  # per source community
    days: dict[str, dict[date, EnergyPoint]]  # per source community (Day responses)
    sources: Callable[[date], list[str]]  # source communities active on a day
    # Site meter and residual: one community's value, not a sum (spec 04).
    first_only: bool = False

    def _slugs(self, h: datetime) -> list[str]:
        """Source communities for hour ``h``.

        For site meter and residual values only one community counts: the
        first active one in entry order whose meter point isn't flagged
        faulty (issue #78: one community can report a faulty 0 while another
        has the value), else the first active one.
        """

        slugs = self.sources(local_date(h))
        if not self.first_only or not slugs:
            return slugs
        if self.sdef.info.attr == "value":
            for slug in slugs:
                point = self.hours[slug].get(h)
                if point is not None and not point.faulty:
                    return [slug]
        return slugs[:1]

    @staticmethod
    def _sum(points: Iterable[EnergyPoint | None], attr: str) -> float | None:
        total = 0.0
        for point in points:
            value = getattr(point, attr) if point is not None else None
            if value is None:
                return None
            total += value
        return total

    def hour(self, h: datetime) -> float | None:
        """Return the hour's raw field value, None if any source lacks it."""

        slugs = self._slugs(h)
        if not slugs:
            return None
        return self._sum((self.hours[s].get(h) for s in slugs), self.sdef.info.attr)

    def day(self, d: date) -> float | None:
        """Return the ``Day`` field value, None if any source lacks it."""

        slugs = self.sources(d)
        if self.first_only:
            slugs = slugs[:1]
        if not slugs:
            return None
        return self._sum((self.days[s].get(d) for s in slugs), self.sdef.info.attr)

    def state(
        self,
        h: datetime,
        flags: Mapping[date, DayCompleteness],
        held: set[date],
        series: Series,
    ) -> float | None:
        """Return the hour's state or None for unknown (spec 04 rules 1-6)."""

        info = self.sdef.info
        d = local_date(h)
        entry = flags.get(d)
        side_complete = entry is not None and entry.complete(info.direction)
        if d in held:  # rule 5
            return None
        slugs = self._slugs(h)
        if not slugs:  # no source community active that day: unknown
            return None
        point = self.hours[slugs[0]].get(h)
        if point is None:
            # Rule 1, except the autumn DST gap: Day residual once complete.
            if h != dst_gap_hour(d) or not side_complete:
                return None
            return self._dst_residual(d, h, series)
        value = self.hour(h)
        if value is None:  # rule 2
            return None
        if info.gated:
            return value if side_complete else None  # rule 3
        if not side_complete and point.faulty:  # rule 4
            return None
        return value

    def _dst_residual(self, d: date, gap: datetime, series: Series) -> float | None:
        total = self.day(d)
        if total is None:
            return None
        hourly_sum = 0.0
        for h in day_hours(d):
            if h == gap:
                continue
            value = self.hour(h)
            if value is None:
                return None
            hourly_sum += value
        residual = round(total - hourly_sum, 6)
        if residual < 0:
            series.dst_negative.append(d)
            key = self.sdef.key
            if (key, d) not in _warned_negative:
                _warned_negative.add((key, d))
                _LOGGER.warning(
                    "%s: Day value on %s is below the sum of its hours; "
                    "the autumn DST hour stays unknown",
                    key,
                    d.isoformat(),
                )
            return None
        series.dst_filled.append(gap)
        return residual + 0.0  # normalise -0.0


def derive_series(
    defs: Sequence[StatisticDef],
    series: dict[str, Series],
    prices: Mapping[datetime, float] | None,
    supplier_prices: Mapping[datetime, float] | None = None,
) -> None:
    """Fill the statistics computed from others (ADR 0008, 0015), in place.

    ``prices`` maps UTC hours to the surplus price in EUR/kWh; an hour without
    a price or without surplus kWh has no surplus income (ADR 0005). The
    feed-in compensation is community income plus surplus income, known only
    where both are. ``supplier_prices`` gives import savings (#73) and the
    supplier energy cost (#75) the same way.
    """

    by_quantity = {(d.community, d.quantity): d for d in defs}
    prices = prices or {}
    _derive_savings(defs, series, supplier_prices or {})

    def values(quantity: Quantity) -> dict[datetime, float]:
        sdef = by_quantity.get((None, quantity))
        return series[sdef.key].values if sdef and sdef.key in series else {}

    def priced(
        kwh: Mapping[datetime, float], price: Mapping[datetime, float]
    ) -> Series:
        return Series(
            {
                h: round(v * price[h], MONEY_DIGITS) + 0.0
                for h, v in kwh.items()
                if h in price
            },
            unpriced=sorted(h for h in kwh if h not in price),
        )

    # Issue #75: supplier kWh at the supplier's energy price.
    supplier_def = by_quantity.get((None, Quantity.SUPPLIER_EUR))
    if supplier_def is not None:
        series[supplier_def.key] = priced(
            values(Quantity.CONSUMPTION_FROM_SUPPLIER), supplier_prices or {}
        )

    surplus_def = by_quantity.get((None, Quantity.SURPLUS_EUR))
    if surplus_def is None:
        return
    surplus = priced(values(Quantity.GENERATION_TO_SURPLUS), prices)
    series[surplus_def.key] = surplus
    total_def = by_quantity.get((None, Quantity.FEED_IN_TOTAL_EUR))
    if total_def is not None:
        community = values(Quantity.FEED_IN_EUR)
        series[total_def.key] = Series(
            {
                h: round(community[h] + v, MONEY_DIGITS) + 0.0
                for h, v in surplus.values.items()
                if h in community
            }
        )


def _derive_savings(
    defs: Sequence[StatisticDef],
    series: dict[str, Series],
    prices: Mapping[datetime, float],
) -> None:
    """Import savings per community and site (issue #73, ADR 0008/0015).

    Per hour: community kWh x supplier energy price - community cost, where
    all three are known. Settled and Zero-corrected costs (#74, #89) are what
    this subtracts, so savings follow the invoices too.
    """

    for sdef in defs:
        if sdef.quantity is not Quantity.IMPORT_SAVINGS_EUR:
            continue
        prefix = sdef.key[: -len(sdef.quantity)]
        kwh = series.get(prefix + Quantity.CONSUMPTION_FROM_COMMUNITY)
        cost = series.get(prefix + Quantity.CONSUMPTION_EUR)
        if kwh is None or cost is None:
            continue
        series[sdef.key] = Series(
            {
                h: round(v * prices[h] - cost.values[h], MONEY_DIGITS) + 0.0
                for h, v in kwh.values.items()
                if h in prices and h in cost.values
            },
            unpriced=sorted(h for h in kwh.values if h not in prices),
        )


# ---------------------------------------------------------------------------
# Writing


@dataclass(slots=True)
class StoredRows:
    """What the recorder holds for one statistic around the window."""

    base: float  # sum of the last row before the window start, 0 if none
    states: dict[datetime, float]  # stored state per hour from the window start


@dataclass(slots=True)
class WriteResult:
    """Outcome for one statistic (for the run summary and diagnostics)."""

    key: str
    rows: int = 0
    kept_stored_hours: int = 0
    unknown_hours: int = 0
    first_unknown: datetime | None = None
    last_unknown: datetime | None = None
    newest_hour: datetime | None = None
    dst_filled: list[datetime] = field(default_factory=list)
    dst_negative: list[date] = field(default_factory=list)
    unpriced: list[datetime] = field(default_factory=list)


def _load_stored(
    hass: HomeAssistant, statistic_ids: set[str], window_start: datetime
) -> dict[str, StoredRows]:
    """Read base sums and stored states (runs in the recorder executor).

    One query per run over the lookback and the window. A statistic with no
    row in the lookback (first import, or a gap longer than the lookback) gets
    a second query over all of its history before the window, so a long gap
    cannot reset the sum to 0.
    """

    rows = statistics_during_period(
        hass,
        window_start - BASE_LOOKBACK,
        None,
        statistic_ids,
        "hour",
        None,
        {"state", "sum"},
    )
    result: dict[str, StoredRows] = {}
    start_ts = window_start.timestamp()
    missing: set[str] = set()
    for statistic_id in statistic_ids:
        base: float | None = None
        states: dict[datetime, float] = {}
        for row in rows.get(statistic_id, ()):
            if row["start"] < start_ts:
                base = row.get("sum")
            elif (state := row.get("state")) is not None:
                states[datetime.fromtimestamp(row["start"], UTC)] = state
        if base is None:
            missing.add(statistic_id)
        result[statistic_id] = StoredRows(base or 0.0, states)
    if missing:
        older = statistics_during_period(
            hass, _EPOCH, window_start, missing, "hour", None, {"sum"}
        )
        for statistic_id, old_rows in older.items():
            if old_rows and (last := old_rows[-1].get("sum")) is not None:
                result[statistic_id].base = last
    return result


async def async_load_stored(
    hass: HomeAssistant, defs: Sequence[StatisticDef], window_start: datetime
) -> dict[str, StoredRows]:
    """Return stored rows per statistic id; a later version may query cheaper."""

    return await get_instance(hass).async_add_executor_job(
        _load_stored, hass, {d.statistic_id for d in defs}, window_start
    )


def compute_rows(
    series: Mapping[datetime, float], stored: StoredRows, window_start: datetime
) -> tuple[list[StatisticData], WriteResult]:
    """Return the rows to import and the counts (spec 04 "Sum computation").

    The rewrite runs from the window start to the newest hour that is known
    in this run or stored, so no stored row after the window keeps a stale
    sum (test plan case 21). Stored rows win over unknown hours.
    """

    result = WriteResult(key="")
    candidates = [h for h in series if h >= window_start]
    candidates += [h for h in stored.states if h >= window_start]
    if not candidates:
        return [], result
    newest = max(candidates)
    result.newest_hour = newest
    running = stored.base
    rows: list[StatisticData] = []
    for h in _hours(window_start, newest + _HOUR):
        value = series.get(h)
        if value is None:
            result.unknown_hours += 1
            result.first_unknown = result.first_unknown or h
            result.last_unknown = h
            value = stored.states.get(h)
            if value is not None:
                result.kept_stored_hours += 1
        if value is None:
            continue
        running += value
        rows.append(StatisticData(start=h, state=value, sum=running))
    result.rows = len(rows)
    return rows, result


async def async_write_statistics(
    hass: HomeAssistant,
    defs: Sequence[StatisticDef],
    series: Mapping[str, Series],
    window_start: datetime,
) -> dict[str, WriteResult]:
    """Import one batch per statistic; return the results by statistic key.

    The recorder queues the imports; this does not wait for the commit.
    """

    stored = await async_load_stored(hass, defs, window_start)
    results: dict[str, WriteResult] = {}
    for sdef in defs:
        s = series.get(sdef.key) or Series()
        rows, result = compute_rows(s.values, stored[sdef.statistic_id], window_start)
        result.key = sdef.key
        result.dst_filled = list(s.dst_filled)
        result.dst_negative = list(s.dst_negative)
        result.unpriced = list(s.unpriced)
        results[sdef.key] = result
        if rows:
            async_add_external_statistics(hass, sdef.metadata(), rows)
    return results


async def async_import_window(
    hass: HomeAssistant,
    defs: Sequence[StatisticDef],
    hourly: Mapping[str, Sequence[AnalysisData]],
    completeness: Sequence[DayCompleteness],
    first_day: date,
    last_day: date,
    *,
    day_values: Mapping[str, Sequence[AnalysisData]] | None = None,
    held_days: Mapping[str, Collection[date]] | None = None,
    active_days: Mapping[str, Collection[date]] | None = None,
    prices: Mapping[datetime, float] | None = None,
    supplier_prices: Mapping[datetime, float] | None = None,
) -> dict[str, WriteResult]:
    """Build and write all statistics for one fetch window (S5 entry point).

    ``first_day``/``last_day`` are the window's local dates, both inclusive;
    rows before the local start of ``first_day`` are never touched. ``prices``
    is the surplus price per UTC hour in EUR/kWh (ADR 0015),
    ``supplier_prices`` the supplier energy price (issue #73).
    """

    series = build_series(
        defs,
        hourly,
        completeness,
        first_day,
        last_day,
        day_values=day_values,
        held_days=held_days,
        active_days=active_days,
    )
    derive_series(defs, series, prices, supplier_prices)
    return await async_write_statistics(hass, defs, series, local_day_start(first_day))
