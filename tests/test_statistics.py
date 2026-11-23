"""Tests for statistics.py (test plan cases 16-21, spec 04)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from functools import partial
import itertools
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.eg_austria import statistics as stats
from custom_components.eg_austria.models import (
    AnalysisData,
    AnalysisFlags,
    AnalysisTotals,
    DayCompleteness,
    Direction,
    EnergyPoint,
    parse_analysis,
    parse_day_completeness,
)
from custom_components.eg_austria.statistics import (
    CommunityRef,
    Quantity,
    StatisticDef,
    StoredRows,
    async_import_window,
    build_series,
    build_statistic_defs,
    compute_rows,
    day_hours,
    dst_gap_hour,
    local_day_start,
)

from .conftest import load_fixture


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before ``hass`` (needed by ``recorder_mock``).

    The shared ``auto_enable_custom_integrations`` fixture requests ``hass``
    for every test, so the recorder must be prepared first.
    """


BEG = CommunityRef("beg", "Example BEG")
EEG = CommunityRef("eeg", "Example EEG")
BOTH = (Direction.CONSUMPTION, Direction.GENERATION)


# ---------------------------------------------------------------------------
# Synthetic data helpers


def _point(
    direction: Direction,
    start: datetime,
    value: float | None,
    community: float | None = None,
    excess: float | None = None,
    *,
    faulty: bool = False,
) -> EnergyPoint:
    return EnergyPoint(
        direction=direction,
        start=start,
        value=value,
        community=value * 0.2 if community is None and value is not None else community,
        excess=value * 0.7 if excess is None and value is not None else excess,
        faulty=faulty,
        community_faulty=False,
    )


def _response(
    consumption: Mapping[datetime, float | None] | None = None,
    generation: Mapping[datetime, float | None] | None = None,
    *,
    faulty: set[datetime] | None = None,
) -> AnalysisData:
    faulty = faulty or set()

    def points(direction: Direction, values: Mapping[datetime, float | None]):
        return tuple(
            _point(direction, h, v, faulty=h in faulty)
            for h, v in sorted(values.items())
        )

    return AnalysisData(
        consumption=points(Direction.CONSUMPTION, consumption or {}),
        generation=points(Direction.GENERATION, generation or {}),
        totals=AnalysisTotals(None, None, None, None, None, None),
        flags=AnalysisFlags(False, False, False, False, False, False),
    )


def _complete(*days: date, consumption: bool = True, generation: bool = True):
    return [DayCompleteness(d, consumption, generation) for d in days]


def _day_range(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _hourly(first: date, last: date, value: float) -> dict[datetime, float | None]:
    return {h: value for d in _day_range(first, last) for h in day_hours(d)}


def _consumption_def(site: str = "home") -> StatisticDef:
    return StatisticDef(f"{site}_consumption", Quantity.CONSUMPTION, "Home Consumption")


def _defs_by_key(defs: list[StatisticDef]) -> dict[str, StatisticDef]:
    return {d.key: d for d in defs}


async def _rows(hass: HomeAssistant, statistic_id: str) -> list[dict]:
    await async_wait_recording_done(hass)
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2000, 1, 1, tzinfo=UTC),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return [
        {
            "start": datetime.fromtimestamp(r["start"], UTC),
            "state": r["state"],
            "sum": r["sum"],
        }
        for r in result.get(statistic_id, [])
    ]


async def _metadata(hass: HomeAssistant, ids: set[str]) -> dict:
    return await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_ids=ids)
    )


def _assert_consistent(rows: list[dict]) -> None:
    """Hourly differences of ``sum`` equal ``state`` (no stale sums)."""

    for prev, cur in itertools.pairwise(rows):
        assert cur["sum"] - prev["sum"] == pytest.approx(cur["state"])


# ---------------------------------------------------------------------------
# Case 16: ids and metadata


def test_defs_two_communities() -> None:
    """Ids, names and metadata as spec 04 for a two-community entry."""

    defs = build_statistic_defs("home", [BEG, EEG], BOTH)
    assert [d.statistic_id for d in defs] == [
        "eg_austria:home_consumption",
        "eg_austria:home_generation",
        "eg_austria:home_consumption_from_community",
        "eg_austria:home_generation_to_community",
        "eg_austria:home_consumption_from_supplier",
        "eg_austria:home_generation_to_surplus",
        "eg_austria:home_feed_in_eur",
        "eg_austria:home_consumption_eur",
        "eg_austria:home_beg_consumption_from_community",
        "eg_austria:home_beg_generation_to_community",
        "eg_austria:home_beg_feed_in_eur",
        "eg_austria:home_beg_consumption_eur",
        "eg_austria:home_eeg_consumption_from_community",
        "eg_austria:home_eeg_generation_to_community",
        "eg_austria:home_eeg_feed_in_eur",
        "eg_austria:home_eeg_consumption_eur",
    ]
    by_key = _defs_by_key(defs)
    assert by_key["home_generation_to_community"].name == (
        "Home Generation to communities"
    )
    assert by_key["home_generation_to_surplus"].name == (
        "Home Generation to surplus buyer"
    )
    assert by_key["home_beg_consumption_from_community"].name == (
        "Example BEG Consumption from community"
    )
    assert by_key["home_eeg_generation_to_community"].community == "eeg"
    assert by_key["home_beg_consumption_from_community"].metadata() == {
        "source": "eg_austria",
        "statistic_id": "eg_austria:home_beg_consumption_from_community",
        "name": "Example BEG Consumption from community",
        "unit_of_measurement": "kWh",
        "unit_class": "energy",
        "has_sum": True,
        "mean_type": StatisticMeanType.NONE,
    }


def test_defs_missing_direction_and_labels() -> None:
    """No statistics for a direction without a metering point; labels override."""

    defs = build_statistic_defs(
        "flat",
        [BEG],
        [Direction.CONSUMPTION],
        site_label="Zuhause",
        labels={"site.consumption": "Verbrauch"},
    )
    assert [d.key for d in defs] == [
        "flat_consumption",
        "flat_consumption_from_community",
        "flat_consumption_from_supplier",
        "flat_consumption_eur",
        "flat_beg_consumption_from_community",
        "flat_beg_consumption_eur",
    ]
    assert defs[0].name == "Zuhause Verbrauch"
    assert defs[1].name == "Zuhause Consumption from communities"


# ---------------------------------------------------------------------------
# Series building (pure)


def test_series_from_two_communities() -> None:
    """Site community values are summed, site meter values come from the first."""

    defs = build_statistic_defs("home", [BEG, EEG], BOTH)
    day = date(2026, 6, 1)
    hourly = {
        "beg": [parse_analysis(load_fixture("analysis_hour_normal_beg.json"))],
        "eeg": [parse_analysis(load_fixture("analysis_hour_normal_eeg.json"))],
    }
    completeness = parse_day_completeness(load_fixture("day_completeness_normal.json"))
    series = build_series(defs, hourly, completeness, day, date(2026, 6, 7))
    beg = {p.start: p for p in hourly["beg"][0].consumption}
    eeg = {p.start: p for p in hourly["eeg"][0].consumption}
    h = local_day_start(day) + timedelta(hours=19)
    assert len(series["home_consumption"].values) == 7 * 24
    assert series["home_consumption"].values[h] == beg[h].value
    assert series["home_consumption_from_supplier"].values[h] == beg[h].excess
    assert series["home_consumption_from_community"].values[h] == pytest.approx(
        beg[h].community + eeg[h].community
    )
    assert series["home_beg_consumption_from_community"].values[h] == (beg[h].community)
    assert series["home_eeg_consumption_from_community"].values[h] == (eeg[h].community)


def test_series_null_hours_unknown() -> None:
    """Null meter values are unknown, never zero (case 17)."""

    defs = [_consumption_def()]
    data = parse_analysis(load_fixture("analysis_hour_null_hours.json"))
    day = date(2026, 6, 3)
    nulls = {p.start for p in data.consumption if p.value is None}
    assert len(nulls) == 2
    series = build_series(defs, {"beg": [data]}, _complete(day), day, day)
    values = series["home_consumption"].values
    assert len(values) == 22
    assert not nulls & values.keys()


def test_series_tail_incomplete() -> None:
    """Community quantities on incomplete days: no value; meter ones: value (18)."""

    defs = build_statistic_defs("home", [BEG], BOTH)
    data = parse_analysis(load_fixture("analysis_hour_tail_incomplete.json"))
    completeness = parse_day_completeness(
        load_fixture("day_completeness_tail_incomplete.json")
    )
    series = build_series(
        defs, {"beg": [data]}, completeness, date(2026, 5, 1), date(2026, 5, 31)
    )
    incomplete = {c.day for c in completeness if not c.consumption_complete}
    assert len(incomplete) == 14
    for key in (
        "home_consumption_from_community",
        "home_consumption_from_supplier",
        "home_beg_consumption_from_community",
    ):
        days = {stats.local_date(h) for h in series[key].values}
        assert days == set(_day_range(date(2026, 5, 1), date(2026, 5, 17)))
    meter_days = {stats.local_date(h) for h in series["home_consumption"].values}
    # Meter values are written on community-incomplete days, except the
    # meter-missing last day (0 with the meter flag set).
    assert meter_days == set(_day_range(date(2026, 5, 1), date(2026, 5, 30)))


def test_series_meter_flag_rules() -> None:
    """Meter flag: unknown on incomplete days, ignored on complete days (rule 4)."""

    defs = [_consumption_def()]
    day = date(2026, 6, 1)
    hours = day_hours(day)
    data = _response(dict.fromkeys(hours, 0.0), faulty={hours[0]})
    incomplete = build_series(
        defs, {"beg": [data]}, _complete(day, consumption=False), day, day
    )
    assert hours[0] not in incomplete["home_consumption"].values
    assert incomplete["home_consumption"].values[hours[1]] == 0.0
    complete = build_series(defs, {"beg": [data]}, _complete(day), day, day)
    assert complete["home_consumption"].values[hours[0]] == 0.0
    missing = build_series(defs, {"beg": [data]}, [], day, day)
    assert hours[0] not in missing["home_consumption"].values
    assert len(missing["home_consumption"].values) == 23


def test_series_missing_completeness_and_held_days() -> None:
    """A missing completeness entry or a held day makes the hour unknown."""

    defs = build_statistic_defs("home", [BEG], [Direction.CONSUMPTION])
    first, last = date(2026, 6, 1), date(2026, 6, 3)
    data = _response(_hourly(first, last, 1.0))
    series = build_series(
        defs,
        {"beg": [data]},
        _complete(first, last),  # 2026-06-02 missing
        first,
        last,
        held_days={"home_consumption": [first]},
    )
    meter_days = {stats.local_date(h) for h in series["home_consumption"].values}
    assert meter_days == {date(2026, 6, 2), last}
    community_days = {
        stats.local_date(h)
        for h in series["home_beg_consumption_from_community"].values
    }
    assert community_days == {first, last}


def test_series_missing_community_data() -> None:
    """A community without a response or with a missing hour gives no value."""

    defs = build_statistic_defs("home", [BEG, EEG], [Direction.CONSUMPTION])
    day = date(2026, 6, 1)
    hours = day_hours(day)
    beg = _response(dict.fromkeys(hours, 1.0))
    eeg = _response(dict.fromkeys(hours[1:], 1.0))
    series = build_series(defs, {"beg": [beg], "eeg": [eeg]}, _complete(day), day, day)
    assert hours[0] not in series["home_consumption_from_community"].values
    assert hours[0] in series["home_consumption"].values
    only_beg = build_series(defs, {"beg": [beg]}, _complete(day), day, day)
    assert only_beg["home_eeg_consumption_from_community"].values == {}
    assert only_beg["home_consumption_from_community"].values == {}


# ---------------------------------------------------------------------------
# DST


def test_dst_helpers() -> None:
    """Day lengths and the autumn gap hour in Austrian local time."""

    assert len(day_hours(date(2026, 3, 29))) == 23
    assert len(day_hours(date(2026, 10, 25))) == 25
    assert len(day_hours(date(2026, 6, 1))) == 24
    assert dst_gap_hour(date(2026, 10, 25)) == datetime(2026, 10, 25, 0, tzinfo=UTC)
    assert dst_gap_hour(date(2026, 3, 29)) is None
    assert dst_gap_hour(date(2026, 6, 1)) is None


def _autumn() -> tuple[list[StatisticDef], dict, dict]:
    defs = build_statistic_defs("home", [BEG], BOTH)
    hourly = {"beg": [parse_analysis(load_fixture("analysis_hour_dst_autumn.json"))]}
    day = {"beg": [parse_analysis(load_fixture("analysis_day_dst_autumn.json"))]}
    return defs, hourly, day


AUTUMN = date(2026, 10, 25)
GAP = datetime(2026, 10, 25, 0, tzinfo=UTC)


def test_dst_autumn_gap_filled_from_day_residual() -> None:
    """Complete autumn day: gap hour = Day - sum of the 24 hours (case 14)."""

    defs, hourly, day = _autumn()
    totals = day["beg"][0].totals
    series = build_series(
        defs, hourly, _complete(AUTUMN), AUTUMN, AUTUMN, day_values=day
    )
    expected = {
        "home_consumption": totals.consumption,
        "home_generation": totals.generation,
        "home_consumption_from_community": totals.consumption_from_community,
        "home_generation_to_community": totals.generation_to_community,
        "home_consumption_from_supplier": totals.consumption_in_excess,
        "home_generation_to_surplus": totals.generation_in_excess,
        "home_beg_consumption_from_community": totals.consumption_from_community,
        "home_beg_generation_to_community": totals.generation_to_community,
    }
    for key, total in expected.items():
        values = series[key].values
        assert len(values) == 25, key
        assert GAP in values, key
        assert series[key].dst_filled == [GAP]
        assert values[GAP] >= 0
        assert sum(values.values()) == pytest.approx(total), key
    # The flagged local 01:00 hour keeps its value on a complete day.
    flagged = datetime(2026, 10, 24, 23, tzinfo=UTC)
    assert series["home_consumption"].values[flagged] == 0.5


def test_dst_autumn_not_filled_before_complete() -> None:
    """Gap stays unknown before the day is complete or without a Day value."""

    defs, hourly, day = _autumn()
    incomplete = build_series(
        defs,
        hourly,
        _complete(AUTUMN, consumption=False),
        AUTUMN,
        AUTUMN,
        day_values=day,
    )
    assert GAP not in incomplete["home_consumption"].values
    assert GAP in incomplete["home_generation"].values
    no_day = build_series(defs, hourly, _complete(AUTUMN), AUTUMN, AUTUMN)
    assert GAP not in no_day["home_consumption"].values
    assert no_day["home_consumption"].dst_filled == []


def test_dst_autumn_negative_residual(caplog: pytest.LogCaptureFixture) -> None:
    """A Day value below the hourly sum leaves the gap unknown, logged once."""

    defs = [_consumption_def()]
    hours = [h for h in day_hours(AUTUMN) if h != GAP]
    hourly = {"beg": [_response(dict.fromkeys(hours, 1.0))]}
    day = {"beg": [_response({local_day_start(AUTUMN): 23.0})]}
    caplog.set_level(logging.WARNING)
    for _ in range(2):
        series = build_series(
            defs, hourly, _complete(AUTUMN), AUTUMN, AUTUMN, day_values=day
        )
        assert GAP not in series["home_consumption"].values
        assert series["home_consumption"].dst_negative == [AUTUMN]
    assert len([r for r in caplog.records if "DST" in r.getMessage()]) == 1
    # A residual of exactly 0 is a value.
    day = {"beg": [_response({local_day_start(AUTUMN): 24.0})]}
    series = build_series(
        defs, hourly, _complete(AUTUMN), AUTUMN, AUTUMN, day_values=day
    )
    assert series["home_consumption"].values[GAP] == 0.0
    # A null hour elsewhere in the day: no residual.
    hourly = {"beg": [_response({**dict.fromkeys(hours, 1.0), hours[3]: None})]}
    series = build_series(
        defs, hourly, _complete(AUTUMN), AUTUMN, AUTUMN, day_values=day
    )
    assert GAP not in series["home_consumption"].values


def test_dst_spring() -> None:
    """Spring day: 23 hours, sums over the day equal the totals."""

    defs = build_statistic_defs("home", [BEG], BOTH)
    data = parse_analysis(load_fixture("analysis_hour_dst_spring.json"))
    day = date(2026, 3, 29)
    series = build_series(defs, {"beg": [data]}, _complete(day), day, day)
    assert len(series["home_consumption"].values) == 23
    assert sum(series["home_consumption"].values.values()) == pytest.approx(
        data.totals.consumption
    )
    assert sum(series["home_generation_to_community"].values.values()) == (
        pytest.approx(data.totals.generation_to_community)
    )


# ---------------------------------------------------------------------------
# Sum computation (pure)


def test_compute_rows_stored_wins_and_tail() -> None:
    """Stored rows fill unknown hours, also after the newest known hour (21)."""

    start = datetime(2026, 6, 1, tzinfo=UTC)
    h = [start + timedelta(hours=i) for i in range(5)]
    stored = StoredRows(base=10.0, states={h[1]: 2.0, h[4]: 4.0})
    rows, result = compute_rows({h[0]: 1.0, h[2]: 3.0}, stored, start)
    assert [(r["start"], r["state"], r["sum"]) for r in rows] == [
        (h[0], 1.0, 11.0),
        (h[1], 2.0, 13.0),
        (h[2], 3.0, 16.0),
        (h[4], 4.0, 20.0),
    ]
    assert result.newest_hour == h[4]
    assert result.kept_stored_hours == 2
    assert result.unknown_hours == 3
    assert (result.first_unknown, result.last_unknown) == (h[1], h[4])
    rows, result = compute_rows({}, StoredRows(0.0, {}), start)
    assert rows == []
    assert result.newest_hour is None


# ---------------------------------------------------------------------------
# Recorder: cases 17, 19, 20, 21 and metadata


async def _import(
    hass: HomeAssistant,
    defs: list[StatisticDef],
    data: AnalysisData,
    first: date,
    last: date,
    completeness: list[DayCompleteness] | None = None,
    **kwargs,
):
    if completeness is None:
        completeness = _complete(*_day_range(first, last))
    result = await async_import_window(
        hass, defs, {"beg": [data]}, completeness, first, last, **kwargs
    )
    await async_wait_recording_done(hass)
    return result


async def test_first_import(recorder_mock, hass: HomeAssistant) -> None:
    """Sums start at 0 and are cumulative; null hours produce no row (17)."""

    defs = [_consumption_def()]
    day = date(2026, 6, 3)
    data = parse_analysis(load_fixture("analysis_hour_null_hours.json"))
    result = await _import(hass, defs, data, day, day)
    rows = await _rows(hass, "eg_austria:home_consumption")
    assert len(rows) == 22
    assert rows[0]["sum"] == rows[0]["state"]
    assert rows[-1]["sum"] == pytest.approx(
        sum(p.value for p in data.consumption if p.value is not None)
    )
    _assert_consistent(rows)
    res = result["home_consumption"]
    assert (res.rows, res.unknown_hours, res.kept_stored_hours) == (22, 2, 0)
    meta = await _metadata(hass, {"eg_austria:home_consumption"})
    _, info = meta["eg_austria:home_consumption"]
    assert info["source"] == "eg_austria"
    assert info["unit_of_measurement"] == "kWh"
    assert info["has_sum"] is True
    assert info["name"] == "Home Consumption"


async def test_refetch_overwrites_window(recorder_mock, hass: HomeAssistant) -> None:
    """Changed values rewrite the day and later sums; earlier rows stay (19)."""

    defs = [_consumption_def()]
    first, last = date(2026, 6, 1), date(2026, 6, 4)
    await _import(hass, defs, _response(_hourly(first, last, 1.0)), first, last)
    before = await _rows(hass, "eg_austria:home_consumption")
    assert len(before) == 96
    assert before[-1]["sum"] == pytest.approx(96.0)

    # Re-fetch from 06-03: that day corrected to 2.0, 06-04 unchanged, 06-05 new.
    window = date(2026, 6, 3)
    corrected = {
        **_hourly(window, window, 2.0),
        **_hourly(date(2026, 6, 4), date(2026, 6, 5), 1.0),
    }
    await _import(hass, defs, _response(corrected), window, date(2026, 6, 5))
    after = await _rows(hass, "eg_austria:home_consumption")
    assert len(after) == 120
    assert after[:48] == before[:48]  # rows before the window untouched
    assert after[48]["sum"] == pytest.approx(48.0 + 2.0)
    assert after[-1]["sum"] == pytest.approx(48.0 + 48.0 + 48.0)
    _assert_consistent(after)


async def test_known_to_unknown_keeps_stored(
    recorder_mock, hass: HomeAssistant
) -> None:
    """Hours that go back to unknown keep their stored rows; sums stay right (20)."""

    defs = build_statistic_defs("home", [BEG], [Direction.CONSUMPTION])
    first, last = date(2026, 6, 1), date(2026, 6, 3)
    await _import(hass, defs, _response(_hourly(first, last, 1.0)), first, last)

    # Next run: 06-02 flips back to incomplete, the last 3 hours of 06-03 are
    # null, and 06-01 is corrected to 2.0.
    values = {**_hourly(first, last, 1.0), **_hourly(first, first, 2.0)}
    for h in day_hours(last)[-3:]:
        values[h] = None
    completeness = [
        *_complete(first),
        *_complete(date(2026, 6, 2), consumption=False),
        *_complete(last),
    ]
    result = await _import(hass, defs, _response(values), first, last, completeness)

    meter = await _rows(hass, "eg_austria:home_consumption")
    assert len(meter) == 72
    assert meter[-1]["sum"] == pytest.approx(48.0 + 24.0 + 24.0)
    _assert_consistent(meter)
    assert result["home_consumption"].kept_stored_hours == 3

    community = await _rows(hass, "eg_austria:home_beg_consumption_from_community")
    assert len(community) == 72
    assert [r["state"] for r in community[24:48]] == pytest.approx([0.2] * 24)
    _assert_consistent(community)
    res = result["home_beg_consumption_from_community"]
    assert res.kept_stored_hours == 27
    assert res.newest_hour == day_hours(last)[-1]


async def test_sum_continues_before_window(recorder_mock, hass: HomeAssistant) -> None:
    """The base comes from the last row before the window, also after long gaps."""

    sdef = _consumption_def()
    old = datetime(2024, 1, 1, tzinfo=UTC)
    async_add_external_statistics(
        hass, sdef.metadata(), [{"start": old, "state": 5.0, "sum": 500.0}]
    )
    await async_wait_recording_done(hass)
    day = date(2026, 6, 1)  # more than 400 days after the stored row
    await _import(hass, [sdef], _response(_hourly(day, day, 1.0)), day, day)
    rows = await _rows(hass, "eg_austria:home_consumption")
    assert rows[0] == {"start": old, "state": 5.0, "sum": 500.0}
    assert rows[1]["sum"] == pytest.approx(501.0)
    assert rows[-1]["sum"] == pytest.approx(524.0)


async def test_rename_updates_metadata(recorder_mock, hass: HomeAssistant) -> None:
    """A portal rename changes the display name; the id and history stay."""

    day = date(2026, 6, 1)
    data = _response(_hourly(day, day, 1.0))
    statistic_id = "eg_austria:home_beg_consumption_from_community"
    for name in ("Example BEG", "Example BEG renamed"):
        defs = build_statistic_defs(
            "home", [CommunityRef("beg", name)], [Direction.CONSUMPTION]
        )
        await _import(hass, defs, data, day, day)
    meta = await _metadata(hass, {statistic_id})
    assert (
        meta[statistic_id][1]["name"]
        == "Example BEG renamed Consumption from community"
    )
    assert len(await _rows(hass, statistic_id)) == 24


async def test_nothing_known_no_import(recorder_mock, hass: HomeAssistant) -> None:
    """No known and no stored hour: no import call, no empty statistic."""

    day = date(2026, 6, 1)
    result = await _import(hass, [_consumption_def()], _response(), day, day, [])
    assert result["home_consumption"].rows == 0
    meta = await _metadata(hass, {"eg_austria:home_consumption"})
    assert meta == {}


async def test_dst_autumn_import(recorder_mock, hass: HomeAssistant) -> None:
    """The filled DST hour is written and the day sums to the Day value."""

    defs, hourly, day = _autumn()
    await async_import_window(
        hass, defs, hourly, _complete(AUTUMN), AUTUMN, AUTUMN, day_values=day
    )
    rows = await _rows(hass, "eg_austria:home_generation_to_community")
    assert len(rows) == 25
    assert GAP in {r["start"] for r in rows}
    assert rows[-1]["sum"] == pytest.approx(
        day["beg"][0].totals.generation_to_community
    )
    _assert_consistent(rows)


# ---------------------------------------------------------------------------
# Money (phase 2, ADR 0008, 0015)


def test_money_defs_metadata_and_gates() -> None:
    """Money statistics are EUR without unit class and follow their kWh keys."""

    defs = _defs_by_key(build_statistic_defs("home", [BEG], BOTH, surplus=True))
    feed = defs["home_beg_feed_in_eur"]
    assert feed.metadata()["unit_of_measurement"] == "€"
    assert feed.metadata()["unit_class"] is None
    assert feed.gate_keys == ("home_beg_generation_to_community",)
    assert defs["home_consumption_eur"].gate_keys == (
        "home_consumption_from_community",
    )
    assert defs["home_feed_in_total_eur"].gate_keys == (
        "home_generation_to_community",
        "home_generation_to_surplus",
    )
    assert defs["home_generation"].gate_keys == ("home_generation",)
    assert "home_beg_surplus_eur" not in defs
    assert defs["home_surplus_eur"].name == "Home Surplus income"
    plain = _defs_by_key(build_statistic_defs("home", [BEG], BOTH))
    assert "home_surplus_eur" not in plain
    assert "home_feed_in_total_eur" not in plain


def test_money_series_from_portal_amounts() -> None:
    """€ series are the portal amounts per community and summed for the site."""

    defs = build_statistic_defs("home", [BEG, EEG], BOTH)
    day = date(2026, 6, 1)
    hourly = {
        "beg": [parse_analysis(load_fixture("analysis_hour_normal_beg.json"))],
        "eeg": [parse_analysis(load_fixture("analysis_hour_normal_eeg.json"))],
    }
    completeness = parse_day_completeness(load_fixture("day_completeness_normal.json"))
    series = build_series(defs, hourly, completeness, day, date(2026, 6, 7))
    beg = {p.start: p for p in hourly["beg"][0].generation}
    eeg = {p.start: p for p in hourly["eeg"][0].generation}
    h = next(
        p.start
        for p in hourly["beg"][0].generation
        if p.money and local_day_start(day) <= p.start
    )
    assert series["home_beg_feed_in_eur"].values[h] == beg[h].money
    assert series["home_feed_in_eur"].values[h] == pytest.approx(
        beg[h].money + eeg[h].money
    )
    cons = {p.start: p for p in hourly["beg"][0].consumption}
    hc = next(p.start for p in hourly["beg"][0].consumption if p.money)
    if hc in series["home_beg_consumption_eur"].values:
        assert series["home_beg_consumption_eur"].values[hc] == cons[hc].money


def test_money_gated_by_completeness() -> None:
    """Portal amounts of an incomplete day are unknown, like community kWh."""

    day = date(2026, 6, 1)
    h = local_day_start(day) + timedelta(hours=12)
    point = EnergyPoint(
        Direction.GENERATION, h, 2.0, 1.0, 1.0, False, False, money=0.0775
    )
    data = AnalysisData(
        consumption=(),
        generation=(point,),
        totals=AnalysisTotals(None, None, None, None, None, None),
        flags=AnalysisFlags(False, False, False, False, False, False),
    )
    defs = [d for d in build_statistic_defs("home", [BEG], BOTH) if "eur" in d.key]
    complete = build_series(defs, {"beg": [data]}, _complete(day), day, day)
    assert complete["home_beg_feed_in_eur"].values == {h: 0.0775}
    incomplete = build_series(
        defs, {"beg": [data]}, _complete(day, generation=False), day, day
    )
    assert incomplete["home_beg_feed_in_eur"].values == {}


def test_derive_surplus_and_total() -> None:
    """Surplus € = surplus kWh * price; total = community € + surplus €."""

    defs = build_statistic_defs("home", [BEG], BOTH, surplus=True)
    h1 = datetime(2026, 6, 1, 10, tzinfo=UTC)
    h2 = h1 + timedelta(hours=1)
    h3 = h2 + timedelta(hours=1)
    series = {d.key: stats.Series() for d in defs}
    series["home_generation_to_surplus"] = stats.Series({h1: 2.0, h2: 1.0, h3: 3.0})
    series["home_feed_in_eur"] = stats.Series({h1: 0.5, h3: 0.25})
    stats.derive_series(defs, series, {h1: 0.1, h2: 0.08})
    assert series["home_surplus_eur"].values == {h1: 0.2, h2: 0.08}
    assert series["home_surplus_eur"].unpriced == [h3]
    assert series["home_feed_in_total_eur"].values == {h1: pytest.approx(0.7)}


def test_derive_import_savings_site_and_community() -> None:
    """Issue #73: community kWh x supplier price - community cost, per hour."""

    defs = build_statistic_defs("home", [BEG], BOTH, supplier=True)
    keys = {d.key for d in defs}
    assert {"home_import_savings_eur", "home_beg_import_savings_eur"} <= keys
    assert "home_surplus_eur" not in keys
    h1 = datetime(2026, 6, 1, 10, tzinfo=UTC)
    h2 = h1 + timedelta(hours=1)
    h3 = h2 + timedelta(hours=1)
    series = {d.key: stats.Series() for d in defs}
    for prefix in ("home_", "home_beg_"):
        series[prefix + "consumption_from_community"] = stats.Series(
            {h1: 2.0, h2: 1.0, h3: 3.0}
        )
        series[prefix + "consumption_eur"] = stats.Series({h1: 0.05, h2: 0.12})
    stats.derive_series(defs, series, None, {h1: 0.12, h3: 0.12})
    for prefix in ("home_", "home_beg_"):
        savings = series[prefix + "import_savings_eur"]
        # h2 has no price, h3 no cost: only h1 is known.
        assert savings.values == {h1: pytest.approx(0.19)}
        assert savings.unpriced == [h2]


def test_derive_supplier_energy_cost() -> None:
    """Issue #75: supplier kWh x supplier price, site only; unpriced listed."""

    defs = build_statistic_defs("home", [BEG], BOTH, supplier=True)
    by_key = {d.key: d for d in defs}
    assert by_key["home_supplier_eur"].name == "Home Supplier energy cost"
    assert by_key["home_supplier_eur"].gate_keys == ("home_consumption_from_supplier",)
    assert "home_beg_supplier_eur" not in by_key
    assert "home_supplier_eur" not in {
        d.key for d in build_statistic_defs("home", [BEG], BOTH, surplus=True)
    }
    h1 = datetime(2026, 6, 1, 10, tzinfo=UTC)
    h2 = h1 + timedelta(hours=1)
    series = {d.key: stats.Series() for d in defs}
    series["home_consumption_from_supplier"] = stats.Series({h1: 2.0, h2: 1.0})
    stats.derive_series(defs, series, None, {h1: 0.1224})
    assert series["home_supplier_eur"].values == {h1: pytest.approx(0.2448)}
    assert series["home_supplier_eur"].unpriced == [h2]


def test_derive_without_surplus_defs_is_noop() -> None:
    """Without a surplus price nothing is derived."""

    defs = build_statistic_defs("home", [BEG], BOTH)
    series = {d.key: stats.Series() for d in defs}
    stats.derive_series(defs, series, None)
    assert all(not s.values for s in series.values())


async def test_import_window_writes_surplus(hass: HomeAssistant, recorder_mock) -> None:
    """Surplus statistics are written with their unpriced hours reported."""

    day = date(2026, 6, 1)
    defs = [
        d
        for d in build_statistic_defs("home", [BEG], BOTH, surplus=True)
        if d.community is None
    ]
    hours = day_hours(day)
    data = _response(generation=dict.fromkeys(hours, 1.0))
    prices = dict.fromkeys(hours[:-1], 0.1)
    results = await async_import_window(
        hass, defs, {"beg": [data]}, _complete(day), day, day, prices=prices
    )
    assert results["home_surplus_eur"].rows == len(hours) - 1
    assert results["home_surplus_eur"].unpriced == [hours[-1]]
    rows = await _rows(hass, "eg_austria:home_surplus_eur")
    assert rows[0]["state"] == pytest.approx(0.07)
    meta = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={"eg_austria:home_surplus_eur"})
    )
    assert meta["eg_austria:home_surplus_eur"][1]["unit_of_measurement"] == "€"


def test_site_meter_from_first_non_faulty_community_issue_78() -> None:
    """The first community's faulty 0 doesn't hide the other's reading."""

    day = date(2026, 6, 3)
    h = local_day_start(day) + timedelta(hours=12)
    defs = [
        d
        for d in build_statistic_defs("home", [BEG, EEG], BOTH)
        if d.key == "home_generation"
    ]
    beg = _response(generation={h: 0.0}, faulty={h})
    eeg = _response(generation={h: 0.031})
    active = {"beg": {day}, "eeg": {day}}
    # Day not complete yet: a faulty hour alone would be unknown (rule 4).
    series = build_series(
        defs,
        {"beg": [beg], "eeg": [eeg]},
        _complete(day, generation=False),
        day,
        day,
        active_days=active,
    )
    assert series["home_generation"].values == {h: 0.031}
    # Both faulty: the first community's hour stays unknown as before.
    both = build_series(
        defs,
        {"beg": [beg], "eeg": [_response(generation={h: 0.0}, faulty={h})]},
        _complete(day, generation=False),
        day,
        day,
        active_days=active,
    )
    assert both["home_generation"].values == {}
