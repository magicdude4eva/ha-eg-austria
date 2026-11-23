"""Summary values and sensors (ADR 0014, issue #51). Synthetic values only."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.eg_austria import summary as sm
from custom_components.eg_austria.const import (
    CONF_SOURCE_FEED_IN,
    CONF_SOURCE_GRID_CONSUMPTION,
    CONF_SOURCE_PV,
    CONF_SUPPLIER_PRICE,
    CONF_SURPLUS_PRICE,
    DOMAIN,
)
from custom_components.eg_austria.models import Direction
from custom_components.eg_austria.statistics import (
    Quantity,
    StatisticDef,
    day_hours,
)
from custom_components.eg_austria.summary import (
    CommunityName,
    DateRange,
    Period,
    PeriodSummary,
    SummaryValue,
)

from .conftest import async_setup_fake_entry

NOW = datetime(2026, 6, 8, 4, 0, tzinfo=UTC)
EXP = int(NOW.timestamp()) + 200 * 86400
BEG = CommunityName("beg", "Example BEG")
EEG = CommunityName("eeg", "Example EEG")
NO_SOURCES: dict[str, str | None] = {}
ALL_SOURCES = {
    CONF_SOURCE_FEED_IN: "example:feed_in",
    CONF_SOURCE_GRID_CONSUMPTION: "example:grid_import",
    CONF_SOURCE_PV: "example:pv",
}


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before ``hass`` (see test_statistics.py)."""


# ---------------------------------------------------------------------------
# Periods


def test_month_so_far() -> None:
    """From the 1st up to the complete date; none before the month starts."""

    today = date(2026, 6, 18)
    assert sm.month_so_far(today, None) is None
    assert sm.month_so_far(today, date(2026, 5, 31)) is None
    assert sm.month_so_far(today, date(2026, 6, 9)) == DateRange(
        date(2026, 6, 1), date(2026, 6, 9)
    )
    # Never beyond today.
    assert sm.month_so_far(today, date(2026, 6, 30)) == DateRange(
        date(2026, 6, 1), today
    )


@pytest.mark.parametrize(
    ("complete", "expected"),
    [
        (None, None),
        (date(2026, 6, 9), DateRange(date(2026, 5, 1), date(2026, 5, 31))),
        (date(2026, 5, 31), DateRange(date(2026, 5, 1), date(2026, 5, 31))),
        (date(2031, 1, 15), DateRange(date(2030, 12, 1), date(2030, 12, 31))),
        (date(2026, 2, 28), DateRange(date(2026, 2, 1), date(2026, 2, 28))),
    ],
)
def test_last_complete_month(complete: date | None, expected: DateRange | None) -> None:
    """The latest month whose days are all complete."""

    assert sm.last_complete_month(complete) == expected
    assert sm.period_range(Period.LAST_MONTH, date(2031, 6, 18), complete) == expected


# ---------------------------------------------------------------------------
# Definitions and values


def test_defs_without_sources() -> None:
    """Site and per-community shares only."""

    defs = sm.build_summary_defs("home", [BEG, EEG], NO_SOURCES)
    assert [d.key for d in defs] == [
        "feed_in_to_communities",
        "import_from_communities",
        "beg_feed_in_share",
        "beg_import_share",
        "eeg_feed_in_share",
        "eeg_import_share",
    ]
    beg = defs[2]
    assert beg.community == "Example BEG"
    assert beg.translation_key == "community_feed_in_share"
    assert beg.statistic_ids == {
        "eg_austria:home_beg_generation_to_community",
        "eg_austria:home_generation",
    }


def test_defs_with_sources() -> None:
    """PV split with a PV source, deviations with feed-in and grid sources."""

    keys = [d.key for d in sm.build_summary_defs("home", [BEG], ALL_SOURCES)]
    assert keys[4:] == [
        "pv_self_consumed",
        "pv_to_communities",
        "pv_to_surplus",
        "feed_in_deviation",
        "grid_consumption_deviation",
    ]
    only_feed_in = sm.build_summary_defs(
        "home", [], {CONF_SOURCE_FEED_IN: "example:feed_in", CONF_SOURCE_PV: None}
    )
    assert [d.key for d in only_feed_in][-1] == "feed_in_deviation"
    assert len(only_feed_in) == 3


def _defs() -> dict[str, sm.SummaryDef]:
    return {d.key: d for d in sm.build_summary_defs("home", [BEG], ALL_SOURCES)}


def test_compute_value_share_and_unknown() -> None:
    """Share in percent; unknown without numerator or with a zero denominator."""

    d = _defs()["feed_in_to_communities"]
    gen, comm = "eg_austria:home_generation", "eg_austria:home_generation_to_community"
    assert sm.compute_value(d, {gen: 3.0, comm: 1.0}) == SummaryValue(33.3, 1.0, 3.0)
    assert sm.compute_value(d, {gen: 3.0, comm: None}) == SummaryValue(None, None, 3.0)
    assert sm.compute_value(d, {gen: 0.0, comm: 0.0}) == SummaryValue(None, 0.0, 0.0)
    assert sm.compute_value(d, {}) == SummaryValue(None, None, None)


def test_compute_value_self_consumed_clamped() -> None:
    """PV minus metered feed-in, limited to 0..100 %."""

    d = _defs()["pv_self_consumed"]
    gen = "eg_austria:home_generation"
    assert sm.compute_value(d, {"example:pv": 10.0, gen: 4.0}).value == 60.0
    assert sm.compute_value(d, {"example:pv": 10.0, gen: 12.0}).value == 0.0
    assert sm.compute_value(d, {"example:pv": 10.0, gen: -1.0}).value == 100.0
    assert sm.compute_value(d, {"example:pv": 10.0}).value is None


def test_compute_value_deviation() -> None:
    """(source - metered) / metered, signed."""

    d = _defs()["feed_in_deviation"]
    gen = "eg_austria:home_generation"
    assert sm.compute_value(d, {"example:feed_in": 101.0, gen: 100.0}).value == 1.0
    assert sm.compute_value(d, {"example:feed_in": 98.5, gen: 100.0}).value == -1.5


def test_meter_data_until() -> None:
    """A full last hour counts its day; otherwise the day before; earlier wins."""

    day = date(2026, 6, 10)
    last_hour = day_hours(day)[-1]
    afternoon = day_hours(day)[14]
    assert sm.meter_data_until({"home_consumption": last_hour}, "home") == day
    assert sm.meter_data_until({"home_generation": afternoon}, "home") == day - (
        timedelta(days=1)
    )
    assert sm.meter_data_until(
        {"home_consumption": last_hour, "home_generation": afternoon}, "home"
    ) == day - timedelta(days=1)
    assert sm.meter_data_until({"home_consumption": None}, "home") is None
    assert (
        sm.meter_data_until({"home_beg_consumption_from_community": last_hour}, "home")
        is None
    )


# ---------------------------------------------------------------------------
# Recorder


def _meta(statistic_id: str, unit: str = "kWh") -> Any:
    quantity = Quantity.FEED_IN_EUR if unit == "EUR" else Quantity.GENERATION
    meta = dict(StatisticDef("home_generation", quantity, "Synthetic").metadata())
    source = statistic_id.split(":")[0]
    meta |= {
        "statistic_id": statistic_id,
        "source": source,
        "name": statistic_id,
        "unit_of_measurement": unit,
    }
    return meta


async def _write(
    hass: HomeAssistant,
    statistic_id: str,
    per_hour: float,
    days: list[date],
    unit: str = "kWh",
) -> None:
    rows, total = [], 0.0
    for day in days:
        for hour in day_hours(day):
            total += per_hour
            rows.append({"start": hour, "state": per_hour, "sum": total})
    async_add_external_statistics(hass, _meta(statistic_id, unit), rows)


async def test_async_compute_from_recorder(
    recorder_mock: Any, hass: HomeAssistant
) -> None:
    """Totals over the complete hours only, units converted to kWh."""

    days = [date(2026, 5, 31) + timedelta(days=i) for i in range(4)]  # to 3 June
    complete = date(2026, 6, 2)
    await _write(hass, "eg_austria:home_generation", 2.0, days)
    await _write(hass, "eg_austria:home_generation_to_community", 0.5, days)
    await _write(hass, "eg_austria:home_beg_generation_to_community", 0.5, days)
    await _write(hass, "example:pv", 4000.0, days, unit="Wh")
    await _write(hass, "example:feed_in", 2.1, days)
    await async_wait_recording_done(hass)

    defs = sm.build_summary_defs(
        "home",
        [BEG],
        {CONF_SOURCE_PV: "example:pv", CONF_SOURCE_FEED_IN: "example:feed_in"},
    )
    result = await sm.async_compute(hass, defs, date(2026, 6, 8), complete)

    june = result[Period.MONTH_SO_FAR]
    assert june.dates == DateRange(date(2026, 6, 1), complete)
    v = june.values
    assert v["feed_in_to_communities"] == SummaryValue(25.0, 24.0, 96.0)
    assert v["beg_feed_in_share"].value == 25.0
    assert v["pv_self_consumed"] == SummaryValue(50.0, 96.0, 192.0)
    assert v["pv_to_communities"].value == 12.5
    assert v["feed_in_deviation"].value == 5.0
    # No consumption statistics: unknown, not zero.
    assert v["import_from_communities"] == SummaryValue(None, None, None)
    assert v["pv_to_surplus"].value is None

    may = result[Period.LAST_MONTH]
    assert may.dates == DateRange(date(2026, 5, 1), date(2026, 5, 31))
    assert may.values["feed_in_to_communities"] == SummaryValue(25.0, 12.0, 48.0)


async def test_async_compute_without_complete_day(
    recorder_mock: Any, hass: HomeAssistant
) -> None:
    """No complete date: both periods empty, no recorder query."""

    defs = sm.build_summary_defs("home", [BEG], NO_SOURCES)
    with patch.object(sm, "_load_totals") as load:
        result = await sm.async_compute(hass, defs, date(2026, 6, 8), None)
    load.assert_not_called()
    assert result == {
        Period.MONTH_SO_FAR: PeriodSummary(None),
        Period.LAST_MONTH: PeriodSummary(None),
    }


# ---------------------------------------------------------------------------
# Sensors


async def _setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory, **kw: Any) -> Any:
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Vienna")
    return await async_setup_fake_entry(
        hass,
        token_exp=EXP,
        store={"initial_done": True, "last_attempt": NOW.isoformat()},
        **kw,
    )


def _entity_id(hass: HomeAssistant, entry_id: str, key: str) -> str:
    eid = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry_id}_{key}")
    assert eid is not None, key
    return eid


async def test_summary_sensors(
    hass: HomeAssistant, recorder_mock: Any, freezer: FrozenDateTimeFactory
) -> None:
    """Two sensors per value; state and attributes from the coordinator."""

    loaded = await _setup(hass, freezer, options={CONF_SOURCE_PV: "example:pv"})
    entry_id = loaded.entry.entry_id
    entries = er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    summary_ids = {e.unique_id for e in entries if e.entity_category is None}
    # 2 site + 2 x 2 community + 3 PV values, each for two periods, and
    # (issue #75) 2 site + 2 x 2 community € values without prices.
    assert len(summary_ids) == 18 + 12
    assert f"{entry_id}_pv_self_consumed_last_month" in summary_ids
    assert not any("deviation" in uid for uid in summary_ids)

    eid = _entity_id(hass, entry_id, "beg_feed_in_share_month_so_far")
    state = hass.states.get(eid)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["unit_of_measurement"] == "%"
    assert "state_class" not in state.attributes
    assert state.attributes["period_start"] is None
    assert state.name == "EG Austria Home Example BEG feed-in share, month so far"

    coordinator = loaded.coordinator
    coordinator.summary = {
        Period.MONTH_SO_FAR: PeriodSummary(
            DateRange(date(2026, 6, 1), date(2026, 6, 2)),
            {"beg_feed_in_share": SummaryValue(25.0, 24.0, 96.0)},
        )
    }
    async_dispatcher_send(hass, coordinator.summary_signal)
    await hass.async_block_till_done()
    state = hass.states.get(eid)
    assert state.state == "25.0"
    assert state.attributes["period_start"] == "2026-06-01"
    assert state.attributes["period_end"] == "2026-06-02"
    assert state.attributes["numerator_kwh"] == 24.0
    assert state.attributes["denominator_kwh"] == 96.0
    # A value missing from its period stays unknown.
    other = hass.states.get(
        _entity_id(hass, entry_id, "feed_in_to_communities_month_so_far")
    )
    assert other.state == STATE_UNKNOWN
    assert other.attributes["period_end"] == "2026-06-02"


async def test_meter_data_until_sensor(
    hass: HomeAssistant, recorder_mock: Any, freezer: FrozenDateTimeFactory
) -> None:
    """The stored meter date is shown after a restart."""

    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Vienna")
    loaded = await async_setup_fake_entry(
        hass,
        token_exp=EXP,
        store={
            "initial_done": True,
            "last_attempt": NOW.isoformat(),
            "meter_until": "2026-06-06",
        },
    )
    eid = _entity_id(hass, loaded.entry.entry_id, "meter_data_until")
    assert hass.states.get(eid).state == "2026-06-06"


async def test_refresh_after_start_and_failure(
    hass: HomeAssistant,
    recorder_mock: Any,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Computed after startup; a failure logs a warning and keeps the values."""

    loaded = await _setup(hass, freezer)
    coordinator = loaded.coordinator
    # Startup (started HA): computed once, no complete date yet.
    assert set(coordinator.summary) == set(Period)

    kept = {Period.MONTH_SO_FAR: PeriodSummary(None)}
    coordinator.summary = kept
    caplog.set_level(logging.WARNING)
    with patch(
        "custom_components.eg_austria.coordinator.async_compute",
        AsyncMock(side_effect=RuntimeError("synthetic")),
    ):
        await coordinator.async_refresh_summary(wait_for_recorder=True)
    assert coordinator.summary is kept
    assert "Summary values could not be computed" in caplog.text


# ---------------------------------------------------------------------------
# € values (issue #75)

GEN = Direction.GENERATION
CON = Direction.CONSUMPTION


def test_money_defs_without_and_with_prices() -> None:
    """Community income and cost always; surplus and savings with a price."""

    plain = sm.build_money_defs("home", [BEG, EEG])
    assert [d.key for d in plain] == [
        "feed_in_income",
        "consumption_cost",
        "beg_feed_in_income",
        "beg_consumption_cost",
        "eeg_feed_in_income",
        "eeg_consumption_cost",
    ]
    assert plain[0].statistic_id == "eg_austria:home_feed_in_eur"
    assert plain[0].settles == (("beg", GEN), ("eeg", GEN))
    assert plain[3].statistic_id == "eg_austria:home_beg_consumption_eur"
    assert plain[3].settles == (("beg", CON),)
    assert plain[3].community == "Example BEG"

    full = {
        d.key: d
        for d in sm.build_money_defs("home", [BEG], surplus=True, supplier=True)
    }
    assert list(full) == [
        "feed_in_income",
        "consumption_cost",
        "surplus_income",
        "feed_in_compensation",
        "import_savings",
        "beg_feed_in_income",
        "beg_consumption_cost",
        "beg_import_savings",
    ]
    assert full["surplus_income"].settles == ()
    assert (
        full["feed_in_compensation"].statistic_id == "eg_austria:home_feed_in_total_eur"
    )
    assert full["feed_in_compensation"].settles == (("beg", GEN),)
    assert full["beg_import_savings"].statistic_id == (
        "eg_austria:home_beg_import_savings_eur"
    )
    assert full["beg_import_savings"].translation_key == "community_import_savings"


def test_settlement_state() -> None:
    """All settled, some settled, none; no marker without settlements."""

    settled = {"beg/Generation": ["2026-05"], "eeg/Generation": ["2026-04", "2026-05"]}
    both = (("beg", GEN), ("eeg", GEN))
    assert sm.settlement_state(both, "2026-05", settled) is sm.Settlement.SETTLED
    assert sm.settlement_state(both, "2026-04", settled) is sm.Settlement.PARTLY_SETTLED
    assert sm.settlement_state(both, "2026-06", settled) is sm.Settlement.PROVISIONAL
    assert sm.settlement_state((("beg", CON),), "2026-05", settled) is (
        sm.Settlement.PROVISIONAL
    )
    assert sm.settlement_state((), "2026-05", settled) is None


def test_compute_money() -> None:
    """Rounded to cents; unknown without rows, then also no marker."""

    d = sm.build_money_defs("home", [BEG])[0]
    settled = {"beg/Generation": ["2026-05"]}
    assert sm.compute_money(d, {d.statistic_id: 1.23456}, "2026-05", settled) == (
        sm.MoneyValue(1.23, sm.Settlement.SETTLED)
    )
    assert sm.compute_money(d, {d.statistic_id: 0.0}, "2026-06", settled) == (
        sm.MoneyValue(0.0, sm.Settlement.PROVISIONAL)
    )
    assert sm.compute_money(d, {}, "2026-05", settled) == sm.MoneyValue(None, None)


async def test_async_compute_money_from_recorder(
    recorder_mock: Any, hass: HomeAssistant
) -> None:
    """€ sums over the same complete hours, marker per period month."""

    days = [date(2026, 5, 31) + timedelta(days=i) for i in range(4)]  # to 3 June
    complete = date(2026, 6, 2)
    await _write(hass, "eg_austria:home_feed_in_eur", 0.01, days, unit="EUR")
    await _write(hass, "eg_austria:home_beg_feed_in_eur", 0.01, days, unit="EUR")
    await async_wait_recording_done(hass)

    money = sm.build_money_defs("home", [BEG])
    result = await sm.async_compute(
        hass,
        [],
        date(2026, 6, 8),
        complete,
        money,
        {"beg/Generation": ["2026-05"]},
    )
    june = result[Period.MONTH_SO_FAR].money
    assert june["feed_in_income"] == sm.MoneyValue(0.48, sm.Settlement.PROVISIONAL)
    assert june["beg_feed_in_income"] == sm.MoneyValue(0.48, sm.Settlement.PROVISIONAL)
    assert june["consumption_cost"] == sm.MoneyValue(None, None)
    may = result[Period.LAST_MONTH].money
    assert may["feed_in_income"] == sm.MoneyValue(0.24, sm.Settlement.SETTLED)


async def test_money_sensors(
    hass: HomeAssistant, recorder_mock: Any, freezer: FrozenDateTimeFactory
) -> None:
    """€ sensors: unit, no state_class, settlement attribute; price-gated ones."""

    price = {"source": "number", "periods": [["2020-01-01", 10.0]]}
    loaded = await _setup(
        hass,
        freezer,
        options={CONF_SURPLUS_PRICE: price, CONF_SUPPLIER_PRICE: price},
    )
    entry_id = loaded.entry.entry_id
    eid = _entity_id(hass, entry_id, "feed_in_compensation_last_month")
    for key in ("surplus_income", "import_savings", "beg_import_savings"):
        _entity_id(hass, entry_id, f"{key}_month_so_far")
    state = hass.states.get(eid)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["unit_of_measurement"] == "€"
    assert state.attributes["device_class"] == "monetary"
    assert "state_class" not in state.attributes
    assert state.attributes["settlement"] is None
    assert state.name == "EG Austria Home Feed-in compensation, last month"

    coordinator = loaded.coordinator
    coordinator.summary = {
        Period.LAST_MONTH: PeriodSummary(
            DateRange(date(2026, 5, 1), date(2026, 5, 31)),
            money={"feed_in_compensation": sm.MoneyValue(12.34, sm.Settlement.SETTLED)},
        )
    }
    async_dispatcher_send(hass, coordinator.summary_signal)
    await hass.async_block_till_done()
    state = hass.states.get(eid)
    assert state.state == "12.34"
    assert state.attributes["settlement"] == "settled"
    assert state.attributes["period_start"] == "2026-05-01"
    beg = hass.states.get(_entity_id(hass, entry_id, "beg_consumption_cost_last_month"))
    assert beg.state == STATE_UNKNOWN
    assert beg.name == "EG Austria Home Example BEG cost of consumption, last month"


async def test_no_price_sensors_without_prices(
    hass: HomeAssistant, recorder_mock: Any, freezer: FrozenDateTimeFactory
) -> None:
    """Without prices: no surplus, compensation or savings sensors."""

    loaded = await _setup(hass, freezer)
    unique_ids = {
        e.unique_id
        for e in er.async_entries_for_config_entry(
            er.async_get(hass), loaded.entry.entry_id
        )
    }
    assert f"{loaded.entry.entry_id}_feed_in_income_month_so_far" in unique_ids
    assert not any(
        k in uid
        for uid in unique_ids
        for k in ("surplus_income", "feed_in_compensation", "import_savings")
    )
