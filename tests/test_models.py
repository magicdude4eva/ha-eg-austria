"""Tests for the response models (spec 07 cases 9-12)."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta
import re

import pytest

from custom_components.eg_austria.exceptions import SchemaError
from custom_components.eg_austria.models import (
    AnalysisData,
    Direction,
    parse_analysis,
    parse_day_completeness,
    parse_maintenance,
    parse_metering_points,
    parse_session,
    parse_version,
)

from .conftest import FIXTURES, load_fixture

NORMAL_ANALYSIS = [
    "analysis_hour_normal_beg.json",
    "analysis_hour_normal_eeg.json",
    "analysis_hour_tail_incomplete.json",
    "analysis_hour_dst_autumn.json",
    "analysis_hour_dst_spring.json",
    "analysis_day_dst_autumn.json",
    "analysis_hour_null_hours.json",
    "analysis_hour_noise.json",
    "analysis_empty_day.json",
]
# Plausibility / history fixtures parse fine; S3 rejects them later.
PLAUSIBILITY_ANALYSIS = sorted(
    p.name
    for p in FIXTURES.glob("analysis_bad_*.json")
    if p.name.split("_")[2][0] in "ph" or p.name.split("_")[2] in ("s5", "s6", "s7")
)


def parse(name: str) -> AnalysisData:
    """Parse an analysis fixture."""

    return parse_analysis(load_fixture(name))


# ---------------------------------------------------------------------------
# 9. Normal fixtures, sorting, int → float


@pytest.mark.parametrize("name", NORMAL_ANALYSIS + PLAUSIBILITY_ANALYSIS)
def test_parses_and_sorts_ascending(name: str) -> None:
    """Every non-shape fixture parses; points are sorted by start, UTC."""

    data = parse(name)
    raw = load_fixture(name)
    for direction in Direction:
        points = data.points(direction)
        assert [p.start for p in points] == sorted(p.start for p in points)
        assert all(p.start.tzinfo is UTC for p in points)
        assert all(p.direction is direction for p in points)
    assert len(data.consumption) == len(raw["consumptionDataPoints"])
    # The portal sends newest first.
    if len(data.consumption) > 1:
        assert (
            raw["consumptionDataPoints"][0]["dateTimeFrom"]
            > raw["consumptionDataPoints"][-1]["dateTimeFrom"]
        )


def test_ints_become_floats() -> None:
    """JSON ints (zero or whole values) are converted to float."""

    data = parse("analysis_hour_normal_beg.json")
    night = data.generation[0]
    assert night.value == 0.0
    assert type(night.value) is float
    assert type(night.community) is float
    assert type(data.totals.consumption) is float
    raw = load_fixture("analysis_hour_normal_beg.json")
    assert any(type(p["generation"]) is int for p in raw["generationDataPoints"])


def test_normal_values_and_flags() -> None:
    """Fields map to the right attributes; complete days have no flags."""

    data = parse("analysis_hour_normal_beg.json")
    assert len(data.consumption) == len(data.generation) == 7 * 24
    assert data.consumption[0].start == datetime(2026, 5, 31, 22, tzinfo=UTC)
    assert data.consumption[0].local_wall_time == "2026-06-01T00:00:00.000Z"
    for p in data.consumption:
        assert not p.faulty
        assert not p.community_faulty
        assert p.value_complete == p.value
        # Single-community response: the residual is global (other community).
        assert p.community + p.excess <= p.value + 0.002
    total = sum(p.value for p in data.consumption)
    assert total == pytest.approx(data.totals.consumption, abs=0.01)
    assert data.flags.consumption is False
    assert not data.is_empty
    assert data.extra == {}
    assert data.extra_keys() == set()


def test_flags_kept_per_point() -> None:
    """Meter-missing day: 0 with faulty; community-incomplete: community flag."""

    data = parse("analysis_hour_tail_incomplete.json")
    last_day = [
        p for p in data.consumption if p.start >= datetime(2026, 5, 30, 22, tzinfo=UTC)
    ]
    assert len(last_day) == 24
    assert all(p.value == 0.0 and p.faulty for p in last_day)
    incomplete = [
        p
        for p in data.generation
        if datetime(2026, 5, 17, 22, tzinfo=UTC)
        <= p.start
        < datetime(2026, 5, 30, 22, tzinfo=UTC)
    ]
    assert incomplete
    assert all(p.community_faulty and not p.faulty for p in incomplete)
    assert all(p.community == 0.0 for p in incomplete)
    assert data.flags.consumption is True


def test_null_hours_are_none() -> None:
    """Null values stay None (unknown), never 0."""

    data = parse("analysis_hour_null_hours.json")
    nulls = [p for p in data.consumption if p.value is None]
    assert len(nulls) == 2
    assert all(p.community is None and p.excess is None for p in nulls)


def test_empty_day() -> None:
    """Empty lists and null totals are valid."""

    data = parse("analysis_empty_day.json")
    assert data.is_empty
    assert data.totals.consumption is None


def test_dst_autumn_shape() -> None:
    """Autumn day: 24 points, first local 02:00 missing, local 01:00 flagged."""

    data = parse("analysis_hour_dst_autumn.json")
    starts = [p.start for p in data.consumption]
    assert len(starts) == 24
    gap = datetime(2026, 10, 25, 0, tzinfo=UTC)
    assert gap not in starts
    before = next(p for p in data.consumption if p.start == gap - timedelta(hours=1))
    assert before.faulty
    day = parse("analysis_day_dst_autumn.json")
    assert len(day.consumption) == 1
    assert day.consumption[0].start == datetime(2026, 10, 24, 22, tzinfo=UTC)
    # The Day value includes the missing hour.
    assert day.consumption[0].value > sum(p.value for p in data.consumption)


def test_dst_spring_shape() -> None:
    """Spring day: 23 points."""

    data = parse("analysis_hour_dst_spring.json")
    assert len(data.consumption) == len(data.generation) == 23


# ---------------------------------------------------------------------------
# 10. ``date`` is never parsed


def test_date_string_never_parsed() -> None:
    """An unparsable locale ``date`` passes; it is not even kept."""

    data = parse("analysis_hour_noise.json")
    newest = data.consumption[-1]
    assert "date" not in newest.extra
    assert newest.local_wall_time is not None


# ---------------------------------------------------------------------------
# 11. Extra keys and missing keys


def test_extra_keys_kept() -> None:
    """Unknown keys go to ``extra``; ``extra_keys`` lists names only."""

    data = parse("analysis_hour_noise.json")
    assert data.extra == {"exampleNewTopLevelKey": {"nested": True}}
    assert data.consumption[-1].extra == {"exampleNewPointKey": 1}
    assert data.extra_keys() == {
        "exampleNewTopLevelKey",
        "consumptionDataPoints[].exampleNewPointKey",
    }


@pytest.mark.parametrize(
    ("name", "rule", "path"),
    [
        ("analysis_bad_s1_not_object.json", "S1", ""),
        (
            "analysis_bad_s2_missing_key.json",
            "S2",
            "consumptionDataPoints[0].consumptionFaulty",
        ),
        (
            "analysis_bad_s2_renamed_key.json",
            "S2",
            "generationDataPoints[0].generation",
        ),
        (
            "analysis_bad_s3_string_value.json",
            "S3",
            "consumptionDataPoints[{i}].consumption",
        ),
        (
            "analysis_bad_s3_bool_flag_as_number.json",
            "S3",
            "consumptionDataPoints[0].consumptionFaulty",
        ),
        (
            "analysis_bad_s4_naive_timestamp.json",
            "S4",
            "consumptionDataPoints[0].dateTimeFrom",
        ),
    ],
)
def test_shape_errors(name: str, rule: str, path: str) -> None:
    """Shape fixtures raise SchemaError with rule id and key path."""

    with pytest.raises(SchemaError) as excinfo:
        parse(name)
    assert excinfo.value.rule == rule
    if "{i}" in path:
        assert re.fullmatch(path.replace("[{i}]", r"\[\d+\]"), excinfo.value.path)
    else:
        assert excinfo.value.path == path


@pytest.mark.parametrize(
    "key",
    [
        "consumptionDataPoints",
        "generationDataPoints",
        "totalConsumption",
        "totalGenerationInExcess",
        "generationToCommunityFaulty",
    ],
)
def test_missing_top_level_key(key: str) -> None:
    """Missing required top-level keys raise S2 with the key as path."""

    raw = load_fixture("analysis_hour_null_hours.json")
    del raw[key]
    with pytest.raises(SchemaError, match="required key missing") as excinfo:
        parse_analysis(raw)
    assert excinfo.value.path == key


def test_offset_timestamp_converted_to_utc() -> None:
    """``+01:00`` offsets are accepted and converted (S4)."""

    raw = load_fixture("analysis_hour_null_hours.json")
    raw["consumptionDataPoints"][0]["dateTimeFrom"] = "2026-06-03T12:00:00+02:00"
    data = parse_analysis(raw)
    assert datetime(2026, 6, 3, 10, tzinfo=UTC) in [p.start for p in data.consumption]
    raw["consumptionDataPoints"][0]["dateTimeFrom"] = "yesterday"
    with pytest.raises(SchemaError, match="S4"):
        parse_analysis(raw)


# ---------------------------------------------------------------------------
# 12. Boolean vs numeric *Complete


def test_completeness_booleans() -> None:
    """day-completeness flags are booleans; null means not complete."""

    entries = parse_day_completeness(load_fixture("day_completeness_future.json"))
    assert [e.day for e in entries] == [date(2026, 6, d) for d in range(1, 11)]
    by_day = {e.day: e for e in entries}
    assert by_day[date(2026, 6, 1)].consumption_complete is True
    assert by_day[date(2026, 6, 7)].complete(Direction.GENERATION) is False
    assert by_day[date(2026, 6, 9)].consumption_complete is False  # null
    assert parse_day_completeness([]) == []


def test_completeness_number_rejected() -> None:
    """A numeric flag in day-completeness is a schema error, not truthy."""

    with pytest.raises(SchemaError) as excinfo:
        parse_day_completeness(load_fixture("day_completeness_bad_numeric.json"))
    assert excinfo.value.rule == "S3"
    assert excinfo.value.path == "[0].consumptionComplete"


def test_analysis_complete_bool_rejected() -> None:
    """A boolean in analysis-data's numeric ``*Complete`` is a schema error."""

    raw = load_fixture("analysis_hour_null_hours.json")
    raw["generationDataPoints"][0]["generationComplete"] = True
    with pytest.raises(SchemaError) as excinfo:
        parse_analysis(raw)
    assert excinfo.value.path == "generationDataPoints[0].generationComplete"


def test_completeness_bad_date_and_duplicates() -> None:
    """Bad dates raise S9; duplicates parse (S3 slice checks uniqueness)."""

    with pytest.raises(SchemaError) as excinfo:
        parse_day_completeness(load_fixture("day_completeness_bad_date.json"))
    assert excinfo.value.rule == "S9"
    dup = parse_day_completeness(load_fixture("day_completeness_bad_duplicate.json"))
    assert len({e.day for e in dup}) == len(dup) - 1
    with pytest.raises(SchemaError, match="S1"):
        parse_day_completeness({"date": "2026-06-01"})


# ---------------------------------------------------------------------------
# metering-point, session, maintenance, version


def test_metering_point_single() -> None:
    """The adapter maps locations, metering points and activations."""

    info = parse_metering_points(load_fixture("metering_point_single.json"))
    (loc,) = info.locations
    assert loc.id == "5001"
    assert loc.label == "Example street 1"
    assert [(m.id, m.direction) for m in loc.metering_points] == [
        (1001, Direction.CONSUMPTION),
        (1002, Direction.GENERATION),
    ]
    mp = loc.metering_points[0]
    assert mp.number_tail == "1001"
    assert [
        (a.community_id, a.community_type, a.community_name) for a in mp.activations
    ] == [
        (2001, "CC", "Example BEG"),
        (2002, "RC_R", "Example EEG"),
    ]
    act = mp.activations[1]
    assert act.active_from == date(2026, 2, 1)
    assert act.active_to is None
    assert act.status == "Active"
    assert act.extra["activePartFactor"] == 50
    assert "receivingZeroPlans" in mp.extra
    assert info.location("5001") is loc
    assert info.location("nope") is None
    assert len(list(info.iter_activations("5001"))) == 4
    # Sensitive values never show up in repr.
    text = repr(info)
    for secret in ("Example street", "Example BEG", "AT0000", "9000001"):
        assert secret not in text


def test_metering_point_two_locations() -> None:
    """Two locations are kept apart."""

    info = parse_metering_points(load_fixture("metering_point_two_locations.json"))
    assert [loc.id for loc in info.locations] == ["5001", "5002"]
    assert len(list(info.iter_activations())) == 5
    assert len(list(info.iter_activations("5002"))) == 1


@pytest.mark.parametrize(
    ("mutate", "rule"),
    [
        (
            lambda d: d[0]["memberMeteringpoints"][0].update(
                consumptionOrGeneration="Both"
            ),
            "S3",
        ),
        (lambda d: d[0]["memberMeteringpoints"][0].update(id="1001"), "S3"),
        (
            lambda d: d[0]["memberMeteringpoints"][0]["activations"][0].pop("begin"),
            "S2",
        ),
        (lambda d: d[0].pop("locationId"), "S2"),
        (lambda d: d[0].update(memberMeteringpoints={}), "S1"),
    ],
)
def test_metering_point_errors(mutate, rule: str) -> None:
    """Unknown directions and wrong types are schema errors."""

    raw = copy.deepcopy(load_fixture("metering_point_single.json"))
    mutate(raw)
    with pytest.raises(SchemaError) as excinfo:
        parse_metering_points(raw)
    assert excinfo.value.rule == rule


def test_session_drops_email() -> None:
    """Only member ids, the terms flag and the (unstored) label are kept."""

    info = parse_session(load_fixture("session_two_members.json"))
    assert info.member_ids() == ["9000001", "9000002"]
    assert info.members[1].must_accept_platform_terms is True
    assert info.members[0].display_name == "Example Member"
    assert "example.invalid" not in repr(info)
    assert "Example Member" not in repr(info)
    assert not hasattr(info, "email")
    one = parse_session(load_fixture("session_one_member.json"))
    assert one.member_ids() == ["9000001"]
    numeric = parse_session({"members": [{"memberId": 9000001}]})
    assert numeric.member_ids() == ["9000001"]
    with pytest.raises(SchemaError, match="S2"):
        parse_session({"email": "member@example.invalid"})


def test_maintenance() -> None:
    """Empty body → []; windows parse to UTC; activity check with margin."""

    assert parse_maintenance(load_fixture("maintenance_none.json")) == []
    (active,) = parse_maintenance(load_fixture("maintenance_active.json"))
    (future,) = parse_maintenance(load_fixture("maintenance_future.json"))
    now = datetime(2026, 6, 8, 2, tzinfo=UTC)
    assert active.active_at(now)
    assert not future.active_at(now)
    assert not active.active_at(datetime(2026, 6, 8, 3, 20, tzinfo=UTC))
    assert active.active_at(
        datetime(2026, 6, 8, 3, 20, tzinfo=UTC), margin_seconds=1800
    )
    alias = parse_maintenance(
        [{"startDate": "2026-06-08T01:00:00Z", "endDate": "2026-06-08T03:00:00Z"}]
    )
    assert alias[0].end == datetime(2026, 6, 8, 3, tzinfo=UTC)
    with pytest.raises(SchemaError, match="S2"):
        parse_maintenance([{"title": "x"}])
    with pytest.raises(SchemaError, match="S1"):
        parse_maintenance({})


def test_version() -> None:
    """Version keeps the full commit hash."""

    a = parse_version(load_fixture("version_a.json"))
    b = parse_version(load_fixture("version_b.json"))
    assert len(a.git_commit) == 40
    assert a.git_commit != b.git_commit
    with pytest.raises(SchemaError, match="gitCommit"):
        parse_version({"buildTime": "x"})


def test_metering_point_label_fields() -> None:
    """Full number, name, type and part factor for the flow labels (#19)."""

    info = parse_metering_points(load_fixture("metering_point_single.json"))
    cons, gen = info.locations[0].metering_points
    assert cons.number == "AT0000000000000000000000000001001"
    assert cons.name == "Example consumption"
    assert cons.source_of_production is None
    assert gen.source_of_production == "Sun"
    assert [a.active_part_factor for a in cons.activations] == [50.0, 50.0]
    # Label-only fields are sensitive: never in repr.
    assert cons.number not in repr(cons)
    assert "Example consumption" not in repr(cons)


def test_metering_point_label_fields_lenient() -> None:
    """Odd label values never fail validation: they are left out."""

    raw = copy.deepcopy(load_fixture("metering_point_single.json"))
    mp = raw[0]["memberMeteringpoints"][0]
    mp["name"] = 5
    mp["sourceOfProduction"] = ""
    mp["activations"][0]["activePartFactor"] = True
    mp["activations"][1]["activePartFactor"] = "50"
    del raw[0]["memberMeteringpoints"][1]["name"]
    info = parse_metering_points(raw)
    cons, gen = info.locations[0].metering_points
    assert cons.name is None
    assert cons.source_of_production is None
    assert [a.active_part_factor for a in cons.activations] == [None, None]
    assert gen.name is None
