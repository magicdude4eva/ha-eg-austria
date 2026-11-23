#!/usr/bin/env python3
"""Generate the synthetic test fixtures (spec 07 "Fixture rules").

Every value, id, name and text here is invented. The shapes follow the
post-update portal fingerprint (build ``20d1c18``, spec 01); keys that phase 1
ignores are filled with plausible noise so the parser sees them.

Run from the repository root to rewrite the committed JSON files:

    python3 tests/fixtures/generate.py

``tests/test_fixtures.py`` checks that the committed files equal the output.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
import copy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import json
import math
from pathlib import Path
import random
from typing import Any
from zoneinfo import ZoneInfo

VIENNA = ZoneInfo("Europe/Vienna")
HERE = Path(__file__).parent

# Synthetic ids (spec 07).
MEMBER = "9000001"
LOCATION = "5001"
MP_CONSUMPTION = 1001
MP_GENERATION = 1002
BEG = 2001
EEG = 2002
EMAIL = "member@example.invalid"
# Community shares: (share of generation to community, share of consumption
# from community). Invented constants.
SHARES = {BEG: (0.30, 0.20), EEG: (0.25, 0.10)}
PART_FACTOR = 50

Json = dict[str, Any]


def mp_number(mp_id: int) -> str:
    """Return a synthetic metering point number (``AT`` + 31 digits, operator 0)."""

    return "AT" + str(mp_id).rjust(31, "0")


def num(value: float) -> float | int:
    """Round to 3 decimals; whole values become JSON ints (as the portal sends)."""

    value = round(value, 3)
    return int(value) if value == int(value) else value


def iso_utc(moment: datetime) -> str:
    """Return ``YYYY-MM-DDTHH:MM:SS.sssZ``."""

    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def local_midnight(day: date) -> datetime:
    """Return local midnight of a day as an aware UTC datetime."""

    return datetime(day.year, day.month, day.day, tzinfo=VIENNA).astimezone(UTC)


def days(start: date, end: date) -> list[date]:
    """Return every date from start to end, both inclusive."""

    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def utc_hours(day: date) -> list[datetime]:
    """Return the UTC start of every local hour of a day (23/24/25)."""

    first = local_midnight(day)
    last = local_midnight(day + timedelta(days=1))
    count = int((last - first).total_seconds() // 3600)
    return [first + timedelta(hours=i) for i in range(count)]


def autumn_gap_hour(day: date) -> datetime | None:
    """Return the first local 02:00 hour on the autumn DST day (portal drops it)."""

    hours = utc_hours(day)
    if len(hours) != 25:
        return None
    return next(h for h in hours if h.astimezone(VIENNA).hour == 2)


# ---------------------------------------------------------------------------
# Deterministic energy model


@dataclass
class Scenario:
    """Knobs for one analysis-data response."""

    start: date
    end: date
    community: int  # the single community of the request
    aggregation: str = "Hour"
    community_incomplete: set[date] = field(default_factory=set)
    meter_missing: set[date] = field(default_factory=set)
    null_hours: set[datetime] = field(default_factory=set)  # meter values null
    seed: int = 7
    # Communities the metering points are members of (residual = meter minus
    # their parts); None = every community in SHARES.
    members: frozenset[int] | None = None


def hour_values(moment: datetime, rng_factor: float) -> tuple[float, float]:
    """Return (consumption, generation) in kWh for one hour."""

    local = moment.astimezone(VIENNA)
    h = local.hour + 0.5
    generation = 0.0
    if 7 <= h <= 17:
        generation = 3.0 * math.sin(math.pi * (h - 7) / 10) * rng_factor
    consumption = 0.4 + (0.6 if 18 <= local.hour <= 21 else 0.0)
    consumption += 0.05 * ((local.hour * 7) % 5)
    return round(consumption, 3), round(generation, 3)


def day_factor(day: date, seed: int) -> float:
    """Return the per-day weather factor from a seeded RNG."""

    # Deterministic test data, not crypto.
    return random.Random(f"{seed}-{day.isoformat()}").uniform(0.2, 1.0)  # noqa: S311


@dataclass
class Hour:
    """Values of one hour for one community request."""

    start: datetime
    consumption: float | None
    generation: float | None
    c_from: float | None
    g_to: float | None
    c_excess: float | None
    g_excess: float | None
    meter_faulty: bool
    community_faulty: bool
    complete: bool


def build_hour(sc: Scenario, moment: datetime) -> Hour:
    """Build one hour; meter and residual values are global, community local."""

    day = moment.astimezone(VIENNA).date()
    cons, gen = hour_values(moment, day_factor(day, sc.seed))
    if day in sc.meter_missing:
        return Hour(moment, 0, 0, 0, 0, 0, 0, True, True, False)
    if moment in sc.null_hours:
        return Hour(moment, None, None, None, None, None, None, False, False, False)
    if day in sc.community_incomplete:
        return Hour(moment, cons, gen, 0, 0, cons, gen, False, True, False)
    to_all = {c: round(gen * SHARES[c][0], 3) for c in SHARES}
    from_all = {c: round(cons * SHARES[c][1], 3) for c in SHARES}
    members = SHARES if sc.members is None else sc.members
    c_excess = round(cons - sum(v for c, v in from_all.items() if c in members), 3)
    g_excess = round(gen - sum(v for c, v in to_all.items() if c in members), 3)
    return Hour(
        moment,
        cons,
        gen,
        from_all[sc.community],
        to_all[sc.community],
        c_excess,
        g_excess,
        False,
        False,
        True,
    )


def _val(value: float | None) -> float | int | None:
    return None if value is None else num(value)


def _pct(part: float | None, whole: float | None) -> float | int | None:
    if not part or not whole:
        return None
    return num(100 * part / whole)


def _mix(part: float | None) -> Json | None:
    if not part:
        return None
    return {"sun": num(61.5), "wind": 20, "water": num(13.5), "other": 5}


def point_label(moment: datetime) -> tuple[str, str]:
    """Return (``date``, ``dateTimeFromLocal``) as the portal formats them."""

    local = moment.astimezone(VIENNA)
    return (
        local.strftime("%d.%m.%y %H:%M"),
        local.strftime("%Y-%m-%dT%H:%M:%S.000Z"),  # local wall time, fake "Z"
    )


def consumption_point(h: Hour) -> Json:
    """Return one consumptionDataPoints entry."""

    label, local = point_label(h.start)
    by_factor = None if h.consumption is None else h.consumption * PART_FACTOR / 100

    def comp(v: float | None) -> float | int | None:
        return _val(v) if h.complete else 0

    return {
        "dateTimeFrom": iso_utc(h.start),
        "dateTimeFromLocal": local,
        "date": label,
        "energyMix": _mix(h.c_from),
        "energyMixComplete": _mix(h.c_from) if h.complete else None,
        "consumption": _val(h.consumption),
        "consumptionByFactor": _val(by_factor),
        "consumptionFromCommunity": _val(h.c_from),
        "consumptionFromCommunityPercent": _pct(h.c_from, h.consumption),
        "consumptionInExcess": _val(h.c_excess),
        "actualConsumptionPartFactor": PART_FACTOR if h.consumption else None,
        "consumptionComplete": comp(h.consumption),
        "consumptionByFactorComplete": comp(by_factor),
        "consumptionFromCommunityComplete": comp(h.c_from),
        "consumptionInExcessComplete": comp(h.c_excess),
        "consumptionFromCommunityPercentComplete": None,
        "actualConsumptionPartFactorComplete": None,
        "consumptionFaulty": h.meter_faulty,
        "consumptionByFactorFaulty": h.meter_faulty,
        "consumptionFromCommunityFaulty": h.community_faulty,
        "spendings": num((h.c_from or 0) * 0.09),
        "spendingsComplete": num((h.c_from or 0) * 0.09) if h.complete else 0,
    }


def generation_point(h: Hour) -> Json:
    """Return one generationDataPoints entry."""

    label, local = point_label(h.start)
    by_factor = None if h.generation is None else h.generation * PART_FACTOR / 100

    def comp(v: float | None) -> float | int | None:
        return _val(v) if h.complete else 0

    return {
        "dateTimeFrom": iso_utc(h.start),
        "dateTimeFromLocal": local,
        "date": label,
        "generation": _val(h.generation),
        "generationByFactor": _val(by_factor),
        "generationToCommunity": _val(h.g_to),
        "generationToCommunityPercent": _pct(h.g_to, h.generation),
        "generationInExcess": _val(h.g_excess),
        "actualGenerationPartFactor": PART_FACTOR if h.generation else None,
        "generationComplete": comp(h.generation),
        "generationByFactorComplete": comp(by_factor),
        "generationToCommunityComplete": comp(h.g_to),
        "generationInExcessComplete": comp(h.g_excess),
        "generationToCommunityPercentComplete": None,
        "actualGenerationPartFactorComplete": None,
        "generationFaulty": h.meter_faulty,
        "generationByFactorFaulty": h.meter_faulty,
        "generationToCommunityFaulty": h.community_faulty,
        "earnings": num((h.g_to or 0) * 0.11),
        "earningsComplete": num((h.g_to or 0) * 0.11) if h.complete else 0,
    }


def _sum(points: list[Json], key: str) -> float | int | None:
    values = [p[key] for p in points if p[key] is not None]
    return num(sum(values)) if values else None


def merge_hours(hours: list[Hour], start: datetime) -> Hour:
    """Sum hours into one (Day aggregation)."""

    def add(attr: str) -> float | None:
        values = [getattr(h, attr) for h in hours if getattr(h, attr) is not None]
        return round(sum(values), 3) if values else None

    return Hour(
        start,
        add("consumption"),
        add("generation"),
        add("c_from"),
        add("g_to"),
        add("c_excess"),
        add("g_excess"),
        any(h.meter_faulty for h in hours),
        any(h.community_faulty for h in hours),
        all(h.complete for h in hours),
    )


def response(cons: list[Json], gen: list[Json]) -> Json:
    """Assemble the top level with totals from the points (newest first)."""

    cons = sorted(cons, key=lambda p: p["dateTimeFrom"], reverse=True)
    gen = sorted(gen, key=lambda p: p["dateTimeFrom"], reverse=True)
    if not cons and not gen:
        totals: dict[str, Any] = dict.fromkeys(TOTAL_KEYS)
        flags = dict.fromkeys(FLAG_KEYS, False)
        return {
            **totals,
            **flags,
            "consumptionDataPoints": [],
            "generationDataPoints": [],
        }
    out: Json = {
        "totalConsumption": _sum(cons, "consumption"),
        "totalConsumptionByFactor": _sum(cons, "consumptionByFactor"),
        "totalConsumptionFromCommunity": _sum(cons, "consumptionFromCommunity"),
        "totalConsumptionFromCommunityPercent": _pct(
            _sum(cons, "consumptionFromCommunity"), _sum(cons, "consumption")
        ),
        "totalConsumptionInExcess": _sum(cons, "consumptionInExcess"),
        "totalConsumptionInExcessByFactor": 0,
        "totalActualConsumptionPartFactor": PART_FACTOR,
        "totalGeneration": _sum(gen, "generation"),
        "totalGenerationByFactor": _sum(gen, "generationByFactor"),
        "totalGenerationToCommunity": _sum(gen, "generationToCommunity"),
        "totalGenerationToCommunityPercent": _pct(
            _sum(gen, "generationToCommunity"), _sum(gen, "generation")
        ),
        "totalGenerationInExcess": _sum(gen, "generationInExcess"),
        "totalGenerationInExcessByFactor": 0,
        "totalActualGenerationPartFactor": PART_FACTOR,
        "totalConsumptionComplete": _sum(cons, "consumptionComplete"),
        "totalConsumptionByFactorComplete": _sum(cons, "consumptionByFactorComplete"),
        "totalActualConsumptionPartFactorComplete": PART_FACTOR,
        "totalConsumptionFromCommunityComplete": _sum(
            cons, "consumptionFromCommunityComplete"
        ),
        "totalConsumptionInExcessComplete": _sum(cons, "consumptionInExcessComplete"),
        "totalConsumptionFromCommunityPercentComplete": None,
        "totalConsumptionEnergyMix": _mix(1),
        "totalConsumptionEnergyMixComplete": _mix(1),
        "totalGenerationComplete": _sum(gen, "generationComplete"),
        "totalGenerationByFactorComplete": _sum(gen, "generationByFactorComplete"),
        "totalActualGenerationPartFactorComplete": PART_FACTOR,
        "totalGenerationToCommunityComplete": _sum(
            gen, "generationToCommunityComplete"
        ),
        "totalGenerationInExcessComplete": _sum(gen, "generationInExcessComplete"),
        "totalGenerationToCommunityPercentComplete": None,
        "consumptionFaulty": any(p["consumptionFaulty"] for p in cons),
        "consumptionByFactorFaulty": any(p["consumptionByFactorFaulty"] for p in cons),
        "consumptionFromCommunityFaulty": any(
            p["consumptionFromCommunityFaulty"] for p in cons
        ),
        "consumptionInExcessFaulty": any(p["consumptionFaulty"] for p in cons),
        "consumptionInExcessByFactorFaulty": False,
        "generationFaulty": any(p["generationFaulty"] for p in gen),
        "generationByFactorFaulty": any(p["generationByFactorFaulty"] for p in gen),
        "generationToCommunityFaulty": any(
            p["generationToCommunityFaulty"] for p in gen
        ),
        "generationInExcessFaulty": any(p["generationFaulty"] for p in gen),
        "generationInExcessByFactorFaulty": False,
        "totalEarnings": _sum(gen, "earnings"),
        "totalSpendings": _sum(cons, "spendings"),
        "totalEarningsComplete": _sum(gen, "earningsComplete"),
        "totalSpendingsComplete": _sum(cons, "spendingsComplete"),
        "consumptionDataPoints": cons,
        "generationDataPoints": gen,
    }
    return out


TOTAL_KEYS = (
    "totalConsumption",
    "totalConsumptionFromCommunity",
    "totalConsumptionInExcess",
    "totalGeneration",
    "totalGenerationToCommunity",
    "totalGenerationInExcess",
)
FLAG_KEYS = (
    "consumptionFaulty",
    "consumptionFromCommunityFaulty",
    "consumptionInExcessFaulty",
    "generationFaulty",
    "generationToCommunityFaulty",
    "generationInExcessFaulty",
)


def analysis(sc: Scenario) -> Json:
    """Build an analysis-data response for a scenario."""

    cons: list[Json] = []
    gen: list[Json] = []
    for day in days(sc.start, sc.end):
        hours = [build_hour(sc, m) for m in utc_hours(day)]
        if sc.aggregation == "Day":
            merged = merge_hours(hours, local_midnight(day))
            cons.append(consumption_point(merged))
            gen.append(generation_point(merged))
            continue
        gap = autumn_gap_hour(day)
        for h in hours:
            if h.start == gap:
                continue
            if gap is not None and h.start == gap - timedelta(hours=1):
                # The hour before the missing one is flagged (G1).
                h.meter_faulty = True
            cons.append(consumption_point(h))
            gen.append(generation_point(h))
    return response(cons, gen)


def completeness(
    start: date,
    end: date,
    incomplete: Iterable[date] = (),
    future_from: date | None = None,
) -> list[Json]:
    """Build a day-completeness list (ascending, both ends inclusive)."""

    incomplete = set(incomplete)
    out = []
    for day in days(start, end):
        if future_from is not None and day >= future_from:
            flag: bool | None = None
        else:
            flag = day not in incomplete
        out.append(
            {
                "date": day.isoformat(),
                "consumptionComplete": flag,
                "generationComplete": flag,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Static responses


def activation(community: int, begin: str, end: str | None = None) -> Json:
    """Return one activation entry (``end``: last active day, None = open)."""

    name, kind = {BEG: ("Example BEG", "CC"), EEG: ("Example EEG", "RC_R")}[community]
    return {
        "energyCommunityId": community,
        "energyCommunityName": name,
        "energyCommunityType": kind,
        "status": "Active",
        "anforderungMsgDate": "2026-01-01",
        "desiredBegin": None,
        "begin": begin,
        "end": end,
        "activePartFactor": PART_FACTOR,
        "pendingPartFactor": PART_FACTOR,
        "confirmedPartFactor": PART_FACTOR,
        "isManualPartFactor": False,
    }


def metering_point(mp_id: int, direction: str, location: str, acts: list[Json]) -> Json:
    """Return one memberMeteringpoints entry."""

    generation = direction == "Generation"
    zero_plan = {
        "id": 7000 + mp_id,
        "fromName": "Example sender",
        "toName": "Example receiver",
        "fromMeteringpointNumber": mp_number(mp_id),
        "toMeteringpointNumber": mp_number(9999),
        "begin": "2026-01-01",
        "end": None,
        "status": "Active",
        "isAutoGenerated": True,
    }
    return {
        "id": mp_id,
        "number": mp_number(mp_id),
        "locationId": location,
        "name": f"Example {direction.lower()}",
        "nameAddition": None,
        "installationNumber": None,
        "ecNumber": "EC000000000000000000000000000001",
        "networkOperator": "Example grid operator",
        "consumptionOrGeneration": direction,
        "sourceOfProduction": "Sun" if generation else None,
        "statedPeakFeedIn": 9.5 if generation else None,
        "statedYearlyConsumption": None if generation else 3200,
        "commercialUse": False,
        "isManualPartFactor": False,
        "desiredPartFactorParticipatingCommunityIds": [
            a["energyCommunityId"] for a in acts
        ],
        "activations": acts,
        "sendingZeroPlans": [zero_plan] if generation else [],
        "receivingZeroPlans": [] if generation else [zero_plan],
    }


def location(loc_id: str, street: str, number: str, mps: list[Json]) -> Json:
    """Return one location entry."""

    return {
        "locationId": loc_id,
        "memberId": MEMBER,
        "address": {
            "postalCode": "9999",
            "city": "Example town",
            "street": street,
            "houseNumber": number,
            "stairsFloorDoor": None,
            "createdAt": "2026-01-01T08:00:00.000Z",
            "updatedAt": "2026-01-01T08:00:00.000Z",
        },
        "memberMeteringpoints": mps,
        "substationName": {
            "id": 3001,
            "name": "Example substation",
            "ecNumber": "EC000000000000000000000000000001",
            "substation": "Example substation 1",
            "meteringPointRange": "Example range",
            "consumerCount": 10,
            "prosumerCount": 5,
            "producerCount": 2,
        },
    }


def metering_point_single() -> list[Json]:
    """One location, consumption + generation, both communities."""

    acts = [activation(BEG, "2026-01-01"), activation(EEG, "2026-02-01")]
    return [
        location(
            LOCATION,
            "Example street",
            "1",
            [
                metering_point(
                    MP_CONSUMPTION, "Consumption", LOCATION, copy.deepcopy(acts)
                ),
                metering_point(
                    MP_GENERATION, "Generation", LOCATION, copy.deepcopy(acts)
                ),
            ],
        )
    ]


def metering_point_two_locations() -> list[Json]:
    """Two locations; the second has one consumption point in the BEG only."""

    second = location(
        "5002",
        "Example lane",
        "2",
        [metering_point(1003, "Consumption", "5002", [activation(BEG, "2026-03-01")])],
    )
    return [*metering_point_single(), second]


def error_body(status: int, error: str, title: str, detail: str, trace: str) -> Json:
    """Return an RFC 7807-like error body with synthetic instance/traceId."""

    return {
        "type": "about:blank",
        "title": title,
        "status": status,
        "error": error,
        "detail": detail,
        "instance": f"/api/v1/portal/member/{MEMBER}/analysis-data?energyCommunityIds={BEG}&mpIds={MP_CONSUMPTION}",
        "traceId": trace,
        "statusCode": status,
        "message": detail,
    }


# ---------------------------------------------------------------------------
# Scenarios

NORMAL = (date(2026, 6, 1), date(2026, 6, 7))
TAIL = (date(2026, 5, 1), date(2026, 5, 31))
AUTUMN = date(2026, 10, 25)
SPRING = date(2026, 3, 29)
EMPTY_DAY = date(2026, 12, 1)
ONE_DAY = date(2026, 6, 3)


def _mutate(data: Json, fn: Callable[[Json], None]) -> Json:
    data = copy.deepcopy(data)
    fn(data)
    return data


def _newest_first_index(data: Json, list_key: str, local_hour: int) -> int:
    for i, p in enumerate(data[list_key]):
        if (
            datetime.fromisoformat(p["dateTimeFrom"]).astimezone(VIENNA).hour
            == local_hour
        ):
            return i
    raise KeyError(local_hour)


def bad_fixtures() -> dict[str, Any]:
    """One fixture per shape (S*) and plausibility (P*, H*) rule."""

    base = analysis(Scenario(ONE_DAY, ONE_DAY, BEG))
    noon = _newest_first_index(base, "generationDataPoints", 12)
    evening = _newest_first_index(base, "consumptionDataPoints", 19)

    def set_point(list_key: str, idx: int, **values: Any) -> Callable[[Json], None]:
        def fn(d: Json) -> None:
            d[list_key][idx].update(values)

        return fn

    out: dict[str, Any] = {
        # Stage 1: shape.
        "analysis_bad_s1_not_object.json": [base],
        "analysis_bad_s2_missing_key.json": _mutate(
            base, lambda d: d["consumptionDataPoints"][0].pop("consumptionFaulty")
        ),
        "analysis_bad_s2_renamed_key.json": _mutate(
            base,
            lambda d: d["generationDataPoints"][0].update(
                {"generationValue": d["generationDataPoints"][0].pop("generation")}
            ),
        ),
        "analysis_bad_s3_string_value.json": _mutate(
            base, set_point("consumptionDataPoints", evening, consumption="1,234")
        ),
        "analysis_bad_s3_bool_flag_as_number.json": _mutate(
            base, set_point("consumptionDataPoints", 0, consumptionFaulty=0)
        ),
        "analysis_bad_s4_naive_timestamp.json": _mutate(
            base,
            lambda d: d["consumptionDataPoints"][0].update(
                {"dateTimeFrom": d["consumptionDataPoints"][0]["dateTimeFrom"][:-5]}
            ),
        ),
        "analysis_bad_s5_not_whole_hour.json": _mutate(
            base,
            lambda d: d["generationDataPoints"][0].update(
                {
                    "dateTimeFrom": d["generationDataPoints"][0]["dateTimeFrom"][:14]
                    + "15:00.000Z"
                }
            ),
        ),
        "analysis_bad_s6_duplicate.json": _mutate(
            base,
            lambda d: d["consumptionDataPoints"][1].update(
                {"dateTimeFrom": d["consumptionDataPoints"][0]["dateTimeFrom"]}
            ),
        ),
        "analysis_bad_s7_out_of_range.json": _mutate(
            base,
            lambda d: d["consumptionDataPoints"][0].update(
                {"dateTimeFrom": iso_utc(local_midnight(ONE_DAY + timedelta(days=1)))}
            ),
        ),
        # Stage 2: plausibility.
        "analysis_bad_p1_negative.json": _mutate(
            base, set_point("generationDataPoints", noon, generationInExcess=-0.25)
        ),
        "analysis_bad_p2_tenfold.json": _mutate(
            base,
            lambda d: [
                p.update(
                    {
                        k: num(p[k] * 1000)
                        for k in (
                            "generation",
                            "generationToCommunity",
                            "generationInExcess",
                        )
                        if p[k]
                    }
                )
                for p in d["generationDataPoints"]
            ],
        ),
        "analysis_bad_p3_community_gt_meter.json": _mutate(
            base,
            set_point("consumptionDataPoints", evening, consumptionFromCommunity=5),
        ),
        "analysis_bad_p5_totals_mismatch.json": _mutate(
            base, lambda d: d.update({"totalGeneration": num(d["totalGeneration"] + 3)})
        ),
    }

    # P4 and P6 need both community responses of a chunk.
    beg = base
    eeg = analysis(Scenario(ONE_DAY, ONE_DAY, EEG))

    def shift_excess(d: Json) -> None:
        p = d["generationDataPoints"][noon]
        p["generationInExcess"] = num(p["generationInExcess"] + 0.5)

    out["analysis_bad_p4_sum_beg.json"] = _mutate(beg, shift_excess)
    out["analysis_bad_p4_sum_eeg.json"] = _mutate(eeg, shift_excess)
    out["analysis_bad_p6_differs_beg.json"] = beg
    out["analysis_bad_p6_differs_eeg.json"] = _mutate(
        eeg,
        set_point(
            "consumptionDataPoints",
            evening,
            consumption=num(eeg["consumptionDataPoints"][evening]["consumption"] + 0.4),
        ),
    )

    # H1: a complete day that now arrives with every hour 0.
    def zero(d: Json) -> None:
        for p in d["consumptionDataPoints"] + d["generationDataPoints"]:
            for k, v in p.items():
                if isinstance(v, int | float) and not isinstance(v, bool):
                    p[k] = 0
        for k, v in d.items():
            if k.startswith("total") and isinstance(v, int | float):
                d[k] = 0

    out["analysis_bad_h1_all_zero.json"] = _mutate(base, zero)
    return out


def all_fixtures() -> dict[str, Any]:
    """Return every committed fixture as {file name: JSON value or b"" body}."""

    fx: dict[str, Any] = {
        "analysis_hour_normal_beg.json": analysis(Scenario(*NORMAL, BEG)),
        "analysis_hour_normal_eeg.json": analysis(Scenario(*NORMAL, EEG)),
        "analysis_hour_tail_incomplete.json": analysis(
            Scenario(
                *TAIL,
                BEG,
                community_incomplete=set(days(TAIL[1] - timedelta(days=13), TAIL[1])),
                meter_missing={TAIL[1]},
            )
        ),
        "analysis_hour_dst_autumn.json": analysis(Scenario(AUTUMN, AUTUMN, BEG)),
        "analysis_day_dst_autumn.json": analysis(
            Scenario(AUTUMN, AUTUMN, BEG, aggregation="Day")
        ),
        "analysis_hour_dst_spring.json": analysis(Scenario(SPRING, SPRING, BEG)),
        "analysis_empty_day.json": response([], []),
        # Issue #78 / forum 1140: the real portal leaves out null total keys
        # and sends the percent / part-factor ones as null.
        "analysis_empty_day_portal.json": {
            **dict.fromkeys(
                (
                    "totalConsumptionFromCommunityPercent",
                    "totalActualConsumptionPartFactor",
                    "totalGenerationToCommunityPercent",
                    "totalActualGenerationPartFactor",
                    "totalActualConsumptionPartFactorComplete",
                    "totalConsumptionFromCommunityPercentComplete",
                    "totalActualGenerationPartFactorComplete",
                    "totalGenerationToCommunityPercentComplete",
                )
            ),
            **{
                f"{side}{part}Faulty": False
                for side in ("consumption", "generation")
                for part in (
                    "",
                    "ByFactor",
                    "FromCommunity" if side == "consumption" else "ToCommunity",
                    "InExcess",
                    "InExcessByFactor",
                )
            },
            "consumptionDataPoints": [],
            "generationDataPoints": [],
        },
        "analysis_hour_null_hours.json": analysis(
            Scenario(
                ONE_DAY,
                ONE_DAY,
                BEG,
                null_hours={
                    local_midnight(ONE_DAY) + timedelta(hours=h) for h in (3, 4)
                },
            )
        ),
        "analysis_hour_noise.json": _mutate(
            analysis(Scenario(ONE_DAY, ONE_DAY, BEG)),
            lambda d: (
                d.update({"exampleNewTopLevelKey": {"nested": True}}),
                d["consumptionDataPoints"][0].update(
                    {
                        "date": "Mittwoch, 3. Juni 2026 um 23 Uhr",
                        "exampleNewPointKey": 1,
                    }
                ),
            ),
        ),
        "day_completeness_normal.json": completeness(*NORMAL),
        "day_completeness_tail_incomplete.json": completeness(
            *TAIL, incomplete=days(TAIL[1] - timedelta(days=13), TAIL[1])
        ),
        "day_completeness_future.json": completeness(
            date(2026, 6, 1),
            date(2026, 6, 10),
            incomplete=[date(2026, 6, 7)],
            future_from=date(2026, 6, 8),
        ),
        "day_completeness_bad_duplicate.json": _mutate(
            {"x": completeness(*NORMAL)},
            lambda d: d["x"].__setitem__(3, dict(d["x"][2])),
        )["x"],
        "day_completeness_bad_date.json": _mutate(
            {"x": completeness(*NORMAL)},
            lambda d: d["x"][0].update({"date": "01.06.2026"}),
        )["x"],
        "day_completeness_bad_numeric.json": _mutate(
            {"x": completeness(*NORMAL)},
            lambda d: d["x"][0].update({"consumptionComplete": 1}),
        )["x"],
        "metering_point_single.json": metering_point_single(),
        "metering_point_two_locations.json": metering_point_two_locations(),
        "maintenance_none.json": b"",  # 200 with an empty body (G1)
        "maintenance_active.json": [
            {
                "id": 1,
                "start": "2026-06-08T01:00:00.000Z",
                "end": "2026-06-08T03:00:00.000Z",
                "title": "Example maintenance",
            }
        ],
        "maintenance_future.json": [
            {
                "id": 2,
                "start": "2026-06-20T01:00:00.000Z",
                "end": "2026-06-20T03:00:00.000Z",
                "title": "Example maintenance",
            }
        ],
        "version_a.json": {"gitCommit": "a" * 40, "buildTime": "2026-05-01T10:00:00Z"},
        "version_b.json": {"gitCommit": "b" * 40, "buildTime": "2026-06-05T10:00:00Z"},
        "session_one_member.json": {
            "email": EMAIL,
            "members": [
                {
                    "memberId": MEMBER,
                    "displayName": "Example Member",
                    "mustAcceptPlatformTerms": False,
                }
            ],
        },
        "session_two_members.json": {
            "email": EMAIL,
            "members": [
                {
                    "memberId": MEMBER,
                    "displayName": "Example Member",
                    "mustAcceptPlatformTerms": False,
                },
                {
                    "memberId": "9000002",
                    "displayName": "Example Company",
                    "mustAcceptPlatformTerms": True,
                },
            ],
        },
        "error_400_stale_client.json": error_body(
            400,
            "Bad Request",
            "Ungültige Anfrage",
            "Das Portal wurde aktualisiert. Bitte laden Sie die Seite neu.",
            "0" * 31 + "1",
        ),
        "error_400_other.json": error_body(
            400,
            "Bad Request",
            "Ungültige Anfrage",
            "Validation failed (enum string is expected)",
            "0" * 31 + "2",
        ),
        "error_401.json": error_body(
            401,
            "Unauthorized",
            "Nicht autorisiert",
            "Access token revoked",
            "0" * 31 + "3",
        ),
    }
    fx.update(bad_fixtures())
    return fx


def render(value: Any) -> bytes:
    """Serialise a fixture deterministically (bytes are written as they are)."""

    if isinstance(value, bytes):
        return value
    return (_pretty(value, 0) + "\n").encode("utf-8")


def _pretty(value: Any, depth: int) -> str:
    """Two levels of indentation, then one compact line per point or entry."""

    def compact(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False, separators=(", ", ": "))

    if depth >= 2 or not isinstance(value, dict | list) or not value:
        return compact(value)
    pad = " " * (depth + 1)
    if isinstance(value, dict):
        items = [
            f"{pad}{json.dumps(k)}: {_pretty(v, depth + 1)}" for k, v in value.items()
        ]
        return "{\n" + ",\n".join(items) + "\n" + " " * depth + "}"
    items = [f"{pad}{_pretty(v, depth + 1)}" for v in value]
    return "[\n" + ",\n".join(items) + "\n" + " " * depth + "]"


def main(argv: list[str] | None = None) -> int:
    """Write every fixture into the target directory."""

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=HERE)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, value in sorted(all_fixtures().items()):
        (args.out / name).write_bytes(render(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
