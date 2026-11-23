"""Daily Zero estimate and derived Zero rates (issue #89, ADR 0008).

The portal's hourly ``earnings`` / ``spendings`` price Zero kWh at the
standard rate. Zero is netted per day: for a community with an own-site
Zero plan (sending and receiving point both in the site),

    Z_d = min(Σ_day generation to community, Σ_day consumption from community)

and those kWh are spread over the day's hours in proportion to the hourly
community kWh of each direction, so an hour's Zero share is
``r_d = Z_d / Σ_day community kWh`` (per community, direction and day).

Zero rates are nowhere in the API. They are derived from settled months
(``settlement.py``): with ``Z`` the month's estimated Zero kWh (≥ 1 kWh)

    zero_rate = (settled - Σ_hours money * (1 - r_d)) / Z

per community and direction. A Zero kWh is then repriced from the hour's
standard rate to the Zero rate:

    money' = money * (1 - r_d) + community kWh * r_d * zero_rate

Settled months use their own derived rate (then the settlement scaling is
close to 1), other months the latest derived rate of that community and
direction. Without a derived rate, Zero kWh stay at the standard rate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
import math
from typing import Final

from .models import AnalysisData, Direction, EnergyPoint
from .settlement import Key, Month, Settlements
from .statistics import local_date

# A month needs this many estimated Zero kWh before a rate is derived.
MIN_ZERO_KWH: Final = 1.0

ShareKey = tuple[str, Direction, date]  # (community slug, direction, local day)


@dataclass(frozen=True, slots=True)
class ZeroRate:
    """A derived Zero rate (EUR/kWh) and the settled month it comes from."""

    month: Month
    rate: float


@dataclass(slots=True)
class ZeroEstimate:
    """Zero shares per day and estimated Zero kWh per month."""

    shares: dict[ShareKey, float]
    month_kwh: dict[tuple[str, Month], float]


def _month(day: date) -> Month:
    return day.year, day.month


def estimate(
    hourly: Mapping[str, Iterable[AnalysisData]], own_zero_on: Callable[[date], bool]
) -> ZeroEstimate:
    """Return the Zero share per community, direction and day (spec 04)."""

    sums: dict[ShareKey, float] = defaultdict(float)
    unknown: set[tuple[str, date]] = set()
    days: set[tuple[str, date]] = set()
    for slug, responses in hourly.items():
        for data in responses:
            for direction in Direction:
                for point in data.points(direction):
                    day = local_date(point.start)
                    if not own_zero_on(day):
                        continue
                    days.add((slug, day))
                    if point.community is None:
                        unknown.add((slug, day))
                    else:
                        sums[slug, direction, day] += point.community
    shares: dict[ShareKey, float] = {}
    month_kwh: dict[tuple[str, Month], float] = defaultdict(float)
    for slug, day in days:
        if (slug, day) in unknown:
            continue  # an unknown hour: the day's Zero is unknown too
        gen = sums.get((slug, Direction.GENERATION, day), 0.0)
        cons = sums.get((slug, Direction.CONSUMPTION, day), 0.0)
        zero = min(gen, cons)
        if zero <= 0:
            continue
        shares[slug, Direction.GENERATION, day] = zero / gen
        shares[slug, Direction.CONSUMPTION, day] = zero / cons
        month_kwh[slug, _month(day)] += zero
    return ZeroEstimate(shares, dict(month_kwh))


def derive_rates(
    hourly: Mapping[str, Iterable[AnalysisData]],
    zero: ZeroEstimate,
    settlements: Settlements,
) -> dict[Key, float]:
    """Return the Zero rate of every settled month with enough Zero kWh."""

    non_zero: dict[Key, float] = defaultdict(float)
    for slug, responses in hourly.items():
        for data in responses:
            for direction in Direction:
                for point in data.points(direction):
                    if point.money is None:
                        continue
                    day = local_date(point.start)
                    share = zero.shares.get((slug, direction, day), 0.0)
                    non_zero[slug, direction, _month(day)] += point.money * (1 - share)
    rates: dict[Key, float] = {}
    for (slug, month), kwh in zero.month_kwh.items():
        if kwh < MIN_ZERO_KWH:
            continue
        for direction in Direction:
            key = (slug, direction, month)
            settled = settlements.settled(key)
            if settled is None or key not in non_zero:
                continue
            rate = (settled - non_zero[key]) / kwh
            # A negative rate can't be a tariff: the estimate didn't fit.
            if math.isfinite(rate) and rate >= 0:
                rates[key] = rate
    return rates


def latest_rates(
    stored: Mapping[tuple[str, Direction], ZeroRate], derived: Mapping[Key, float]
) -> dict[tuple[str, Direction], ZeroRate]:
    """Keep the rate of the latest settled month per community and direction."""

    result = dict(stored)
    for (slug, direction, month), rate in derived.items():
        current = result.get((slug, direction))
        if current is None or month >= current.month:
            result[slug, direction] = ZeroRate(month, rate)
    return result


def _reprice_point(
    point: EnergyPoint, share: float | None, rate: float | None
) -> EnergyPoint:
    if not share or rate is None or point.money is None or point.community is None:
        return point
    money = point.money * (1 - share) + point.community * share * rate
    return replace(point, money=money)


def reprice(
    responses: Mapping[str, list[AnalysisData]],
    zero: ZeroEstimate,
    rate_for: Callable[[str, Direction, Month], float | None],
) -> dict[str, list[AnalysisData]]:
    """Return the responses with each hour's Zero kWh at the Zero rate."""

    if not zero.shares:
        return {slug: list(items) for slug, items in responses.items()}

    def one(slug: str, data: AnalysisData) -> AnalysisData:
        def points(direction: Direction) -> tuple[EnergyPoint, ...]:
            out = []
            for point in data.points(direction):
                day = local_date(point.start)
                share = zero.shares.get((slug, direction, day))
                rate = rate_for(slug, direction, _month(day)) if share else None
                out.append(_reprice_point(point, share, rate))
            return tuple(out)

        return replace(
            data,
            consumption=points(Direction.CONSUMPTION),
            generation=points(Direction.GENERATION),
        )

    return {slug: [one(slug, d) for d in items] for slug, items in responses.items()}


def rate_lookup(
    derived: Mapping[Key, float], latest: Mapping[tuple[str, Direction], ZeroRate]
) -> Callable[[str, Direction, Month], float | None]:
    """Return a lookup: the settled month's own rate, else the latest one."""

    def rate_for(slug: str, direction: Direction, month: Month) -> float | None:
        own = derived.get((slug, direction, month))
        if own is not None:
            return own
        found = latest.get((slug, direction))
        return found.rate if found is not None else None

    return rate_for


def rates_to_store(
    rates: Mapping[tuple[str, Direction], ZeroRate],
) -> dict[str, dict[str, object]]:
    """``{"<slug>/<direction>": {"month": "YYYY-MM", "rate": float}}``."""

    return {
        f"{slug}/{direction.value}": {
            "month": f"{r.month[0]:04d}-{r.month[1]:02d}",
            "rate": round(r.rate, 6),
        }
        for (slug, direction), r in sorted(
            rates.items(), key=lambda kv: (kv[0][0], kv[0][1].value)
        )
    }


def rates_from_store(raw: object) -> dict[tuple[str, Direction], ZeroRate]:
    """Read the stored rates; broken entries are skipped."""

    result: dict[tuple[str, Direction], ZeroRate] = {}
    if not isinstance(raw, Mapping):
        return result
    for key, value in raw.items():
        try:
            slug, direction = str(key).rsplit("/", 1)
            year, month = str(value["month"]).split("-")
            rate = float(value["rate"])
            result[slug, Direction(direction)] = ZeroRate((int(year), int(month)), rate)
        except KeyError, TypeError, ValueError:
            continue
    return result
