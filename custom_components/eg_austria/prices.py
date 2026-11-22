"""Surplus and supplier price sources (ADR 0015, spec 04 "Money").

A price source turns into one price per UTC hour in EUR/kWh:

* ``number``: dated periods in ct/kWh; a period runs until the next one starts.
* ``sensor``: an entity with long-term statistics; the hourly mean is the price.
* ``table``: an entity attribute mapping ``YYYY-MM`` to a price for the month
  (for example the price table of the ha-oemag integration).

Hours without a price stay unknown (ADR 0005). Nothing here talks to the
portal.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
import re
from typing import Any, Final

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance

from .statistics import local_date

UNIT_CT: Final = "ct/kWh"
UNIT_EUR: Final = "EUR/kWh"
_UNIT_FACTORS: Final = {
    UNIT_CT: 0.01,
    "ct/kwh": 0.01,
    UNIT_EUR: 1.0,
    "eur/kwh": 1.0,
    "€/kwh": 1.0,
}
DEFAULT_TABLE_ATTRIBUTE: Final = "preise"
_MONTH_KEY: Final = re.compile(r"^(\d{4})-(\d{2})$")
_PERIOD_LINE: Final = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2})\s*[:=]\s*(-?\d+(?:[.,]\d+)?)\s*$"
)
_HOUR: Final = timedelta(hours=1)

# Option keys inside one price source dict.
KEY_SOURCE: Final = "source"
KEY_PERIODS: Final = "periods"
KEY_ENTITY: Final = "entity"
KEY_ATTRIBUTE: Final = "attribute"
KEY_UNIT: Final = "unit"


class SourceType(StrEnum):
    """How a price is provided."""

    NUMBER = "number"
    SENSOR = "sensor"
    TABLE = "table"


def unit_factor(unit: str | None) -> float | None:
    """Return the factor from ``unit`` to EUR/kWh, None if unsupported."""

    if unit is None:
        return None
    return _UNIT_FACTORS.get(unit.strip().lower())


# ---------------------------------------------------------------------------
# Periods (number source)


def parse_periods(text: str) -> list[tuple[date, float]]:
    """Parse ``YYYY-MM-DD: price`` lines (ct/kWh), sorted by date.

    Empty lines are skipped. Raises ``ValueError`` naming the first bad line
    (1-based) or a repeated date.
    """

    periods: dict[date, float] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        match = _PERIOD_LINE.match(line)
        if match is None:
            raise ValueError(f"line {number}")
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError as err:
            raise ValueError(f"line {number}") from err
        if day in periods:
            raise ValueError(f"line {number}")
        periods[day] = float(match.group(2).replace(",", "."))
    if not periods:
        raise ValueError("empty")
    return sorted(periods.items())


def format_periods(periods: Iterable[Sequence[Any]]) -> str:
    """Return periods as the text the options form shows."""

    return "\n".join(f"{day}: {price:g}" for day, price in periods)


def price_on(periods: Sequence[tuple[date, float]], day: date) -> float | None:
    """Return the period price (as stored) valid on ``day``, None before the first."""

    price: float | None = None
    for start, value in periods:
        if start > day:
            break
        price = value
    return price


# ---------------------------------------------------------------------------
# Source


@dataclass(frozen=True, slots=True)
class PriceSource:
    """One configured price (ADR 0015)."""

    source: SourceType
    periods: tuple[tuple[date, float], ...] = ()  # number: ct/kWh
    entity: str | None = None  # sensor, table
    attribute: str = DEFAULT_TABLE_ATTRIBUTE  # table
    unit: str = UNIT_CT  # table

    @classmethod
    def from_options(cls, raw: Mapping[str, Any] | None) -> PriceSource | None:
        """Return the source stored in the options, None if not set or broken."""

        if not raw:
            return None
        try:
            source = SourceType(raw[KEY_SOURCE])
            if source is SourceType.NUMBER:
                periods = tuple(
                    (date.fromisoformat(str(d)), float(p)) for d, p in raw[KEY_PERIODS]
                )
                return cls(source, periods=tuple(sorted(periods))) if periods else None
            entity = str(raw[KEY_ENTITY])
            if source is SourceType.SENSOR:
                return cls(source, entity=entity)
            return cls(
                source,
                entity=entity,
                attribute=str(raw.get(KEY_ATTRIBUTE) or DEFAULT_TABLE_ATTRIBUTE),
                unit=str(raw.get(KEY_UNIT) or UNIT_CT),
            )
        except KeyError, TypeError, ValueError:
            return None

    def as_options(self) -> dict[str, Any]:
        """Return the dict stored in the options."""

        if self.source is SourceType.NUMBER:
            return {
                KEY_SOURCE: self.source.value,
                KEY_PERIODS: [[d.isoformat(), p] for d, p in self.periods],
            }
        out: dict[str, Any] = {KEY_SOURCE: self.source.value, KEY_ENTITY: self.entity}
        if self.source is SourceType.TABLE:
            out |= {KEY_ATTRIBUTE: self.attribute, KEY_UNIT: self.unit}
        return out

    @property
    def late_months(self) -> int:
        """Months before the current one whose price may still be unpublished."""

        return 1 if self.source is SourceType.TABLE else 0


@dataclass(slots=True)
class HourlyPrices:
    """Prices of a window in EUR/kWh per UTC hour."""

    prices: dict[datetime, float] = field(default_factory=dict)


def month_table(raw: Any, unit: str) -> dict[tuple[int, int], float] | None:
    """Return ``{(year, month): EUR/kWh}`` from a table attribute, None if invalid."""

    factor = unit_factor(unit)
    if not isinstance(raw, Mapping) or factor is None:
        return None
    table: dict[tuple[int, int], float] = {}
    for key, value in raw.items():
        match = _MONTH_KEY.match(str(key))
        if match is None or isinstance(value, bool):
            return None
        if not isinstance(value, int | float):
            return None
        table[(int(match.group(1)), int(match.group(2)))] = float(value) * factor
    return table


def _sensor_means(
    hass: HomeAssistant, entity: str, start: datetime, end: datetime
) -> dict[datetime, float]:
    rows = statistics_during_period(hass, start, end, {entity}, "hour", None, {"mean"})
    return {
        datetime.fromtimestamp(row["start"], UTC): mean
        for row in rows.get(entity, ())
        if (mean := row.get("mean")) is not None
    }


async def async_hourly_prices(
    hass: HomeAssistant, source: PriceSource, hours: Sequence[datetime]
) -> HourlyPrices:
    """Return the price of each hour in ``hours`` that has one (EUR/kWh)."""

    result = HourlyPrices()
    if not hours:
        return result
    if source.source is SourceType.NUMBER:
        for h in hours:
            price = price_on(source.periods, local_date(h))
            if price is not None:
                result.prices[h] = price * 0.01
        return result
    if source.entity is None:  # pragma: no cover - from_options sets it
        return result
    if source.source is SourceType.TABLE:
        state = hass.states.get(source.entity)
        raw = state.attributes.get(source.attribute) if state is not None else None
        table = month_table(raw, source.unit) or {}
        for h in hours:
            d = local_date(h)
            if (price := table.get((d.year, d.month))) is not None:
                result.prices[h] = price
        return result
    state = hass.states.get(source.entity)
    factor = unit_factor(
        state.attributes.get("unit_of_measurement") if state is not None else None
    )
    if factor is None:
        return result
    means = await get_instance(hass).async_add_executor_job(
        _sensor_means, hass, source.entity, min(hours), max(hours) + _HOUR
    )
    for h in hours:
        if (mean := means.get(h)) is not None:
            result.prices[h] = mean * factor
    return result


def first_unpriced(
    kwh_hours: Iterable[datetime],
    prices: Mapping[datetime, float],
    source: PriceSource,
    today: date,
) -> date | None:
    """Return the first local date with kWh but no price that is overdue.

    A monthly table may lack the current month and ``late_months`` before it;
    those hours are expected to get a price later and are not reported.
    """

    first_due = date(today.year, today.month, 1)
    for _ in range(source.late_months):
        first_due = (first_due - timedelta(days=1)).replace(day=1)
    missing = [
        local_date(h)
        for h in kwh_hours
        if h not in prices
        and (source.source is not SourceType.TABLE or local_date(h) < first_due)
    ]
    return min(missing) if missing else None
