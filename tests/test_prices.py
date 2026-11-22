"""Tests for prices.py (ADR 0015)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.eg_austria.prices import (
    PriceSource,
    SourceType,
    async_hourly_prices,
    first_unpriced,
    format_periods,
    month_table,
    parse_periods,
    price_on,
    unit_factor,
)
from custom_components.eg_austria.statistics import day_hours, local_day_start


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before ``hass``."""


def test_parse_and_format_periods() -> None:
    """Lines are parsed, sorted and formatted back."""

    periods = parse_periods("2031-03-15: 11\n\n2030-02-20 = 9,75\n")
    assert periods == [(date(2030, 2, 20), 9.75), (date(2031, 3, 15), 11.0)]
    assert format_periods(periods) == "2030-02-20: 9.75\n2031-03-15: 11"


@pytest.mark.parametrize(
    "text",
    ["", "2026-01-01", "2026-13-01: 5", "x: 5", "2026-01-01: 5\n2026-01-01: 6"],
)
def test_parse_periods_rejects(text: str) -> None:
    """Bad lines, bad dates, repeats and empty input are rejected."""

    with pytest.raises(ValueError, match=r"line|empty"):
        parse_periods(text)


def test_price_on() -> None:
    """A period runs until the next one; nothing before the first."""

    periods = [(date(2030, 1, 1), 5.0), (date(2030, 7, 1), 6.0)]
    assert price_on(periods, date(2029, 12, 31)) is None
    assert price_on(periods, date(2030, 6, 30)) == 5.0
    assert price_on(periods, date(2030, 7, 1)) == 6.0


def test_unit_factor() -> None:
    """Ct and EUR per kWh are supported, others not."""

    assert unit_factor("ct/kWh") == 0.01
    assert unit_factor("EUR/kWh") == 1.0
    assert unit_factor("€/kWh") == 1.0
    assert unit_factor("EUR") is None
    assert unit_factor(None) is None


def test_source_options_roundtrip() -> None:
    """Each source type survives the options dict; broken ones are None."""

    number = PriceSource(SourceType.NUMBER, periods=((date(2026, 1, 1), 7.5),))
    sensor = PriceSource(SourceType.SENSOR, entity="sensor.price")
    table = PriceSource(
        SourceType.TABLE, entity="sensor.t", attribute="preise", unit="ct/kWh"
    )
    for source in (number, sensor, table):
        assert PriceSource.from_options(source.as_options()) == source
    assert PriceSource.from_options(None) is None
    assert PriceSource.from_options({"source": "bogus"}) is None
    assert PriceSource.from_options({"source": "number", "periods": []}) is None
    assert PriceSource.from_options({"source": "sensor"}) is None
    assert table.late_months == 1
    assert number.late_months == 0


def test_month_table() -> None:
    """Month keys and numbers only; converted to EUR/kWh."""

    assert month_table({"2026-10": 14.874}, "ct/kWh") == {
        (2026, 10): pytest.approx(0.14874)
    }
    assert month_table({"2026-10": 0.1}, "EUR/kWh") == {(2026, 10): 0.1}
    assert month_table({"October": 1}, "ct/kWh") is None
    assert month_table({"2026-10": "1"}, "ct/kWh") is None
    assert month_table({"2026-10": True}, "ct/kWh") is None
    assert month_table([1], "ct/kWh") is None
    assert month_table({"2026-10": 1}, "kWh") is None


async def test_hourly_prices_number(hass: HomeAssistant) -> None:
    """Number periods apply per Austrian local date, in EUR/kWh."""

    source = PriceSource(SourceType.NUMBER, periods=((date(2026, 6, 2), 10.0),))
    hours = day_hours(date(2026, 6, 1)) + day_hours(date(2026, 6, 2))
    prices = (await async_hourly_prices(hass, source, hours)).prices
    assert len(prices) == 24
    assert prices[local_day_start(date(2026, 6, 2))] == pytest.approx(0.1)
    assert (await async_hourly_prices(hass, source, [])).prices == {}


async def test_hourly_prices_table(hass: HomeAssistant) -> None:
    """A month table prices every hour of its months."""

    hass.states.async_set("sensor.t", "2", {"preise": {"2026-06": 8.0}})
    source = PriceSource(SourceType.TABLE, entity="sensor.t")
    hours = day_hours(date(2026, 6, 30)) + day_hours(date(2026, 7, 1))
    prices = (await async_hourly_prices(hass, source, hours)).prices
    assert len(prices) == 24
    assert all(v == pytest.approx(0.08) for v in prices.values())
    missing = PriceSource(SourceType.TABLE, entity="sensor.none")
    assert (await async_hourly_prices(hass, missing, hours)).prices == {}


async def test_hourly_prices_sensor(hass: HomeAssistant, recorder_mock) -> None:
    """A sensor's hourly statistics mean is the price."""

    h = datetime(2026, 6, 1, 10, tzinfo=UTC)
    hass.states.async_set(
        "sensor.spot",
        "0.1",
        {"unit_of_measurement": "EUR/kWh", "state_class": "measurement"},
    )
    async_import_statistics(
        hass,
        StatisticMetaData(
            source="recorder",
            statistic_id="sensor.spot",
            name=None,
            unit_of_measurement="EUR/kWh",
            unit_class=None,
            has_sum=False,
            mean_type=StatisticMeanType.ARITHMETIC,
        ),
        [StatisticData(start=h, mean=0.2, min=0.2, max=0.2)],
    )
    await async_wait_recording_done(hass)
    source = PriceSource(SourceType.SENSOR, entity="sensor.spot")
    prices = await async_hourly_prices(hass, source, [h, h + timedelta(hours=1)])
    assert prices.prices == {h: pytest.approx(0.2)}
    hass.states.async_set("sensor.spot", "1", {"unit_of_measurement": "W"})
    assert (await async_hourly_prices(hass, source, [h])).prices == {}


def test_first_unpriced() -> None:
    """Overdue days only; a table may lack the current and previous month."""

    number = PriceSource(SourceType.NUMBER, periods=((date(2026, 1, 1), 1.0),))
    table = PriceSource(SourceType.TABLE, entity="sensor.t")
    sep = local_day_start(date(2026, 9, 15))
    oct_ = local_day_start(date(2026, 10, 15))
    nov = local_day_start(date(2026, 11, 15))
    today = date(2026, 11, 22)
    assert first_unpriced([nov, oct_], {}, number, today) == date(2026, 10, 15)
    assert first_unpriced([nov, oct_], {}, table, today) is None
    assert first_unpriced([nov, sep], {}, table, today) == date(2026, 9, 15)
    assert first_unpriced([sep], {sep: 0.1}, number, today) is None
