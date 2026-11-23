"""Tests for validation.py (spec 05; test plan cases 13-15)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.eg_austria.exceptions import SchemaError
from custom_components.eg_austria.models import (
    Aggregation,
    AnalysisData,
    DayCompleteness,
    parse_analysis,
    parse_day_completeness,
)
from custom_components.eg_austria.validation import (
    DEFAULT_MAX_HOURLY_KWH,
    RULE_BLOCKING,
    Correction,
    Severity,
    ValidationResult,
    check_history,
    day_totals,
    hours_in_day,
    local_day,
    local_midnight,
    schema_failure,
    validate_chunk,
    validate_completeness,
)

from .conftest import load_fixture

NORMAL = (date(2026, 6, 1), date(2026, 6, 7))
ONE_DAY = date(2026, 6, 3)
AUTUMN = date(2026, 10, 25)
SPRING = date(2026, 3, 29)
TAIL = (date(2026, 5, 1), date(2026, 5, 31))
SITE = "home"


def analysis(name: str) -> AnalysisData:
    """Parse an analysis-data fixture."""

    return parse_analysis(load_fixture(name))


def completeness(name: str) -> list[DayCompleteness]:
    """Parse a day-completeness fixture."""

    return parse_day_completeness(load_fixture(name))


def one_day(name: str, **kwargs) -> ValidationResult:
    """Validate a single-community (BEG only) fixture for ONE_DAY."""

    kwargs.setdefault("all_communities", False)
    return validate_chunk(
        ONE_DAY, ONE_DAY, {"beg": analysis(name)}, site=SITE, **kwargs
    )


def pair(beg: str, eeg: str, start: date = ONE_DAY, end: date = ONE_DAY, **kwargs):
    """Validate the BEG and EEG fixtures of one chunk."""

    return validate_chunk(
        start, end, {"beg": analysis(beg), "eeg": analysis(eeg)}, site=SITE, **kwargs
    )


def pair_raw(beg: dict, eeg: dict) -> ValidationResult:
    """Validate raw BEG and EEG responses of the NORMAL chunk."""

    return validate_chunk(
        *NORMAL,
        {"beg": parse_analysis(beg), "eeg": parse_analysis(eeg)},
        site=SITE,
    )


# ---------------------------------------------------------------------------
# Helpers


def test_hours_in_day_dst() -> None:
    """Local day length and conversions around DST."""

    assert hours_in_day(SPRING) == 23
    assert hours_in_day(AUTUMN) == 25
    assert hours_in_day(ONE_DAY) == 24
    assert local_midnight(ONE_DAY) == datetime(2026, 6, 2, 22, tzinfo=UTC)
    assert local_day(datetime(2026, 6, 2, 22, tzinfo=UTC)) == ONE_DAY


# ---------------------------------------------------------------------------
# Passing data


def test_normal_pair_passes() -> None:
    """Normal pair passes."""

    result = pair(
        "analysis_hour_normal_beg.json", "analysis_hour_normal_eeg.json", *NORMAL
    )
    assert result.ok
    assert result.findings == []
    assert not result.schema_failed


@pytest.mark.parametrize(
    "name",
    [
        "analysis_hour_normal_beg.json",
        "analysis_hour_normal_eeg.json",
    ],
)
def test_single_normal_passes_with_subset(name: str) -> None:
    """Single normal passes with subset."""

    result = validate_chunk(
        *NORMAL, {"c": analysis(name)}, site=SITE, all_communities=False
    )
    assert result.findings == []


def test_site_without_generation_passes_issue_78() -> None:
    """Issue #78: no generation totals or flags in the response is no finding."""

    raw = load_fixture("analysis_hour_normal_beg.json")
    for key in [k for k in raw if k.startswith(("totalGeneration", "generation"))]:
        if key != "generationDataPoints":
            del raw[key]
    raw["generationDataPoints"] = []
    result = validate_chunk(
        *NORMAL, {"beg": parse_analysis(raw)}, site=SITE, all_communities=False
    )
    assert not result.schema_failed
    assert result.findings == []


def test_faulty_meter_zero_is_not_a_p6_difference_issue_78() -> None:
    """One community reports a faulty 0, the other the reading: no P6 / P4."""

    beg = load_fixture("analysis_hour_normal_beg.json")
    eeg = load_fixture("analysis_hour_normal_eeg.json")
    point = eeg["generationDataPoints"][12]
    assert point["generation"] > 0
    point["generation"] = 0
    point["generationFaulty"] = True
    result = pair_raw(beg, eeg)
    assert "P6" not in result.rule_ids()
    assert "P4" not in result.rule_ids()
    # A real difference (not flagged) still fails.
    point["generationFaulty"] = False
    assert "P6" in pair_raw(beg, eeg).rule_ids()


def test_p4_meter_from_first_non_faulty_response() -> None:
    """With the faulty response first, P4 uses the other one's meter."""

    beg = load_fixture("analysis_hour_normal_beg.json")
    eeg = load_fixture("analysis_hour_normal_eeg.json")
    point = beg["generationDataPoints"][12]
    point["generation"] = 0
    point["generationFaulty"] = True
    assert "P4" not in pair_raw(beg, eeg).rule_ids()


def test_single_community_subset_strict_p4_fails() -> None:
    """A subset of the communities can't satisfy the P4 equality."""

    result = validate_chunk(
        *NORMAL, {"beg": analysis("analysis_hour_normal_beg.json")}, site=SITE
    )
    assert result.rule_ids() == {"P4"}


@pytest.mark.parametrize(
    "name",
    [
        "analysis_hour_null_hours.json",
        "analysis_hour_noise.json",
        "analysis_empty_day.json",
    ],
)
def test_other_valid_fixtures_pass(name: str) -> None:
    """Other valid fixtures pass."""

    assert one_day(name).findings == []


def test_tail_incomplete_passes() -> None:
    """Community-incomplete and meter-missing days (0 + flag) are valid data."""

    result = validate_chunk(
        *TAIL,
        {"beg": analysis("analysis_hour_tail_incomplete.json")},
        site=SITE,
        all_communities=False,
    )
    assert result.findings == []


def test_schema_extra_collected() -> None:
    """Schema extra collected."""

    result = one_day("analysis_hour_noise.json")
    assert "exampleNewTopLevelKey" in result.schema_extra
    assert "consumptionDataPoints[].exampleNewPointKey" in result.schema_extra
    assert result.ok


# ---------------------------------------------------------------------------
# DST (case 14)


def test_dst_autumn_hour_passes() -> None:
    """Dst autumn hour passes."""

    data = analysis("analysis_hour_dst_autumn.json")
    assert len(data.consumption) == 24  # gap hour missing, 25-hour day
    result = validate_chunk(
        AUTUMN, AUTUMN, {"beg": data}, site=SITE, all_communities=False
    )
    assert result.findings == []


def test_dst_autumn_day_passes() -> None:
    """Dst autumn day passes."""

    result = validate_chunk(
        AUTUMN,
        AUTUMN,
        {"beg": analysis("analysis_day_dst_autumn.json")},
        site=SITE,
        aggregation=Aggregation.DAY,
        all_communities=False,
    )
    assert result.findings == []


def test_dst_spring_hour_passes() -> None:
    """Dst spring hour passes."""

    data = analysis("analysis_hour_dst_spring.json")
    assert len(data.consumption) == 23
    result = validate_chunk(
        SPRING, SPRING, {"beg": data}, site=SITE, all_communities=False
    )
    assert result.findings == []


def test_dst_autumn_p4_skips_gap_hour() -> None:
    """P4 runs on the hours present; the missing gap hour is no failure."""

    data = analysis("analysis_hour_dst_autumn.json")
    gap = local_midnight(AUTUMN) + timedelta(hours=2)
    assert gap not in {p.start for p in data.consumption}
    result = validate_chunk(AUTUMN, AUTUMN, {"beg": data}, all_communities=False)
    assert "P4" not in result.rule_ids()


def test_day_point_not_at_local_midnight_fails_s5() -> None:
    """Day point not at local midnight fails S5."""

    data = analysis("analysis_day_dst_autumn.json")
    moved = replace(
        data.consumption[0], start=data.consumption[0].start + timedelta(hours=1)
    )
    data = replace(data, consumption=(moved,))
    result = validate_chunk(AUTUMN, AUTUMN, {"beg": data}, aggregation=Aggregation.DAY)
    assert "S5" in result.rule_ids()


def test_day_limit_scales_with_hours() -> None:
    """For Day the P2 limit is the hourly bound times the day's hours."""

    data = analysis("analysis_day_dst_autumn.json")
    result = validate_chunk(
        AUTUMN,
        AUTUMN,
        {"beg": data},
        aggregation=Aggregation.DAY,
        all_communities=False,
        max_hourly_kwh=1,
    )
    day_value = data.generation[0].value
    assert day_value is not None
    assert ("P2" in result.rule_ids()) == (day_value > 25)


# ---------------------------------------------------------------------------
# Stage 1: shape (case 13)


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("analysis_bad_s1_not_object.json", "S1"),
        ("analysis_bad_s2_missing_key.json", "S2"),
        ("analysis_bad_s2_renamed_key.json", "S2"),
        ("analysis_bad_s3_string_value.json", "S3"),
        ("analysis_bad_s3_bool_flag_as_number.json", "S3"),
        ("analysis_bad_s4_naive_timestamp.json", "S4"),
    ],
)
def test_parser_rules_become_schema_failures(name: str, rule: str) -> None:
    """Parser rules become schema failures."""

    with pytest.raises(SchemaError) as err:
        analysis(name)
    result = schema_failure(err.value, chunk=(ONE_DAY, ONE_DAY))
    assert result.rule_ids() == {rule}
    assert result.schema_failed
    assert not result.ok
    assert result.is_blocked("home_consumption", ONE_DAY)
    assert result.is_blocked("anything", date(2020, 1, 1))  # whole run


def test_schema_failure_redacts() -> None:
    """Schema failure redacts."""

    err = SchemaError("S2", "x", "member 9000001 mp 1001 missing")
    result = schema_failure(err, sensitive=("9000001", 1001))
    message = result.findings[0].message
    assert "9000001" not in message
    assert "1001" not in message


def test_schema_failure_unknown_rule_falls_back() -> None:
    """Schema failure unknown rule falls back."""

    result = schema_failure(SchemaError("X9", "a", "b"))
    assert result.rule_ids() == {"S2"}
    assert result.schema_failed


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("analysis_bad_s5_not_whole_hour.json", "S5"),
        ("analysis_bad_s6_duplicate.json", "S6"),
        ("analysis_bad_s7_out_of_range.json", "S7"),
    ],
)
def test_shape_rules_on_models(name: str, rule: str) -> None:
    """Shape rules on models."""

    result = one_day(name)
    assert rule in result.rule_ids()
    assert result.schema_failed
    assert all(f.is_shape for f in result.findings)  # stage 2 skipped
    assert all(f.statistic is None for f in result.findings)


def test_s8_too_many_points() -> None:
    """Built in code: no fixture can have more points without breaking S5-S7."""

    data = analysis("analysis_hour_normal_beg.json")
    extra = replace(
        data.consumption[-1], start=data.consumption[-1].start + timedelta(hours=1)
    )
    data = replace(data, consumption=(*data.consumption, extra))
    result = validate_chunk(*NORMAL, {"beg": data}, all_communities=False)
    assert "S8" in result.rule_ids()
    assert result.schema_failed


def test_s8_limit_counts_dst_hours() -> None:
    """A 25-hour autumn day allows 25 points; the 24-hour limit is not used."""

    data = analysis("analysis_hour_dst_autumn.json")
    gap = local_midnight(AUTUMN) + timedelta(hours=2)
    filled = replace(data.consumption[0], start=gap)
    data = replace(
        data,
        consumption=tuple(sorted((*data.consumption, filled), key=lambda p: p.start)),
    )
    assert len(data.consumption) == 25
    result = validate_chunk(AUTUMN, AUTUMN, {"beg": data}, all_communities=False)
    assert not {"S5", "S6", "S7", "S8"} & result.rule_ids()


def test_s8_spring_day_rejects_24_points() -> None:
    """S8 spring day rejects 24 points."""

    data = analysis("analysis_hour_dst_spring.json")
    extra = replace(
        data.consumption[0], start=local_midnight(SPRING) - timedelta(hours=1)
    )
    data = replace(data, consumption=(extra, *data.consumption))
    result = validate_chunk(SPRING, SPRING, {"beg": data})
    assert {"S7", "S8"} <= result.rule_ids()


def test_s9_completeness_normal_passes() -> None:
    """S9 completeness normal passes."""

    result = validate_completeness(
        completeness("day_completeness_normal.json"), *NORMAL
    )
    assert result.findings == []


def test_s9_completeness_future_passes() -> None:
    """S9 completeness future passes."""

    entries = completeness("day_completeness_future.json")
    result = validate_completeness(entries, date(2026, 6, 1), date(2026, 6, 10))
    assert result.findings == []


def test_s9_completeness_missing_day_is_not_a_failure() -> None:
    """S9 completeness missing day is not a failure."""

    entries = completeness("day_completeness_normal.json")
    del entries[2]
    assert validate_completeness(entries, *NORMAL).findings == []


def test_s9_duplicate() -> None:
    """S9 duplicate."""

    result = validate_completeness(
        completeness("day_completeness_bad_duplicate.json"), *NORMAL
    )
    assert result.rule_ids() == {"S9"}
    assert result.schema_failed


def test_s9_out_of_range() -> None:
    """S9 out of range."""

    entries = completeness("day_completeness_normal.json")
    result = validate_completeness(entries, NORMAL[0], date(2026, 6, 5))
    assert result.rule_ids() == {"S9"}
    assert "2 times" in result.findings[0].message


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("day_completeness_bad_date.json", "S9"),
        ("day_completeness_bad_numeric.json", "S3"),
    ],
)
def test_s9_parser_errors(name: str, rule: str) -> None:
    """S9 parser errors."""

    with pytest.raises(SchemaError) as err:
        completeness(name)
    assert schema_failure(err.value).rule_ids() == {rule}


def test_completeness_extra_keys() -> None:
    """Completeness extra keys."""

    entries = completeness("day_completeness_normal.json")
    entries[0] = replace(entries[0], extra={"newFlag": True})
    result = validate_completeness(entries, *NORMAL)
    assert result.schema_extra == {"day-completeness[].newFlag"}
    assert result.ok


# ---------------------------------------------------------------------------
# Stage 2: plausibility (cases 13, 15)


def test_p1_negative() -> None:
    """P1 negative."""

    result = one_day("analysis_bad_p1_negative.json")
    assert "P1" in result.rule_ids()
    p1 = [f for f in result.failures if f.rule == "P1"]
    assert {f.statistic for f in p1} == {"home_generation_to_surplus"}
    assert result.is_blocked("home_generation_to_surplus", ONE_DAY)
    assert not result.is_blocked("home_generation_to_surplus", ONE_DAY + timedelta(1))
    assert not result.schema_failed


def test_p2_tenfold() -> None:
    """P2 tenfold."""

    result = one_day("analysis_bad_p2_tenfold.json")
    p2 = {f.statistic for f in result.failures if f.rule == "P2"}
    assert p2 == {
        "home_generation",
        "home_generation_to_community",
        "home_beg_generation_to_community",
        "home_generation_to_surplus",
    }
    assert not result.is_blocked("home_consumption", ONE_DAY)


def test_p2_option_threshold() -> None:
    """P2 option threshold."""

    result = validate_chunk(
        *NORMAL,
        {"beg": analysis("analysis_hour_normal_beg.json")},
        all_communities=False,
        max_hourly_kwh=0.1,
    )
    assert "P2" in result.rule_ids()
    assert DEFAULT_MAX_HOURLY_KWH == 50


def test_p3_community_gt_meter() -> None:
    """P3 community gt meter."""

    result = one_day("analysis_bad_p3_community_gt_meter.json")
    p3 = {f.statistic for f in result.failures if f.rule == "P3"}
    assert p3 == {
        "home_consumption",
        "home_consumption_from_community",
        "home_beg_consumption_from_community",
    }
    assert not result.is_blocked("home_generation", ONE_DAY)


def test_p3_within_tolerance() -> None:
    """P3 within tolerance."""

    data = analysis("analysis_hour_normal_beg.json")
    p = data.consumption[10]
    assert p.value is not None
    nudged = replace(p, community=p.value + 0.0005)
    data = replace(
        data, consumption=(*data.consumption[:10], nudged, *data.consumption[11:])
    )
    data = replace(data, totals=replace(data.totals, consumption_from_community=None))
    result = validate_chunk(*NORMAL, {"beg": data}, all_communities=False)
    assert "P3" not in result.rule_ids()


def test_p4_sum_pair() -> None:
    """P4 sum pair."""

    result = pair("analysis_bad_p4_sum_beg.json", "analysis_bad_p4_sum_eeg.json")
    assert "P4" in result.rule_ids()
    assert "P6" not in result.rule_ids()  # residual shifted in both alike
    p4 = {f.statistic for f in result.failures if f.rule == "P4"}
    assert p4 == {
        "home_generation",
        "home_generation_to_surplus",
        "home_generation_to_community",
        "home_beg_generation_to_community",
        "home_eeg_generation_to_community",
    }
    assert not any(s.startswith("home_consumption") for s in p4)


def test_p4_subset_one_sided() -> None:
    """With a subset, parts + residual above the meter still fails P4."""

    result = one_day("analysis_bad_p4_sum_beg.json")
    assert "P4" in result.rule_ids()


def test_p5_totals_mismatch() -> None:
    """P5 totals mismatch."""

    result = one_day("analysis_bad_p5_totals_mismatch.json")
    assert result.rule_ids() == {"P5"}
    assert {f.statistic for f in result.failures} == {"home_generation"}


def test_p5_null_totals_skipped() -> None:
    """P5 null totals skipped."""

    data = analysis("analysis_bad_p5_totals_mismatch.json")
    data = replace(data, totals=replace(data.totals, generation=None))
    result = validate_chunk(ONE_DAY, ONE_DAY, {"beg": data}, all_communities=False)
    assert result.findings == []


def test_p6_differs_pair() -> None:
    """P6 differs pair."""

    result = pair(
        "analysis_bad_p6_differs_beg.json", "analysis_bad_p6_differs_eeg.json"
    )
    assert "P6" in result.rule_ids()
    p6 = {f.statistic for f in result.failures if f.rule == "P6"}
    assert p6 == {"home_consumption"}


def test_p6_null_in_one_response() -> None:
    """P6 null in one response."""

    beg = analysis("analysis_hour_normal_beg.json")
    eeg = analysis("analysis_hour_normal_eeg.json")
    eeg = replace(
        eeg,
        consumption=(replace(eeg.consumption[0], excess=None), *eeg.consumption[1:]),
    )
    eeg = replace(eeg, totals=replace(eeg.totals, consumption_in_excess=None))
    result = validate_chunk(*NORMAL, {"beg": beg, "eeg": eeg}, site=SITE)
    assert result.rule_ids() == {"P6"}
    assert {f.statistic for f in result.failures} == {"home_consumption_from_supplier"}


@pytest.mark.parametrize(
    ("beg", "eeg", "rule"),
    [
        ("analysis_bad_p4_sum_beg.json", "analysis_bad_p4_sum_eeg.json", "P4"),
        ("analysis_bad_p6_differs_beg.json", "analysis_bad_p6_differs_eeg.json", "P6"),
    ],
)
def test_constant_downgrades_rule_to_warning(beg: str, eeg: str, rule: str) -> None:
    """Case 15: off -> warning only; on (default) -> blocking."""

    on = pair(beg, eeg)
    assert rule in {f.rule for f in on.failures}

    off = pair(beg, eeg, blocking={rule: False})
    assert rule not in {f.rule for f in off.failures}
    assert rule in {f.rule for f in off.warnings}
    assert all(f.severity is Severity.WARNING for f in off.warnings)


def test_downgrade_p5() -> None:
    """Downgrade P5."""

    off = one_day("analysis_bad_p5_totals_mismatch.json", blocking={"P5": False})
    assert off.ok
    assert off.rule_ids() == {"P5"}
    assert not off.is_blocked("home_generation", ONE_DAY)


def test_rule_blocking_defaults() -> None:
    """Rule blocking defaults."""

    assert all(RULE_BLOCKING[f"P{i}"] for i in range(1, 7))


def test_findings_grouped_and_value_free() -> None:
    """Findings grouped and value free."""

    result = one_day("analysis_hour_null_hours.json", max_hourly_kwh=0.1)
    p2 = [f for f in result.findings if f.rule == "P2"]
    assert len(p2) == len({f.statistic for f in p2})  # one per statistic
    for f in p2:
        assert "times" in f.message
        assert f.chunk == (ONE_DAY, ONE_DAY)
        assert "0.1" not in f.message


def test_no_site_slug() -> None:
    """No site slug."""

    result = validate_chunk(
        ONE_DAY,
        ONE_DAY,
        {"beg": analysis("analysis_bad_p1_negative.json")},
        all_communities=False,
    )
    assert "generation_to_surplus" in {f.statistic for f in result.failures}


def test_empty_responses() -> None:
    """Empty responses."""

    assert validate_chunk(ONE_DAY, ONE_DAY, {}).findings == []


# ---------------------------------------------------------------------------
# Stage 3: history guard (case 13)


def _complete_day() -> list[DayCompleteness]:
    """Completeness with ONE_DAY complete on both sides."""

    return [DayCompleteness(ONE_DAY, True, True)]


def test_day_totals_only_complete_days() -> None:
    """Day totals only complete days."""

    data = {"beg": analysis("analysis_hour_tail_incomplete.json")}
    entries = completeness("day_completeness_tail_incomplete.json")
    totals = day_totals(data, entries, site=SITE)
    incomplete = {e.day for e in entries if not e.consumption_complete}
    assert incomplete
    assert not {d for (_, d) in totals} & incomplete
    assert ("home_consumption", TAIL[0]) in totals


def test_day_totals_site_community_is_sum() -> None:
    """Day totals site community is sum."""

    beg = analysis("analysis_hour_normal_beg.json")
    eeg = analysis("analysis_hour_normal_eeg.json")
    entries = completeness("day_completeness_normal.json")
    totals = day_totals({"beg": beg, "eeg": eeg}, entries, site=SITE)
    key = "home_generation_to_community"
    site_total = totals[key, ONE_DAY][0]
    parts = (
        totals["home_beg_generation_to_community", ONE_DAY][0]
        + totals["home_eeg_generation_to_community", ONE_DAY][0]
    )
    assert site_total == pytest.approx(parts)
    alone = day_totals({"beg": beg}, entries, site=SITE)
    assert totals["home_generation", ONE_DAY] == alone["home_generation", ONE_DAY]


def test_h1_all_zero_held() -> None:
    """H1 all zero held."""

    data = {"beg": analysis("analysis_bad_h1_all_zero.json")}
    stored = {
        ("home_generation", ONE_DAY): 12.5,
        ("home_consumption", ONE_DAY): 9.0,
    }
    result = check_history(data, _complete_day(), stored, site=SITE)
    assert result.rule_ids() == {"H1"}
    assert result.held_days == {
        ("home_generation", ONE_DAY),
        ("home_consumption", ONE_DAY),
    }
    assert result.is_blocked("home_generation", ONE_DAY)
    assert not result.is_blocked("home_generation", ONE_DAY + timedelta(1))
    assert not result.schema_failed
    assert result.held_by_statistic() == {
        "home_consumption": [ONE_DAY],
        "home_generation": [ONE_DAY],
    }
    assert "12.5" not in result.findings[0].message


def test_h1_not_held_when_stored_zero_or_absent() -> None:
    """H1 not held when stored zero or absent."""

    data = {"beg": analysis("analysis_bad_h1_all_zero.json")}
    stored = {("home_generation", ONE_DAY): 0.0}
    result = check_history(data, _complete_day(), stored, site=SITE)
    assert result.findings == []
    assert result.held_days == set()


def test_h1_not_held_when_incomplete() -> None:
    """H1 not held when incomplete."""

    data = {"beg": analysis("analysis_bad_h1_all_zero.json")}
    stored = {("home_generation", ONE_DAY): 12.5}
    result = check_history(
        data, [DayCompleteness(ONE_DAY, True, False)], stored, site=SITE
    )
    assert result.held_days == set()


def test_h1_released() -> None:
    """H1 released."""

    data = {"beg": analysis("analysis_bad_h1_all_zero.json")}
    stored = {("home_generation", ONE_DAY): 12.5}
    result = check_history(
        data,
        _complete_day(),
        stored,
        site=SITE,
        released=[("home_generation", ONE_DAY)],
    )
    assert result.held_days == set()
    # The release doesn't hide the large change from diagnostics.
    assert result.corrections == [Correction(ONE_DAY, "home_generation", -1.0)]


def test_h1_normal_day_passes() -> None:
    """H1 normal day passes."""

    data = {"beg": analysis("analysis_hour_normal_beg.json")}
    entries = completeness("day_completeness_normal.json")
    totals = day_totals(data, entries, site=SITE)
    result = check_history(
        data, entries, {k: v[0] for k, v in totals.items()}, site=SITE
    )
    assert result.findings == []
    assert result.corrections == []


def test_h2_large_change_recorded_not_blocked() -> None:
    """H2 large change recorded not blocked."""

    data = {"beg": analysis("analysis_hour_normal_beg.json")}
    entries = completeness("day_completeness_normal.json")
    total = day_totals(data, entries, site=SITE)["home_generation", ONE_DAY][0]
    stored = {("home_generation", ONE_DAY): total / 3}
    result = check_history(data, entries, stored, site=SITE)
    assert result.ok
    assert result.findings == []
    assert len(result.corrections) == 1
    corr = result.corrections[0]
    assert corr.statistic == "home_generation"
    assert corr.day == ONE_DAY
    assert corr.relative_change == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("ratio", "recorded"),
    [
        (1.4, False),  # +40 %: below the relative threshold
        (1.6, True),
    ],
)
def test_h2_relative_threshold(ratio: float, recorded: bool) -> None:
    """H2 relative threshold."""

    data = {"beg": analysis("analysis_hour_normal_beg.json")}
    entries = completeness("day_completeness_normal.json")
    total = day_totals(data, entries, site=SITE)["home_generation", ONE_DAY][0]
    assert total > 3  # the absolute threshold (1 kWh) is met
    stored = {("home_generation", ONE_DAY): total / ratio}
    result = check_history(data, entries, stored, site=SITE)
    assert bool(result.corrections) is recorded


def test_h2_small_absolute_change_ignored() -> None:
    """A change above 50 % but below 1 kWh is not recorded."""

    data = {"beg": analysis("analysis_hour_normal_beg.json")}
    entries = completeness("day_completeness_normal.json")
    total = day_totals(data, entries, site=SITE)["home_generation", ONE_DAY][0]
    stored = {("home_generation", ONE_DAY): total - 0.9}
    stored_small = {("home_generation", ONE_DAY): 0.5}
    assert check_history(data, entries, stored, site=SITE).corrections == []
    assert total - 0.5 > 1
    assert check_history(data, entries, stored_small, site=SITE).corrections


def test_h2_from_zero() -> None:
    """H2 from zero."""

    data = {"beg": analysis("analysis_hour_normal_beg.json")}
    entries = completeness("day_completeness_normal.json")
    stored = {("home_generation", ONE_DAY): 0.0}
    result = check_history(data, entries, stored, site=SITE)
    assert result.corrections == [Correction(ONE_DAY, "home_generation", None)]


# ---------------------------------------------------------------------------
# Result handling


def test_merge_combines_everything() -> None:
    """Merge combines everything."""

    a = one_day("analysis_hour_noise.json")
    b = one_day("analysis_bad_p5_totals_mismatch.json")
    c = check_history(
        {"beg": analysis("analysis_bad_h1_all_zero.json")},
        _complete_day(),
        {("home_generation", ONE_DAY): 5.0},
        site=SITE,
    )
    merged = ValidationResult().merge(a, b, c)
    assert merged.rule_ids() == {"P5", "H1"}
    assert "exampleNewTopLevelKey" in merged.schema_extra
    assert merged.held_days == {("home_generation", ONE_DAY)}
    assert merged.blocked_statistics() == {"home_generation"}
    assert not merged.ok
    assert not merged.schema_failed


def test_day_totals_skip_null_hours() -> None:
    """Null hours are left out of the day total, not counted as 0."""

    data = analysis("analysis_hour_null_hours.json")
    totals = day_totals({"beg": data}, _complete_day(), site=SITE)
    known = [p.value for p in data.consumption if p.value is not None]
    assert len(known) == 22
    assert totals["home_consumption", ONE_DAY][0] == pytest.approx(sum(known))
