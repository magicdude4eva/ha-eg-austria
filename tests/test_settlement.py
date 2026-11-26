"""Settlement from ``billing/transactions`` (issue #74, ADR 0008).

All rows are synthetic; their shape follows the portal (a list, newest
first, Austrian German month names, reversals with negated amounts).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import math
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest

from custom_components.eg_austria.coordinator import RunRequest
from custom_components.eg_austria.exceptions import (
    ForbiddenError,
    SchemaError,
)
from custom_components.eg_austria.importer import RunStatus, RunType
from custom_components.eg_austria.models import (
    Activation,
    Direction,
    Location,
    MeteringPoint,
    MeteringPointInfo,
    Transaction,
    parse_analysis,
    parse_transactions,
)
from custom_components.eg_austria.settlement import (
    MonthFactor,
    MonthState,
    MonthTotal,
    Settlements,
    apply_settlements,
    billed_month,
    build_settlements,
    changed_months,
    diagnostics,
    mark_other_points,
    month_factors,
    settled_months,
    settled_window_start,
    shared_requests,
)
from custom_components.eg_austria.statistics import local_date

from .conftest import load_fixture
from .test_coordinator import NOW, make, stored

NAMES = {"beg": "Example BEG", "eeg": "Example EEG"}
C, G = Direction.CONSUMPTION, Direction.GENERATION


def tx(when: str, amount: float, description: str, community: str | None) -> Any:
    """One raw transaction row as the portal sends it."""

    return {
        "id": abs(hash((when, amount, description))) % 10_000,
        "date": f"{when}T00:00:00.000Z",
        "amount": amount,
        "preview": False,
        "description": description,
        "document": {
            "id": 1,
            "name": "Rechnung.pdf",
            "fileName": "Rechnung_Example_Member.pdf",
            "fileSize": 1,
            "mimeType": "application/pdf",
        },
        "energyCommunityName": f"Long name of {community}" if community else None,
        "energyCommunityShortName": community,
        "invoiceNumber": "R-0001",
        "payoutStatus": None,
        "deniedReason": None,
        "invoiceStatus": None,
        "depositStatus": None,
    }


def rows(*items: Any) -> list[Transaction]:
    """Parse raw rows (given oldest first) in the portal's newest-first order."""

    return parse_transactions(list(reversed(items)))


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before ``hass`` (see test_statistics.py)."""


@pytest.fixture
async def env(
    hass: HomeAssistant, recorder_mock: Any, freezer: FrozenDateTimeFactory
) -> FrozenDateTimeFactory:
    """Frozen time in Vienna with a recorder."""

    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Vienna")
    return freezer


# ---------------------------------------------------------------------------
# Parsing


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Einspeisevergütung Jänner 2026", ("einspeisevergütung", (2026, 1))),
        ("Strombezug März 2026", ("strombezug", (2026, 3))),
        (
            "Strombezug August 2026 (Zero-Tarif-Korrektur folgt)",
            ("strombezug", (2026, 8)),
        ),
        (
            "Energierechnungskorrektur August 2026",
            ("energierechnungskorrektur", (2026, 8)),
        ),
        ("Mitgliedsbeitrag 2026", None),
        ("Einzahlung", None),
        ("Strombezug Smarch 2026", None),
    ],
)
def test_billed_month(text: str, expected: Any) -> None:
    """Austrian German months, the Zero note, and rows that aren't energy."""

    assert billed_month(text) == expected


def test_parse_transactions_drops_document_and_checks_shape() -> None:
    """Only date, amount, description and community name are kept."""

    parsed = parse_transactions(
        [tx("2026-07-16", 1.5, "Einspeisevergütung Mai 2026", "Example BEG")]
    )
    assert parsed[0].date == date(2026, 7, 16)
    assert parsed[0].community_name == "Example BEG"
    assert "Example_Member" not in repr(parsed)
    assert not hasattr(parsed[0], "document")
    bad = tx("2026-07-16", 1.5, "x", None)
    del bad["amount"]
    with pytest.raises(SchemaError) as err:
        parse_transactions([bad])
    assert err.value.rule == "S2"
    with pytest.raises(SchemaError):
        parse_transactions({"items": []})


# ---------------------------------------------------------------------------
# Grouping


def test_reversal_and_rebill_net_to_the_rebill() -> None:
    """Billed, reversed by negated amounts, rebilled: the rebill counts."""

    result = build_settlements(
        rows(
            tx(
                "2026-09-20",
                12.50,
                "Einspeisevergütung August 2026 (Zero-Tarif-Korrektur folgt)",
                "Example BEG",
            ),
            tx(
                "2026-09-20",
                -3.40,
                "Strombezug August 2026 (Zero-Tarif-Korrektur folgt)",
                "Example BEG",
            ),
            tx(
                "2026-10-15",
                -12.50,
                "Energierechnungskorrektur August 2026",
                "Example BEG",
            ),
            tx(
                "2026-10-15",
                3.40,
                "Energierechnungskorrektur August 2026",
                "Example BEG",
            ),
            tx("2026-11-05", 10.80, "Einspeisevergütung August 2026", "Example BEG"),
            tx("2026-11-05", -0.90, "Strombezug August 2026", "Example BEG"),
        ),
        NAMES,
    )
    assert result.settled(("beg", G, (2026, 8))) == pytest.approx(10.80)
    # Consumption: the charged amount becomes a positive cost.
    assert result.settled(("beg", C, (2026, 8))) == pytest.approx(0.90)
    assert not result.unmatched


def test_reversed_direction_is_unsettled_until_rebilled() -> None:
    """Reversed, not billed again yet: provisional, not settled with 0 (#103)."""

    feed_in = tx("2026-09-20", 12.50, "Einspeisevergütung August 2026", "Example BEG")
    drawn = tx("2026-09-20", -3.40, "Strombezug August 2026", "Example BEG")
    undo_feed_in = tx(
        "2026-10-15", -12.50, "Energierechnungskorrektur August 2026", "Example BEG"
    )
    undo_drawn = tx(
        "2026-10-15", 3.40, "Energierechnungskorrektur August 2026", "Example BEG"
    )
    billed = build_settlements(rows(feed_in, drawn), NAMES)

    both = build_settlements(rows(feed_in, drawn, undo_feed_in, undo_drawn), NAMES)
    assert both.amounts == {}
    assert not both.unmatched
    assert settled_months(both, {}) == {}
    # The vanished settlement is a change, so the month is fetched again.
    assert changed_months(billed.digests(), both.digests()) == {(2026, 8)}

    one = build_settlements(rows(feed_in, drawn, undo_drawn), NAMES)
    assert one.settled(("beg", G, (2026, 8))) == pytest.approx(12.50)
    assert one.settled(("beg", C, (2026, 8))) is None


def test_rebill_with_the_same_amount_is_settled() -> None:
    """One of two equal invoices reversed: the other one stays."""

    invoice = "Einspeisevergütung August 2026"
    correction = "Energierechnungskorrektur August 2026"
    result = build_settlements(
        rows(
            tx("2026-09-20", 12.50, invoice, "Example BEG"),
            tx("2026-10-15", -12.50, correction, "Example BEG"),
            tx("2026-11-05", 12.50, invoice, "Example BEG"),
        ),
        NAMES,
    )
    assert result.settled(("beg", G, (2026, 8))) == pytest.approx(12.50)
    assert not result.unmatched


def test_zero_correction_is_ignored() -> None:
    """0.00 invoices both ways and a 0.00 correction: still settled (#103)."""

    result = build_settlements(
        rows(
            tx("2026-09-20", 0, "Einspeisevergütung August 2026", "Example BEG"),
            tx("2026-09-20", 0, "Strombezug August 2026", "Example BEG"),
            tx("2026-10-15", 0, "Energierechnungskorrektur August 2026", "Example BEG"),
        ),
        NAMES,
    )
    assert result.settled(("beg", G, (2026, 8))) == 0
    assert result.settled(("beg", C, (2026, 8))) == 0
    assert not result.unmatched


def test_unmatched_correction_leaves_month_unsettled() -> None:
    """A correction that negates no row: both directions stay provisional."""

    result = build_settlements(
        rows(
            tx("2026-04-29", 6.40, "Einspeisevergütung Jänner 2026", "Example BEG"),
            tx("2026-04-29", -2.70, "Strombezug Jänner 2026", "Example BEG"),
            tx(
                "2026-05-10",
                0.55,
                "Energierechnungskorrektur Jänner 2026",
                "Example BEG",
            ),
        ),
        NAMES,
    )
    assert result.settled(("beg", G, (2026, 1))) is None
    assert result.settled(("beg", C, (2026, 1))) is None
    assert ("beg", (2026, 1)) in result.unmatched


def test_zero_invoice_other_rows_and_unknown_community() -> None:
    """A 0 invoice is a settlement; fees, deposits, other communities ignored."""

    result = build_settlements(
        rows(
            tx("2026-11-17", 0, "Strombezug Oktober 2026", "Example EEG"),
            tx("2026-11-17", 0.61, "Einspeisevergütung Oktober 2026", "Example EEG"),
            tx("2026-01-05", -25.0, "Mitgliedsbeitrag 2026", None),
            tx("2026-01-06", 25.0, "Einzahlung", None),
            tx("2026-11-17", 3.0, "Einspeisevergütung Oktober 2026", "Other EEG"),
            tx("2026-11-17", 1.0, "Bonuspunkte Oktober 2026", "Example EEG"),
        ),
        NAMES,
    )
    assert result.settled(("eeg", C, (2026, 10))) == 0
    assert result.settled(("eeg", G, (2026, 10))) == pytest.approx(0.61)
    assert result.ignored_rows == 3
    assert result.unknown_rows == 1
    assert {k[0] for k in result.amounts} == {"eeg"}


def test_names_match_ignoring_case_and_spaces() -> None:
    """``energyCommunityShortName`` vs the stored name: whitespace and case."""

    result = build_settlements(
        rows(
            tx("2026-07-23", 3.30, "Einspeisevergütung April 2026", "  example   beg ")
        ),
        NAMES,
    )
    assert result.settled(("beg", G, (2026, 4))) == pytest.approx(3.30)


def test_digests_and_changed_months() -> None:
    """Digests are stable, value-free and show which months changed."""

    first = build_settlements(
        rows(tx("2026-07-23", 3.30, "Einspeisevergütung April 2026", "Example BEG")),
        NAMES,
    ).digests()
    again = build_settlements(
        rows(tx("2026-07-23", 3.30, "Einspeisevergütung April 2026", "Example BEG")),
        NAMES,
    ).digests()
    assert first == again
    assert "3.30" not in str(first)
    assert "401" not in str(first)
    rebilled = build_settlements(
        rows(
            tx("2026-07-23", 3.30, "Einspeisevergütung April 2026", "Example BEG"),
            tx("2026-07-23", 2.00, "Einspeisevergütung Mai 2026", "Example BEG"),
        ),
        NAMES,
    ).digests()
    assert changed_months(first, rebilled) == {(2026, 5)}
    assert changed_months({}, first) == {(2026, 4)}


# ---------------------------------------------------------------------------
# Scaling


def _june() -> Any:
    return parse_analysis(load_fixture("analysis_hour_normal_beg.json"))


def _month_total(data: Any, direction: Direction, month: tuple[int, int]) -> float:
    return math.fsum(
        p.money
        for p in data.points(direction)
        if p.money is not None
        and (local_date(p.start).year, local_date(p.start).month) == month
    )


def test_settled_month_scaled_to_the_invoice() -> None:
    """Month totals equal the settlement; unsettled months stay as they are."""

    data = _june()
    months = {
        (local_date(p.start).year, local_date(p.start).month) for p in data.consumption
    }
    month = max(months)
    provisional = _month_total(data, C, month)
    assert provisional > 0
    invoice = round(provisional * 0.5, 2)
    settlements = build_settlements(
        rows(tx("2026-07-20", -invoice, f"Strombezug Juni {month[0]}", "Example BEG")),
        NAMES,
    )
    factors = month_factors({"beg": [data]}, settlements)
    assert factors[("beg", C, month)].state is MonthState.SETTLED
    assert factors[("beg", C, month)].factor == pytest.approx(invoice / provisional)
    scaled = apply_settlements({"beg": [data]}, factors)["beg"][0]
    assert _month_total(scaled, C, month) == pytest.approx(invoice)
    # Generation of the same month has no settlement: unchanged.
    assert _month_total(scaled, G, month) == pytest.approx(_month_total(data, G, month))
    # kWh are never touched.
    assert [p.community for p in scaled.consumption] == [
        p.community for p in data.consumption
    ]


def test_zero_provisional_month_not_scaled() -> None:
    """Nothing to scale: the month stays provisional and is flagged."""

    raw = load_fixture("analysis_hour_normal_beg.json")
    for point in raw["generationDataPoints"]:
        point["earnings"] = 0
    data = parse_analysis(raw)
    month = (
        local_date(data.generation[-1].start).year,
        local_date(data.generation[-1].start).month,
    )
    settlements = build_settlements(
        rows(
            tx("2026-07-20", 5.0, f"Einspeisevergütung Juni {month[0]}", "Example BEG")
        ),
        NAMES,
    )
    factors = month_factors({"beg": [data]}, settlements)
    assert factors[("beg", G, month)].state is MonthState.ZERO_PROVISIONAL
    assert apply_settlements({"beg": [data]}, factors)["beg"][0] == data


def test_window_start_whole_months_and_changed_months() -> None:
    """Partly covered settled months start on the 1st; changed months too."""

    settlements = build_settlements(
        rows(tx("2026-06-01", 1.0, "Einspeisevergütung April 2026", "Example BEG")),
        NAMES,
    )
    start, end = date(2026, 4, 7), date(2026, 6, 8)
    assert settled_window_start(start, end, settlements, ()) == date(2026, 4, 1)
    assert settled_window_start(start, end, settlements, {(2026, 2)}) == date(
        2026, 2, 1
    )
    assert settled_window_start(
        start, end, settlements, {(2026, 2)}, floor=date(2026, 3, 1)
    ) == date(2026, 3, 1)
    # A settled month entirely before the window doesn't move it.
    old = build_settlements(
        rows(tx("2026-03-01", 1.0, "Einspeisevergütung Jänner 2026", "Example BEG")),
        NAMES,
    )
    assert settled_window_start(start, end, old, ()) == start


# ---------------------------------------------------------------------------
# A run


async def _month_sum(hass: HomeAssistant, key: str, month: tuple[int, int]) -> float:
    total = 0.0
    for row in await stored(hass, key):
        hour = datetime.fromtimestamp(row["start"], UTC)
        day = local_date(hour)
        if (day.year, day.month) == month and row["state"] is not None:
            total += row["state"]
    return total


async def test_run_writes_settled_month(hass: HomeAssistant, env: Any) -> None:
    """The written month equals the invoice; the site sum follows; digests kept."""

    # A short daily window keeps the recorder work small; the new June
    # settlement pulls the window back to 1 June.
    coordinator, _, client = make(hass, options={"daily_window_days": 7})
    client.transactions_raw = list(
        reversed(
            [
                tx("2026-06-08", -1.23, "Strombezug Juni 2026", "Example BEG"),
                tx("2026-06-08", 0, "Strombezug Juni 2026", "Example EEG"),
            ]
        )
    )
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.OK
    assert summary.window == (date(2026, 6, 1), date(2026, 6, 8))
    beg = await _month_sum(hass, "home_beg_consumption_eur", (2026, 6))
    eeg = await _month_sum(hass, "home_eeg_consumption_eur", (2026, 6))
    site = await _month_sum(hass, "home_consumption_eur", (2026, 6))
    assert beg == pytest.approx(1.23, abs=1e-4)
    assert eeg == pytest.approx(0, abs=1e-6)
    assert site == pytest.approx(beg + eeg, abs=1e-4)
    # Feed-in of the same month stays provisional.
    assert await _month_sum(hass, "home_beg_feed_in_eur", (2026, 6)) > 0
    assert summary.settlement is not None
    months = summary.settlement["months"]
    assert months["beg/Consumption/2026-06"]["state"] == "settled"
    assert months["eeg/Consumption/2026-06"]["factor"] == 0
    assert "beg/Generation/2026-06" not in months
    assert coordinator.state.settlement_digests
    # Issue #75: kept for the settled / provisional marker.
    assert coordinator.state.settled_months == {
        "beg/Consumption": ["2026-06"],
        "eeg/Consumption": ["2026-06"],
    }
    assert ("get_transactions", ("9000001",)) in client.calls


async def test_changed_settlement_refetches_its_month(
    hass: HomeAssistant, env: Any
) -> None:
    """A month settled (or rebilled) before the window is fetched again."""

    coordinator, _, client = make(hass, options={"daily_window_days": 7})
    client.transactions_raw = [
        tx("2026-04-20", 1.0, "Einspeisevergütung Februar 2026", "Example BEG")
    ]
    # Only the window matters here: skip the statistics write.
    with patch(
        "custom_components.eg_austria.importer.async_import_window",
        AsyncMock(return_value={}),
    ):
        summary = await coordinator.async_run(RunRequest(RunType.DAILY))
        assert summary is not None
        assert summary.window is not None
        assert summary.window[0] == date(2026, 2, 1)
        # Same settlement on the next run: back to the normal (short) window.
        summary = await coordinator.async_run(RunRequest(RunType.DAILY))
        assert summary is not None
        assert summary.window is not None
        assert summary.window[0] > date(2026, 5, 1)


async def test_transactions_schema_change_aborts(hass: HomeAssistant, env: Any) -> None:
    """A changed transactions shape is a portal change: nothing written."""

    coordinator, _, client = make(hass)
    client.transactions_raw = {"unexpected": True}
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.API_CHANGED
    assert await stored(hass, "home_consumption") == []


def test_settled_months() -> None:
    """Issue #75: settled per community and direction, value-free.

    An unmatched correction (both directions) or a month without provisional
    amounts to scale isn't settled.
    """

    gen, con = Direction.GENERATION, Direction.CONSUMPTION
    settlements = Settlements(
        amounts={
            ("beg", gen, (2026, 1)): 1.0,
            ("beg", con, (2026, 1)): 2.0,
            ("beg", gen, (2026, 2)): 1.0,
            ("beg", con, (2026, 3)): 3.0,
            ("eeg", gen, (2026, 1)): 0.5,
        }
    )
    settlements.unmatched.add(("beg", (2026, 2)))
    factors = {
        ("beg", con, (2026, 3)): MonthFactor(MonthState.ZERO_PROVISIONAL, None),
        ("beg", gen, (2026, 1)): MonthFactor(MonthState.SETTLED, 1.1),
    }
    assert settled_months(settlements, factors) == {
        "beg/Consumption": ["2026-01"],
        "beg/Generation": ["2026-01"],
        "eeg/Generation": ["2026-01"],
    }
    assert settled_months(Settlements(), {}) == {}


# ---------------------------------------------------------------------------
# Invoices that cover unselected metering points (issue #104)

SLUGS = {2001: "beg", 2002: "eeg"}


def _point(
    mp_id: int,
    direction: Direction,
    *acts: tuple[int, date, date | None],
) -> MeteringPoint:
    return MeteringPoint(
        id=mp_id,
        direction=direction,
        activations=tuple(
            Activation(
                community_id=cid,
                community_type="CC",
                active_from=begin,
                active_to=end,
                status="Active",
                community_name="Example",
            )
            for cid, begin, end in acts
        ),
        number_tail=f"{mp_id:04d}",
    )


def _info(*locations: tuple[MeteringPoint, ...]) -> MeteringPointInfo:
    return MeteringPointInfo(
        locations=tuple(
            Location(id=f"{5001 + i}", metering_points=points, label="Example")
            for i, points in enumerate(locations)
        )
    )


JAN = date(2026, 1, 1)
OWN = _point(1001, C, (2001, JAN, None), (2002, JAN, None))


def _june_invoices() -> Settlements:
    return build_settlements(
        rows(
            tx("2026-07-20", -34.88, "Strombezug Juni 2026", "Example BEG"),
            tx("2026-07-20", 5.0, "Einspeisevergütung Juni 2026", "Example BEG"),
            tx("2026-07-20", -3.0, "Strombezug Juni 2026", "Example EEG"),
        ),
        NAMES,
    )


def test_other_location_in_the_community_keeps_the_month_provisional() -> None:
    """Two locations in one community: the invoice covers both (#104)."""

    data = _june()
    months = {
        (local_date(p.start).year, local_date(p.start).month) for p in data.consumption
    }
    month = max(months)
    assert month == (2026, 6)
    settlements = _june_invoices()
    other = _point(1003, C, (2001, date(2026, 3, 1), None))
    mark_other_points(settlements, _info((OWN,), (other,)), (1001,), SLUGS)
    key = ("beg", C, month)
    assert settlements.shared == {key}
    # No Zero rate can come from it, and it isn't scaled.
    assert settlements.settled(key) is None
    assert settlements.settled(("eeg", C, month)) == pytest.approx(3.0)
    factors = month_factors({"beg": [data]}, settlements)
    assert factors[key] == MonthFactor(MonthState.OTHER_POINTS, None)
    scaled = apply_settlements({"beg": [data]}, factors)["beg"][0]
    assert _month_total(scaled, C, month) == pytest.approx(_month_total(data, C, month))
    # Feed-in: no other generation point, still settled.
    assert factors[("beg", G, month)].state is MonthState.SETTLED
    assert settled_months(settlements, factors) == {
        "beg/Generation": ["2026-06"],
        "eeg/Consumption": ["2026-06"],
    }
    assert diagnostics(factors, settlements)["other_points"] == [
        "beg/Consumption/2026-06"
    ]


def test_unselected_point_at_the_same_location() -> None:
    """A subset of one location's points: the others share the invoice."""

    settlements = _june_invoices()
    second = _point(1002, C, (2002, JAN, None))
    mark_other_points(settlements, _info((OWN, second)), (1001,), SLUGS)
    assert settlements.shared == {("eeg", C, (2026, 6))}
    # Selected, it is part of the entry: nothing shared.
    settlements = _june_invoices()
    mark_other_points(settlements, _info((OWN, second)), (1001, 1002), SLUGS)
    assert settlements.shared == set()


@pytest.mark.parametrize(
    "other",
    [
        # Active only before or after the month.
        _point(1003, C, (2001, JAN, date(2026, 5, 31))),
        _point(1003, C, (2001, date(2026, 7, 1), None)),
        # Withdrawn before it started.
        _point(1003, C, (2001, date(2026, 6, 10), date(2026, 6, 1))),
        # Another community, or one the entry doesn't select.
        _point(1003, C, (2003, JAN, None)),
        # Another direction (the feed-in invoice is checked against
        # generation points only).
        _point(1003, G, (2002, JAN, None)),
    ],
    ids=["ended-before", "starts-after", "withdrawn", "other-community", "generation"],
)
def test_other_points_that_dont_share_the_invoice(other: MeteringPoint) -> None:
    """Points outside the month, community or direction change nothing."""

    settlements = _june_invoices()
    mark_other_points(settlements, _info((OWN,), (other,)), (1001,), SLUGS)
    assert settlements.shared == set()


def test_point_active_for_part_of_the_month_shares_it() -> None:
    """One day of overlap is enough: the invoice covers that day."""

    settlements = _june_invoices()
    other = _point(1003, C, (2001, date(2026, 6, 30), None))
    mark_other_points(settlements, _info((OWN,), (other,)), (1001,), SLUGS)
    assert settlements.shared == {("beg", C, (2026, 6))}


def test_shared_month_changes_its_digest() -> None:
    """Becoming shared (or no longer) fetches the month again."""

    plain = _june_invoices()
    shared = _june_invoices()
    other = _point(1003, C, (2001, JAN, None))
    mark_other_points(shared, _info((OWN,), (other,)), (1001,), SLUGS)
    assert changed_months(plain.digests(), shared.digests()) == {(2026, 6)}
    assert set(plain.digests()) == set(shared.digests())


async def test_run_with_another_location_keeps_the_month_provisional(
    hass: HomeAssistant, env: Any
) -> None:
    """End to end: the member total is refused, BEG consumption stays provisional."""

    coordinator, _, client = make(hass, options={"daily_window_days": 7})
    client.mp_raw = load_fixture("metering_point_two_locations.json")

    def refuse(raw: Any, aggregation: str, *_: Any) -> Any:
        if aggregation == "Month":
            raise ForbiddenError("HTTP 403")
        return raw

    client.mutate = refuse
    client.transactions_raw = list(
        reversed(
            [
                tx("2026-06-08", -1.23, "Strombezug Juni 2026", "Example BEG"),
                tx("2026-06-08", 0, "Strombezug Juni 2026", "Example EEG"),
            ]
        )
    )
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.OK
    assert summary.settlement is not None
    months = summary.settlement["months"]
    assert months["beg/Consumption/2026-06"] == {
        "state": "other_points",
        "factor": None,
    }
    assert months["eeg/Consumption/2026-06"]["state"] == "settled"
    assert summary.settlement["other_points"] == ["beg/Consumption/2026-06"]
    assert summary.settlement["shared_requests"] == {"failed": 1}
    assert coordinator.state.settled_months == {"eeg/Consumption": ["2026-06"]}
    # Provisional amounts are written as they are, not the 1.23 invoice.
    beg = await _month_sum(hass, "home_beg_consumption_eur", (2026, 6))
    assert beg > 0
    assert beg != pytest.approx(1.23, abs=1e-4)


# Issue #115: a shared month scaled by the member's total.


def _own(
    data: Any, direction: Direction, month: tuple[int, int]
) -> tuple[float, float]:
    kwh = math.fsum(
        p.value or 0
        for p in data.points(direction)
        if (local_date(p.start).year, local_date(p.start).month) == month
    )
    return kwh, _month_total(data, direction, month)


@pytest.mark.parametrize("points", [2, 3], ids=["two-locations", "one-of-three"])
def test_shared_month_gets_its_share_of_the_invoice(points: int) -> None:
    """Each entry's month = invoice * own portal € / member portal €."""

    data = _june()
    month = (2026, 6)
    key = ("beg", C, month)
    settlements = _june_invoices()
    others = tuple(_point(1003 + i, C, (2001, JAN, None)) for i in range(points - 1))
    mark_other_points(settlements, _info((OWN,), others), (1001,), SLUGS)
    assert settlements.others[key] == {1003 + i for i in range(points - 1)}
    kwh, money = _own(data, C, month)
    # The other points draw as much as this one.
    totals = {key: MonthTotal(kwh * points, money * points)}
    factors = month_factors({"beg": [data]}, settlements, totals)
    assert factors[key].state is MonthState.SETTLED_SHARED
    scaled = apply_settlements({"beg": [data]}, factors)["beg"][0]
    assert _month_total(scaled, C, month) == pytest.approx(34.88 / points)
    assert settled_months(settlements, factors)["beg/Consumption"] == ["2026-06"]
    assert (
        diagnostics(factors, settlements)["months"]["beg/Consumption/2026-06"]["state"]
        == "settled_shared"
    )
    # Still no Zero rate from a shared month.
    assert settlements.settled(key) is None


def test_two_entries_add_up_to_the_invoice() -> None:
    """Two locations with a 2:1 split: their months sum to the invoice."""

    data = _june()
    month = (2026, 6)
    key = ("beg", C, month)
    kwh, money = _own(data, C, month)
    total = MonthTotal(kwh * 3, money * 3)  # the other site draws twice as much
    mine = _june_invoices()
    mark_other_points(
        mine, _info((OWN,), (_point(1003, C, (2001, JAN, None)),)), (1001,), SLUGS
    )
    first = _month_total(
        apply_settlements(
            {"beg": [data]}, month_factors({"beg": [data]}, mine, {key: total})
        )["beg"][0],
        C,
        month,
    )
    # The other entry: same hours at twice the amounts.
    other = apply_settlements(
        {"beg": [data]}, {key: MonthFactor(MonthState.SETTLED, 2.0)}
    )
    theirs = _june_invoices()
    mark_other_points(
        theirs, _info((OWN,), (_point(1003, C, (2001, JAN, None)),)), (1003,), SLUGS
    )
    second = _month_total(
        apply_settlements(other, month_factors(other, theirs, {key: total}))["beg"][0],
        C,
        month,
    )
    assert first == pytest.approx(34.88 / 3)
    assert second == pytest.approx(34.88 * 2 / 3)
    assert first + second == pytest.approx(34.88)


def test_shared_share_ignores_the_zero_repricing() -> None:
    """The share follows the portal amounts, whatever the repriced hours are."""

    data = _june()
    month = (2026, 6)
    key = ("beg", C, month)
    settlements = _june_invoices()
    mark_other_points(
        settlements,
        _info((OWN,), (_point(1003, C, (2001, JAN, None)),)),
        (1001,),
        SLUGS,
    )
    kwh, money = _own(data, C, month)
    repriced = apply_settlements(
        {"beg": [data]}, {key: MonthFactor(MonthState.SETTLED, 0.7)}
    )
    factors = month_factors(
        repriced, settlements, {key: MonthTotal(kwh * 2, money * 2)}, {"beg": [data]}
    )
    scaled = apply_settlements(repriced, factors)["beg"][0]
    assert _month_total(scaled, C, month) == pytest.approx(34.88 / 2)


@pytest.mark.parametrize(
    "scale",
    [1.0, 0.5],
    ids=["others-dropped", "total-below-own"],
)
def test_shared_total_that_cant_be_right_stays_provisional(scale: float) -> None:
    """A total without the other points must never bring back the whole invoice."""

    data = _june()
    month = (2026, 6)
    key = ("beg", C, month)
    settlements = _june_invoices()
    mark_other_points(
        settlements,
        _info((OWN,), (_point(1003, C, (2001, JAN, None)),)),
        (1001,),
        SLUGS,
    )
    kwh, money = _own(data, C, month)
    factors = month_factors(
        {"beg": [data]}, settlements, {key: MonthTotal(kwh * scale, money * scale)}
    )
    assert factors[key] == MonthFactor(MonthState.OTHER_POINTS, None)
    assert "beg/Consumption" not in settled_months(settlements, factors)


def test_shared_requests_one_per_community() -> None:
    """Only fetched shared months; one range per community; ids merged."""

    data = _june()
    settlements = build_settlements(
        rows(
            tx("2026-07-20", -34.88, "Strombezug Juni 2026", "Example BEG"),
            tx("2026-07-20", 5.0, "Einspeisevergütung Juni 2026", "Example BEG"),
            tx("2026-06-20", -9.0, "Strombezug Mai 2026", "Example BEG"),
            tx("2026-07-20", -3.0, "Strombezug Juni 2026", "Example EEG"),
        ),
        NAMES,
    )
    mark_other_points(
        settlements,
        _info(
            (OWN,),
            (
                _point(1003, C, (2001, JAN, None)),
                _point(1004, G, (2001, JAN, None)),
            ),
        ),
        (1001,),
        SLUGS,
    )
    # May is shared but not in this run's hours: not requested.
    assert ("beg", C, (2026, 5)) in settlements.shared
    plan = shared_requests(settlements, {"beg": [data], "eeg": []}, (1001,))
    assert len(plan) == 1
    req = plan[0]
    assert (req.slug, req.first, req.last) == (
        "beg",
        date(2026, 6, 1),
        date(2026, 6, 30),
    )
    assert req.mp_ids == (1001, 1003, 1004)
    # An unmatched correction: nothing to scale, nothing to request.
    settlements.unmatched.add(("beg", (2026, 6)))
    assert shared_requests(settlements, {"beg": [data]}, (1001,)) == []


def test_shared_month_outside_the_window_stays_settled() -> None:
    """A shared month scaled in an earlier run keeps its settled marker."""

    settlements = _june_invoices()
    mark_other_points(
        settlements,
        _info((OWN,), (_point(1003, C, (2001, JAN, None)),)),
        (1001,),
        SLUGS,
    )
    previous = {"beg/Consumption": ["2026-06"]}
    assert settled_months(settlements, {}, previous)["beg/Consumption"] == ["2026-06"]
    assert "beg/Consumption" not in settled_months(settlements, {})


async def test_run_scales_a_shared_month_by_the_member_total(
    hass: HomeAssistant, env: Any
) -> None:
    """End to end: one Month request, the month = invoice * own / member total."""

    coordinator, _, client = make(hass, options={"daily_window_days": 7})
    client.mp_raw = load_fixture("metering_point_two_locations.json")
    client.transactions_raw = list(
        reversed(
            [
                tx("2026-06-08", -1.23, "Strombezug Juni 2026", "Example BEG"),
                tx("2026-06-08", 0, "Strombezug Juni 2026", "Example EEG"),
            ]
        )
    )
    seen: dict[str, list[Any]] = {"Hour": [], "Month": []}

    def capture(raw: Any, aggregation: str, start: Any, end: Any, cid: int) -> Any:
        if cid == 2001 and aggregation in seen:
            seen[aggregation].append(parse_analysis(raw))
        return raw

    client.mutate = capture
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.OK
    assert summary.settlement is not None
    assert summary.settlement["months"]["beg/Consumption/2026-06"]["state"] == (
        "settled_shared"
    )
    assert summary.settlement["shared_requests"] == {"ok": 1}
    month_calls = client.analysis_calls("Month")
    assert len(month_calls) == 1
    assert month_calls[0][2:] == (date(2026, 6, 1), date(2026, 6, 30), [2001])
    assert coordinator.state.settled_months == {
        "beg/Consumption": ["2026-06"],
        "eeg/Consumption": ["2026-06"],
    }
    own = math.fsum(_month_total(d, C, (2026, 6)) for d in seen["Hour"])
    member = math.fsum(_month_total(d, C, (2026, 6)) for d in seen["Month"])
    assert 0 < own < member
    beg = await _month_sum(hass, "home_beg_consumption_eur", (2026, 6))
    assert beg == pytest.approx(1.23 * own / member, abs=1e-4)
