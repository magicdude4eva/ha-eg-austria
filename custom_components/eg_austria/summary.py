"""Summary values: shares over the complete hours (ADR 0014, issue #51).

Core cards can't divide statistics, so the integration computes a few
percentages from the recorder's hourly statistics: the ``eg_austria:``
statistics and the comparison sources (#50). Numerator and denominator always
cover the same hours: from the period start up to *Data complete until*.

Two periods: the month so far (current calendar month up to the complete
date) and the last complete month (the latest month whose days are all
complete). Unknown is not zero (ADR 0005): no complete day or a denominator of
0 gives ``None``.

Issue #75 adds € values over the same hours: the sums of the money
statistics (ADR 0008, 0015), with a settled / provisional marker from the
settled months of the communities they depend on.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
import logging
from typing import Final

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance

from .const import (
    CONF_SOURCE_FEED_IN,
    CONF_SOURCE_GRID_CONSUMPTION,
    CONF_SOURCE_PV,
    DOMAIN,
)
from .models import Direction
from .statistics import Quantity, local_date, local_day_start

_LOGGER = logging.getLogger(__name__)

PRECISION: Final = 1  # decimals of a share in percent
KWH_PRECISION: Final = 3
EUR_PRECISION: Final = 2


class Period(StrEnum):
    """The two summary periods."""

    MONTH_SO_FAR = "month_so_far"
    LAST_MONTH = "last_month"


@dataclass(frozen=True, slots=True)
class DateRange:
    """Local dates, both inclusive."""

    start: date
    end: date


def month_so_far(today: date, complete_until: date | None) -> DateRange | None:
    """Return the 1st of the current month up to the complete date, if any."""

    if complete_until is None:
        return None
    first = today.replace(day=1)
    if complete_until < first:
        return None
    return DateRange(first, min(complete_until, today))


def last_complete_month(complete_until: date | None) -> DateRange | None:
    """Return the latest calendar month whose days are all complete."""

    if complete_until is None:
        return None
    if (complete_until + timedelta(days=1)).day == 1:
        end = complete_until
    else:
        end = complete_until.replace(day=1) - timedelta(days=1)
    return DateRange(end.replace(day=1), end)


def period_range(
    period: Period, today: date, complete_until: date | None
) -> DateRange | None:
    """Return the dates of one period."""

    if period is Period.MONTH_SO_FAR:
        return month_so_far(today, complete_until)
    return last_complete_month(complete_until)


# ---------------------------------------------------------------------------
# Value definitions

Totals = Mapping[str, float | None]


@dataclass(frozen=True, slots=True)
class SummaryDef:
    """One percentage: ``numerator / denominator`` or a deviation."""

    key: str  # unique within the entry, e.g. "feed_in_to_communities"
    translation_key: str
    numerator: Callable[[Totals], float | None]
    denominator: str  # statistic id
    statistic_ids: frozenset[str]  # everything the value reads
    community: str | None = None  # community display name (placeholder)
    deviation: bool = False  # (numerator - denominator) / denominator
    clamp: bool = False  # limit the share to 0..100


@dataclass(frozen=True, slots=True)
class SummaryValue:
    """A computed value with what it was computed from."""

    value: float | None
    numerator_kwh: float | None
    denominator_kwh: float | None


class Settlement(StrEnum):
    """Whether the € value of a period is final (issue #75, ADR 0008)."""

    SETTLED = "settled"  # every community month it depends on is settled
    PARTLY_SETTLED = "partly_settled"
    PROVISIONAL = "provisional"


# Settled months ("YYYY-MM") per "<community slug>/<direction>" (StoreData).
SettledMonths = Mapping[str, Collection[str]]


@dataclass(frozen=True, slots=True)
class MoneyDef:
    """One € value: the sum of a money statistic over the period."""

    key: str
    translation_key: str
    statistic_id: str
    community: str | None = None  # community display name (placeholder)
    # Community settlements that make the value final; empty: no marker
    # (surplus income is priced by the surplus buyer, not settled here).
    settles: tuple[tuple[str, Direction], ...] = ()


@dataclass(frozen=True, slots=True)
class MoneyValue:
    """A € sum and whether it is settled."""

    value: float | None
    settlement: Settlement | None


@dataclass(frozen=True, slots=True)
class PeriodSummary:
    """All values of one period."""

    dates: DateRange | None
    values: Mapping[str, SummaryValue] = field(default_factory=dict)
    money: Mapping[str, MoneyValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommunityName:
    """A community slug and its display name."""

    slug: str
    name: str


def _stat(site: str, quantity: Quantity, community: str | None = None) -> str:
    middle = f"{community}_" if community else ""
    return f"{DOMAIN}:{site}_{middle}{quantity}"


def _total(stat_id: str) -> Callable[[Totals], float | None]:
    return lambda totals: totals.get(stat_id)


def _difference(minuend: str, subtrahend: str) -> Callable[[Totals], float | None]:
    def compute(totals: Totals) -> float | None:
        a, b = totals.get(minuend), totals.get(subtrahend)
        if a is None or b is None:
            return None
        return a - b

    return compute


def _share(
    key: str,
    translation_key: str,
    numerator: str,
    denominator: str,
    *,
    community: str | None = None,
    deviation: bool = False,
) -> SummaryDef:
    return SummaryDef(
        key=key,
        translation_key=translation_key,
        numerator=_total(numerator),
        denominator=denominator,
        statistic_ids=frozenset({numerator, denominator}),
        community=community,
        deviation=deviation,
    )


def build_summary_defs(
    site: str,
    communities: Sequence[CommunityName],
    sources: Mapping[str, str | None],
) -> list[SummaryDef]:
    """Return the values of one entry; source values only for set sources."""

    generation = _stat(site, Quantity.GENERATION)
    consumption = _stat(site, Quantity.CONSUMPTION)
    to_communities = _stat(site, Quantity.GENERATION_TO_COMMUNITY)
    from_communities = _stat(site, Quantity.CONSUMPTION_FROM_COMMUNITY)
    to_surplus = _stat(site, Quantity.GENERATION_TO_SURPLUS)

    defs = [
        _share(
            "feed_in_to_communities",
            "feed_in_to_communities",
            to_communities,
            generation,
        ),
        _share(
            "import_from_communities",
            "import_from_communities",
            from_communities,
            consumption,
        ),
    ]
    for c in communities:
        defs += [
            _share(
                f"{c.slug}_feed_in_share",
                "community_feed_in_share",
                _stat(site, Quantity.GENERATION_TO_COMMUNITY, c.slug),
                generation,
                community=c.name,
            ),
            _share(
                f"{c.slug}_import_share",
                "community_import_share",
                _stat(site, Quantity.CONSUMPTION_FROM_COMMUNITY, c.slug),
                consumption,
                community=c.name,
            ),
        ]
    pv = sources.get(CONF_SOURCE_PV)
    if pv:
        defs += [
            SummaryDef(
                key="pv_self_consumed",
                translation_key="pv_self_consumed",
                numerator=_difference(pv, generation),
                denominator=pv,
                statistic_ids=frozenset({pv, generation}),
                clamp=True,
            ),
            _share("pv_to_communities", "pv_to_communities", to_communities, pv),
            _share("pv_to_surplus", "pv_to_surplus", to_surplus, pv),
        ]
    for source_key, key, metered in (
        (CONF_SOURCE_FEED_IN, "feed_in_deviation", generation),
        (CONF_SOURCE_GRID_CONSUMPTION, "grid_consumption_deviation", consumption),
    ):
        source = sources.get(source_key)
        if source:
            defs.append(_share(key, key, source, metered, deviation=True))
    return defs


def build_money_defs(
    site: str,
    communities: Sequence[CommunityName],
    *,
    surplus: bool = False,
    supplier: bool = False,
) -> list[MoneyDef]:
    """Return the € values of one entry (issue #75).

    Surplus income and the total feed-in compensation need a surplus price,
    import savings a supplier price (ADR 0015); without one they aren't
    created, like their statistics.
    """

    feed_in = tuple((c.slug, Direction.GENERATION) for c in communities)
    consumption = tuple((c.slug, Direction.CONSUMPTION) for c in communities)
    defs = [
        MoneyDef(
            "feed_in_income",
            "feed_in_income",
            _stat(site, Quantity.FEED_IN_EUR),
            settles=feed_in,
        ),
        MoneyDef(
            "consumption_cost",
            "consumption_cost",
            _stat(site, Quantity.CONSUMPTION_EUR),
            settles=consumption,
        ),
    ]
    if surplus:
        defs += [
            MoneyDef(
                "surplus_income", "surplus_income", _stat(site, Quantity.SURPLUS_EUR)
            ),
            MoneyDef(
                "feed_in_compensation",
                "feed_in_compensation",
                _stat(site, Quantity.FEED_IN_TOTAL_EUR),
                settles=feed_in,
            ),
        ]
    if supplier:
        defs.append(
            MoneyDef(
                "import_savings",
                "import_savings",
                _stat(site, Quantity.IMPORT_SAVINGS_EUR),
                settles=consumption,
            )
        )
    for c in communities:
        defs += [
            MoneyDef(
                f"{c.slug}_feed_in_income",
                "community_feed_in_income",
                _stat(site, Quantity.FEED_IN_EUR, c.slug),
                community=c.name,
                settles=((c.slug, Direction.GENERATION),),
            ),
            MoneyDef(
                f"{c.slug}_consumption_cost",
                "community_consumption_cost",
                _stat(site, Quantity.CONSUMPTION_EUR, c.slug),
                community=c.name,
                settles=((c.slug, Direction.CONSUMPTION),),
            ),
        ]
        if supplier:
            defs.append(
                MoneyDef(
                    f"{c.slug}_import_savings",
                    "community_import_savings",
                    _stat(site, Quantity.IMPORT_SAVINGS_EUR, c.slug),
                    community=c.name,
                    settles=((c.slug, Direction.CONSUMPTION),),
                )
            )
    return defs


def settlement_state(
    settles: Collection[tuple[str, Direction]], month: str, settled: SettledMonths
) -> Settlement | None:
    """Settled, partly settled or provisional for one month (``YYYY-MM``)."""

    if not settles:
        return None
    done = sum(
        1
        for slug, direction in settles
        if month in settled.get(f"{slug}/{direction.value}", ())
    )
    if done == len(settles):
        return Settlement.SETTLED
    return Settlement.PARTLY_SETTLED if done else Settlement.PROVISIONAL


def compute_money(
    d: MoneyDef, totals: Totals, month: str, settled: SettledMonths
) -> MoneyValue:
    """Compute one € value; unknown (no rows) has no marker either."""

    total = totals.get(d.statistic_id)
    if total is None:
        return MoneyValue(None, None)
    return MoneyValue(
        round(total, EUR_PRECISION), settlement_state(d.settles, month, settled)
    )


def compute_value(d: SummaryDef, totals: Totals) -> SummaryValue:
    """Compute one value from period totals (kWh per statistic id)."""

    numerator = d.numerator(totals)
    denominator = totals.get(d.denominator)
    value: float | None = None
    if numerator is not None and denominator:
        if d.deviation:
            value = (numerator - denominator) / denominator * 100
        else:
            value = numerator / denominator * 100
        if d.clamp:
            value = min(max(value, 0.0), 100.0)
        value = round(value, PRECISION)
    return SummaryValue(
        value,
        None if numerator is None else round(numerator, KWH_PRECISION),
        None if denominator is None else round(denominator, KWH_PRECISION),
    )


def meter_data_until(newest: Mapping[str, datetime | None], site: str) -> date | None:
    """Last local day whose meter values reach its last hour.

    ``newest`` maps statistic keys to the start (UTC) of their newest known
    hour. Uses the meter statistics of the site (consumption, generation)
    that exist; the earlier of the two wins. ``None`` without any.
    """

    days = [
        local_date(hour + timedelta(hours=1)) - timedelta(days=1)
        for quantity in (Quantity.CONSUMPTION, Quantity.GENERATION)
        if (hour := newest.get(f"{site}_{quantity}")) is not None
    ]
    return min(days) if days else None


# ---------------------------------------------------------------------------
# Recorder


def _load_totals(
    hass: HomeAssistant, dates: DateRange, statistic_ids: set[str]
) -> dict[str, float | None]:
    """Sum the hourly changes per statistic (runs in the recorder executor)."""

    rows = statistics_during_period(
        hass,
        local_day_start(dates.start),
        local_day_start(dates.end + timedelta(days=1)),
        statistic_ids,
        "hour",
        {"energy": "kWh"},
        {"change"},
    )
    totals: dict[str, float | None] = {}
    for stat_id in statistic_ids:
        changes = [
            float(change)
            for r in rows.get(stat_id, [])
            if (change := r.get("change")) is not None
        ]
        totals[stat_id] = sum(changes) if changes else None
    return totals


async def async_compute(
    hass: HomeAssistant,
    defs: Collection[SummaryDef],
    today: date,
    complete_until: date | None,
    money_defs: Collection[MoneyDef] = (),
    settled: SettledMonths | None = None,
) -> dict[Period, PeriodSummary]:
    """Compute every value for both periods.

    Both periods lie in one calendar month, so one month decides the
    settled / provisional marker of the € values.
    """

    statistic_ids = {sid for d in defs for sid in d.statistic_ids} | {
        d.statistic_id for d in money_defs
    }
    result: dict[Period, PeriodSummary] = {}
    for period in Period:
        dates = period_range(period, today, complete_until)
        if dates is None or not statistic_ids:
            result[period] = PeriodSummary(dates)
            continue
        totals = await get_instance(hass).async_add_executor_job(
            _load_totals, hass, dates, statistic_ids
        )
        month = f"{dates.start.year:04d}-{dates.start.month:02d}"
        result[period] = PeriodSummary(
            dates,
            {d.key: compute_value(d, totals) for d in defs},
            {d.key: compute_money(d, totals, month, settled or {}) for d in money_defs},
        )
    return result
