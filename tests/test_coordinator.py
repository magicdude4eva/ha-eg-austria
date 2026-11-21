"""Coordinator, scheduler and importer tests (test plan cases 22-30, spec 03).

The S1 client is replaced by ``FakeClient`` (generated synthetic data, no
HTTP); time is frozen with ``freezer``; statistics go to the test recorder.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
import itertools
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.eg_austria.config_flow import default_options
from custom_components.eg_austria.const import (
    CONF_COMMUNITIES,
    CONF_DAILY_WINDOW_DAYS,
    CONF_IMPORT_HISTORY,
    CONF_LONG_REFETCH_MONTHS,
    DOMAIN,
)
from custom_components.eg_austria.coordinator import (
    EgAustriaCoordinator,
    IssueHints,
    RunRequest,
    StoreData,
    entry_store,
)
from custom_components.eg_austria.exceptions import (
    AuthError,
    PortalUnavailableError,
    RateLimitedError,
)
from custom_components.eg_austria.importer import (
    RunStatus,
    RunType,
    add_months,
    autumn_dst_days,
    chunk_window,
    complete_until,
    daily_window_start,
    first_incomplete,
    request_count,
)
from custom_components.eg_austria.models import DayCompleteness, parse_day_completeness
from custom_components.eg_austria.statistics import local_day_start

from .conftest import FakeClient, entry_data, load_fixture

NOW = datetime(2026, 6, 8, 4, 0, tzinfo=UTC)  # 06:00 in Vienna
TODAY = date(2026, 6, 8)
ACTIVATION = date(2026, 1, 1)  # metering_point_single.json, BEG


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before ``hass`` (see test_statistics.py)."""


@pytest.fixture
def no_writes() -> Iterator[AsyncMock]:
    """Skip the statistics write (the test recorder needs ~5 ms per row)."""

    with patch(
        "custom_components.eg_austria.importer.async_import_window",
        AsyncMock(return_value={}),
    ) as mock:
        yield mock


@pytest.fixture
async def env(
    hass: HomeAssistant, recorder_mock: Any, freezer: FrozenDateTimeFactory
) -> FrozenDateTimeFactory:
    """Frozen time in Vienna with a recorder."""

    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Vienna")
    return freezer


def make(
    hass: HomeAssistant,
    client: FakeClient | None = None,
    *,
    options: dict[str, Any] | None = None,
    token_exp: int | None = None,
    initial_done: bool = True,
) -> tuple[EgAustriaCoordinator, MockConfigEntry, FakeClient]:
    """Return a coordinator on an added (not set up) entry."""

    client = client or FakeClient()
    exp = token_exp if token_exp is not None else int(NOW.timestamp()) + 86400 * 300
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EG Austria Home",
        data=entry_data(exp),
        options={**default_options(), **(options or {})},
    )
    entry.add_to_hass(hass)
    coordinator = EgAustriaCoordinator(
        hass, entry, client, entry_store(hass, entry.entry_id)
    )
    coordinator.state.initial_done = initial_done
    return coordinator, entry, client


async def stored(hass: HomeAssistant, key: str) -> list[dict[str, Any]]:
    """Every stored hourly row of one statistic."""

    await async_wait_recording_done(hass)
    statistic_id = f"{DOMAIN}:{key}"
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2020, 1, 1, tzinfo=UTC),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return rows.get(statistic_id, [])


def _hour(row: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(row["start"], UTC)


def _complete(*days: date, incomplete: tuple[date, ...] = ()) -> list[DayCompleteness]:
    return [DayCompleteness(d, d not in incomplete, d not in incomplete) for d in days]


def _range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# ---------------------------------------------------------------------------
# 22. Daily window


def test_daily_window_from_completeness() -> None:
    """Earliest incomplete day in the last 120 days; at least 62 days."""

    days = _range(date(2026, 1, 1), TODAY - timedelta(days=1))
    # Everything complete (today missing = incomplete): 62 days back.
    assert daily_window_start(TODAY, _complete(*days), 62) == date(2026, 4, 7)
    # An incomplete day inside the 120-day lookback extends the window.
    entries = _complete(*days, incomplete=(date(2026, 3, 1),))
    assert daily_window_start(TODAY, entries, 62) == date(2026, 3, 1)
    # A missing day counts as incomplete.
    gap = [e for e in _complete(*days) if e.day != date(2026, 3, 20)]
    assert daily_window_start(TODAY, gap, 62) == date(2026, 3, 20)
    # Older than 120 days: capped, the long re-fetch picks it up.
    entries = _complete(*days, incomplete=(date(2026, 1, 20),))
    assert first_incomplete(entries, date(2026, 1, 1), TODAY) == date(2026, 1, 20)
    assert daily_window_start(TODAY, entries, 62) == date(2026, 4, 7)
    # A longer configured window wins over a later incomplete day.
    entries = _complete(*days, incomplete=(date(2026, 5, 1),))
    assert daily_window_start(TODAY, entries, 90) == date(2026, 3, 10)
    # Null flags (future days, parsed as False) are incomplete.
    future = parse_day_completeness(
        [
            {
                "date": "2026-06-08",
                "consumptionComplete": None,
                "generationComplete": None,
            }
        ]
    )
    assert first_incomplete(future, TODAY, TODAY) == TODAY


def test_complete_until() -> None:
    """Contiguous complete run for the ``data_complete_until`` sensor."""

    days = _range(date(2026, 1, 1), date(2026, 6, 7))
    entries = _complete(*days, incomplete=(date(2026, 5, 20),))
    first, last = date(2026, 1, 1), TODAY
    assert complete_until(entries, first, last, None) == date(2026, 5, 19)
    assert complete_until(_complete(*days), first, date(2026, 6, 7), None) == date(
        2026, 6, 7
    )
    # Stored value ends before the fetched range: the gap was not re-checked.
    later = date(2026, 3, 1)
    assert complete_until(entries, later, last, date(2026, 1, 31)) == date(2026, 1, 31)
    # First fetched day incomplete.
    bad_first = _complete(*days, incomplete=(later,))
    assert complete_until(bad_first, later, last, date(2026, 5, 1)) == date(2026, 2, 28)
    assert complete_until(bad_first, later, last, None) is None


async def test_daily_run_window(hass: HomeAssistant, env: Any) -> None:
    """A daily run starts at the earliest incomplete day and writes."""

    coordinator, _, client = make(hass, options={CONF_DAILY_WINDOW_DAYS: 5})
    client.incomplete = {date(2026, 5, 25)}
    client.future_from = TODAY
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.OK
    assert summary.window == (date(2026, 5, 25), TODAY)
    assert summary.issues == IssueHints(False, False, False)
    # Completeness covers 13 months in one request.
    (comp,) = [a for n, a in client.calls if n == "get_day_completeness"]
    assert comp[1] == add_months(TODAY, -13) + timedelta(days=1)
    assert comp[2] == TODAY
    rows = await stored(hass, "home_consumption")
    assert _hour(rows[0]) == local_day_start(date(2026, 5, 25))
    assert coordinator.state.last_success == NOW
    assert coordinator.state.complete_until == date(2026, 5, 24)


@pytest.mark.usefixtures("no_writes")
async def test_daily_window_floor_is_activation(hass: HomeAssistant, env: Any) -> None:
    """No request before the earliest activation of the selected points."""

    coordinator, _, client = make(hass)
    for mp in client.mp_raw[0]["memberMeteringpoints"]:
        for act in mp["activations"]:
            act["begin"] = "2026-05-01"
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.window == (date(2026, 5, 1), TODAY)
    assert min(a[2] for a in client.analysis_calls()) == date(2026, 5, 1)


@pytest.mark.usefixtures("no_writes")
async def test_community_names_refreshed(hass: HomeAssistant, env: Any) -> None:
    """``communities[].name`` follows the portal; slugs and ids don't change."""

    coordinator, entry, client = make(hass)
    act = client.mp_raw[0]["memberMeteringpoints"][0]["activations"][0]
    act["energyCommunityName"] = "Renamed BEG"
    for mp in client.mp_raw[0]["memberMeteringpoints"][1:]:
        mp["activations"][0]["energyCommunityName"] = "Renamed BEG"
    await coordinator.async_run(RunRequest(RunType.DAILY))
    beg = entry.data[CONF_COMMUNITIES][0]
    assert beg == {"id": 2001, "slug": "beg", "name": "Renamed BEG"}


# ---------------------------------------------------------------------------
# 23. Long run


async def test_long_run_schedule(hass: HomeAssistant, env: Any) -> None:
    """First daily run on or after the 3rd, once per month."""

    coordinator, _, _ = make(hass)

    def at(day: date) -> datetime:
        return datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=2)

    assert coordinator.scheduled_run_type(at(date(2026, 6, 2))) is RunType.DAILY
    assert coordinator.scheduled_run_type(at(date(2026, 6, 3))) is RunType.LONG
    coordinator.state.last_long_success = datetime(2026, 5, 20, tzinfo=UTC)
    assert coordinator.scheduled_run_type(at(date(2026, 6, 10))) is RunType.LONG
    coordinator.state.last_long_success = at(date(2026, 6, 3))
    assert coordinator.scheduled_run_type(at(date(2026, 6, 4))) is RunType.DAILY
    assert coordinator.scheduled_run_type(at(date(2026, 6, 30))) is RunType.DAILY
    assert coordinator.scheduled_run_type(at(date(2026, 7, 2))) is RunType.DAILY
    assert coordinator.scheduled_run_type(at(date(2026, 7, 3))) is RunType.LONG
    # Initial import pending (import_history on) comes first.
    coordinator.state.initial_done = False
    assert coordinator.scheduled_run_type(at(date(2026, 6, 4))) is RunType.INITIAL


@pytest.mark.usefixtures("no_writes")
async def test_long_run(hass: HomeAssistant, env: Any) -> None:
    """The long window is ``long_refetch_months`` back, regardless of completeness."""

    coordinator, _, client = make(hass, options={CONF_LONG_REFETCH_MONTHS: 3})
    summary = await coordinator.async_run(RunRequest(RunType.LONG))
    assert summary is not None
    assert summary.status is RunStatus.OK
    assert summary.window == (date(2026, 3, 8), TODAY)
    assert summary.chunks == 4
    assert coordinator.state.last_long_success == NOW
    (comp,) = [a for n, a in client.calls if n == "get_day_completeness"]
    assert (comp[1], comp[2]) == (date(2026, 3, 8), TODAY)


@pytest.mark.usefixtures("no_writes")
async def test_daily_listener_runs_long(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """The time listener fires at ``run_minute`` local and picks the long run."""

    coordinator, entry, _ = make(hass, options={CONF_IMPORT_HISTORY: False})
    start = datetime(2026, 6, 3, 1, 59, tzinfo=UTC)  # 03:59 Vienna
    env.move_to(start)
    await entry_store(hass, entry.entry_id).async_save(
        {"last_success": (start - timedelta(hours=2)).isoformat()}
    )
    await coordinator.async_start()
    env.move_to(start + timedelta(minutes=1))
    async_fire_time_changed(hass, start + timedelta(minutes=1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data is not None
    assert coordinator.data.run_type is RunType.LONG
    await coordinator.async_stop()


# ---------------------------------------------------------------------------
# 24. Startup catch-up


async def test_startup_run_type(hass: HomeAssistant, env: Any) -> None:
    """Catch up only when the last success is > 26 h old; never in a loop."""

    coordinator, _, _ = make(hass)
    state = coordinator.state
    state.last_long_success = NOW - timedelta(days=1)
    assert coordinator.startup_run_type(NOW) is RunType.DAILY  # never succeeded
    state.last_success = NOW - timedelta(hours=27)
    assert coordinator.startup_run_type(NOW) is RunType.DAILY
    state.last_success = NOW - timedelta(hours=25)
    assert coordinator.startup_run_type(NOW) is None
    state.last_success = NOW - timedelta(days=3)
    state.last_attempt = NOW - timedelta(minutes=10)
    assert coordinator.startup_run_type(NOW) is None  # restart loop: no storm
    state.last_attempt = NOW - timedelta(hours=2)
    assert coordinator.startup_run_type(NOW) is RunType.DAILY
    state.initial_done = False
    assert coordinator.startup_run_type(NOW) is RunType.INITIAL


@pytest.mark.usefixtures("no_writes")
async def test_startup_catch_up_is_delayed_and_single(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """One catch-up run, 1-5 minutes after start, not at once."""

    coordinator, entry, client = make(hass)
    await entry_store(hass, entry.entry_id).async_save(
        {
            "initial_done": True,
            "last_success": (NOW - timedelta(days=2)).isoformat(),
            "last_long_success": (NOW - timedelta(days=3)).isoformat(),
        }
    )
    await coordinator.async_start()
    await hass.async_block_till_done()
    assert client.calls == []
    later = NOW + timedelta(seconds=301)
    env.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data is not None
    assert coordinator.data.run_type is RunType.DAILY
    assert len([c for c in client.calls if c[0] == "get_maintenance"]) == 1
    await coordinator.async_stop()


async def test_startup_no_run_when_recent(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """A recent success means no startup run."""

    coordinator, entry, client = make(hass)
    await entry_store(hass, entry.entry_id).async_save(
        {"initial_done": True, "last_success": (NOW - timedelta(hours=3)).isoformat()}
    )
    await coordinator.async_start()
    later = NOW + timedelta(minutes=10)
    env.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert client.calls == []
    await coordinator.async_stop()


# ---------------------------------------------------------------------------
# 25. Maintenance


@pytest.mark.usefixtures("no_writes")
async def test_maintenance_active_reschedules(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """Inside a window (± 30 min): skipped, then run 30 min after its end."""

    now = datetime(2026, 6, 8, 2, 0, tzinfo=UTC)  # window 01:00-03:00 UTC
    env.move_to(now)
    coordinator, _, client = make(hass)
    client.maintenance_raw = load_fixture("maintenance_active.json")
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.SKIPPED_MAINTENANCE
    assert summary.rescheduled_for == datetime(2026, 6, 8, 3, 30, tzinfo=UTC)
    assert summary.issues == IssueHints()
    assert [c[0] for c in client.calls] == ["get_maintenance"]
    assert coordinator.state.last_success is None

    client.maintenance_raw = None
    resume = datetime(2026, 6, 8, 3, 30, 1, tzinfo=UTC)
    env.move_to(resume)
    async_fire_time_changed(hass, resume)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data is not None
    assert coordinator.data.status is RunStatus.OK
    assert coordinator.data.run_type is RunType.DAILY


async def test_maintenance_margin(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """The 30 min margin before the window counts as maintenance."""

    env.move_to(datetime(2026, 6, 8, 0, 31, tzinfo=UTC))
    coordinator, _, client = make(hass)
    client.maintenance_raw = load_fixture("maintenance_active.json")
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.SKIPPED_MAINTENANCE
    await coordinator.async_stop()


@pytest.mark.usefixtures("no_writes")
async def test_maintenance_future_runs(hass: HomeAssistant, env: Any) -> None:
    """An announced future window doesn't stop today's run."""

    coordinator, _, client = make(hass)
    client.maintenance_raw = load_fixture("maintenance_future.json")
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.OK
    assert len(summary.maintenance) == 1


# ---------------------------------------------------------------------------
# 26. Version


@pytest.mark.usefixtures("no_writes")
async def test_version_change_flagged(hass: HomeAssistant, env: Any) -> None:
    """A new portal build is flagged; the run continues."""

    coordinator, _, _ = make(hass)
    coordinator.state.portal_git_commit = "b" * 40
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.portal_version_changed
    assert summary.status is RunStatus.OK
    assert coordinator.state.portal_git_commit == "a" * 40
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert not summary.portal_version_changed


# ---------------------------------------------------------------------------
# 27. Chunking and request count


def test_chunking() -> None:
    """≤ 31 days, aligned to calendar months."""

    chunks = chunk_window(date(2026, 4, 7), TODAY)
    assert chunks == [
        (date(2026, 4, 7), date(2026, 4, 30)),
        (date(2026, 5, 1), date(2026, 5, 31)),
        (date(2026, 6, 1), TODAY),
    ]
    long = chunk_window(date(2026, 1, 9), date(2027, 2, 8))
    assert len(long) == 14
    assert all((e - s).days < 31 for s, e in long)
    assert all(b[0] - a[1] == timedelta(days=1) for a, b in itertools.pairwise(long))
    assert long[0][0] == date(2026, 1, 9)
    assert long[-1][1] == date(2027, 2, 8)
    assert chunk_window(TODAY, TODAY) == [(TODAY, TODAY)]
    assert autumn_dst_days(date(2026, 1, 9), date(2027, 2, 8)) == [date(2026, 10, 25)]
    assert autumn_dst_days(date(2026, 10, 1), date(2026, 10, 24)) == []
    # Spec 03: 2 communities, 62-day window.
    assert request_count(chunks, 2) == 10
    assert request_count(chunk_window(date(2026, 10, 1), date(2026, 10, 31)), 2) == 8


@pytest.mark.usefixtures("no_writes")
async def test_request_count_daily(hass: HomeAssistant, env: Any) -> None:
    """One Hour request per community and chunk; every request counted."""

    coordinator, _, client = make(hass)
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.window == (date(2026, 4, 7), TODAY)
    assert summary.chunks == 3
    assert summary.requests == 10 == client.request_count
    hour_calls = client.analysis_calls("Hour")
    assert len(hour_calls) == 6
    assert {tuple(c[4]) for c in hour_calls} == {(2001,), (2002,)}
    assert client.analysis_calls("Day") == []


async def test_autumn_dst_day_request(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """The autumn DST day adds one Day request per community; gap hour filled."""

    env.move_to(datetime(2026, 11, 2, 4, 0, tzinfo=UTC))
    coordinator, _, client = make(hass)
    summary = await coordinator.async_run(
        RunRequest(RunType.MANUAL, date(2026, 10, 24), date(2026, 10, 26))
    )
    assert summary is not None
    assert summary.status is RunStatus.OK, summary.error
    day_calls = client.analysis_calls("Day")
    assert [(c[2], c[3], tuple(c[4])) for c in day_calls] == [
        (date(2026, 10, 25), date(2026, 10, 25), (2001,)),
        (date(2026, 10, 25), date(2026, 10, 25), (2002,)),
    ]
    rows = await stored(hass, "home_consumption")
    day_rows = [r for r in rows if _hour(r).date() == date(2026, 10, 25)]
    # 25 local hours: the gap hour (00:00 UTC) is filled from the Day value.
    assert datetime(2026, 10, 25, 0, 0, tzinfo=UTC) in {_hour(r) for r in rows}
    assert len(rows) == 24 + 25 + 24
    assert day_rows


# ---------------------------------------------------------------------------
# 28. Error paths


async def test_auth_error_starts_reauth(hass: HomeAssistant, env: Any) -> None:
    """401 during a run → ``ConfigEntryAuthFailed`` → reauth flow."""

    coordinator, entry, client = make(hass)
    client.errors["get_version"] = AuthError("HTTP 401: Access token revoked")
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.AUTH_FAILED
    assert client.analysis_calls() == []
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    assert flows[0]["context"]["entry_id"] == entry.entry_id


async def test_schema_error_writes_nothing(hass: HomeAssistant, env: Any) -> None:
    """A parser failure in a later chunk: no statistic written at all."""

    coordinator, _, client = make(hass)

    def mutate(raw: Any, agg: str, start: date, end: date, cid: int) -> Any:
        return [raw] if start.month == 6 else raw  # S1: not an object

    client.mutate = mutate
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.API_CHANGED
    assert summary.issues.api_changed is True
    assert summary.validation is not None
    assert summary.validation.schema_failed
    assert "S1" in summary.validation.rule_ids()
    assert await stored(hass, "home_consumption") == []
    assert await stored(hass, "home_generation") == []
    assert coordinator.state.last_success is None


async def test_shape_rule_writes_nothing(hass: HomeAssistant, env: Any) -> None:
    """A validation shape rule (S6 duplicate) also stops the whole run."""

    coordinator, _, client = make(hass)

    def mutate(raw: Any, agg: str, start: date, end: date, cid: int) -> Any:
        if start.month == 5 and cid == 2002:
            points = raw["consumptionDataPoints"]
            points[1]["dateTimeFrom"] = points[0]["dateTimeFrom"]
        return raw

    client.mutate = mutate
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.API_CHANGED
    assert summary.validation is not None
    assert "S6" in summary.validation.rule_ids()
    assert summary.error
    assert "9000001" not in summary.error
    assert await stored(hass, "home_consumption") == []


async def test_api_changed_metering_points_gone(hass: HomeAssistant, env: Any) -> None:
    """Selected metering points missing from the portal → api_changed."""

    coordinator, _, client = make(hass)
    for mp in client.mp_raw[0]["memberMeteringpoints"]:
        mp["id"] += 100
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.API_CHANGED
    assert client.analysis_calls() == []


async def test_plausibility_partial_write(hass: HomeAssistant, env: Any) -> None:
    """A negative value blocks the statistics involved for that chunk only."""

    coordinator, _, client = make(hass, options={CONF_DAILY_WINDOW_DAYS: 10})

    def mutate(raw: Any, agg: str, start: date, end: date, cid: int) -> Any:
        if start.month == 6 and cid == 2001:
            for point in raw["generationDataPoints"]:
                if point["generation"]:
                    point["generation"] = -abs(point["generation"])
                    break
        return raw

    client.mutate = mutate
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.PARTIAL
    assert summary.issues.implausible_data is True
    assert summary.issues.api_changed is False
    assert summary.validation is not None
    assert "P1" in summary.validation.rule_ids()
    june = _range(date(2026, 6, 1), TODAY)
    assert summary.blocked["home_generation"] == june
    assert "home_consumption" not in summary.blocked
    june_start = local_day_start(date(2026, 6, 1))
    gen = await stored(hass, "home_generation")
    assert gen
    assert all(_hour(r) < june_start for r in gen)
    cons = await stored(hass, "home_consumption")
    assert any(_hour(r) >= june_start for r in cons)
    assert coordinator.state.last_success == NOW  # partial counts as success


@pytest.mark.usefixtures("no_writes")
async def test_unavailable_three_days_raises_repair_hint(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """Failure days are counted once per calendar day; at 3 the hint is set."""

    coordinator, _, client = make(hass)
    client.errors["get_maintenance"] = PortalUnavailableError("HTTP 503", status=503)
    hints = []
    for offset in (0, 2, 24, 48):  # hours: day 1, day 1, day 2, day 3
        env.move_to(NOW + timedelta(hours=offset))
        summary = await coordinator.async_run(RunRequest(RunType.DAILY))
        assert summary is not None
        assert summary.status is RunStatus.UNAVAILABLE
        hints.append(
            (
                coordinator.state.consecutive_failure_days,
                summary.issues.portal_unreachable,
            )
        )
    assert hints == [(1, False), (1, False), (2, False), (3, True)]
    del client.errors["get_maintenance"]
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.issues.portal_unreachable is False
    assert coordinator.state.consecutive_failure_days == 0


@pytest.mark.usefixtures("no_writes")
async def test_rate_limited_doubles_spacing_next_run(
    hass: HomeAssistant, env: Any
) -> None:
    """429 aborts; the next run uses twice the request spacing, then resets."""

    coordinator, _, client = make(hass)
    client.errors["get_version"] = RateLimitedError("HTTP 429", retry_after=60)
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.RATE_LIMITED
    assert coordinator.state.rate_limited
    del client.errors["get_version"]
    client.intervals.clear()
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.OK
    assert set(client.intervals) == {4.0}
    assert client.min_interval == 2.0
    assert not coordinator.state.rate_limited


# ---------------------------------------------------------------------------
# 29. Expired token


async def test_expired_token_no_request(hass: HomeAssistant, env: Any) -> None:
    """Past the stored expiry: no request at all, reauth starts."""

    coordinator, _, client = make(hass, token_exp=int(NOW.timestamp()) - 60)
    summary = await coordinator.async_run(RunRequest(RunType.DAILY))
    assert summary is not None
    assert summary.status is RunStatus.AUTH_FAILED
    assert client.calls == []
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


# ---------------------------------------------------------------------------
# 30. Concurrency


@pytest.mark.usefixtures("no_writes")
async def test_concurrent_triggers_one_run(hass: HomeAssistant, env: Any) -> None:
    """A trigger during a run is dropped."""

    coordinator, _, client = make(hass)
    gate = asyncio.Event()
    started = asyncio.Event()

    async def before(name: str) -> None:
        started.set()
        if name == "get_maintenance":
            await gate.wait()

    client.before = before
    first = hass.async_create_task(coordinator.async_run(RunRequest(RunType.DAILY)))
    await started.wait()
    assert coordinator.running
    assert coordinator.async_trigger(RunRequest(RunType.MANUAL)) is False
    assert await coordinator.async_run(RunRequest(RunType.DAILY)) is None
    gate.set()
    summary = await first
    assert summary is not None
    assert summary.status is RunStatus.OK
    assert [c[0] for c in client.calls].count("get_maintenance") == 1


# ---------------------------------------------------------------------------
# Store


def test_store_round_trip() -> None:
    """The store keeps dates and counters; broken values fall back."""

    state = StoreData(
        last_success=NOW,
        last_attempt=NOW,
        last_long_success=NOW - timedelta(days=3),
        portal_git_commit="a" * 40,
        consecutive_failure_days=2,
        last_failure_day=TODAY,
        held_days={"home_generation": [date(2026, 6, 3)]},
        released={"home_consumption": [date(2026, 6, 2)]},
        complete_until=date(2026, 6, 6),
        initial_done=True,
        rate_limited=True,
    )
    assert StoreData.from_dict(state.as_dict()) == state
    broken = StoreData.from_dict(
        {
            "last_success": 5,
            "consecutive_failure_days": "x",
            "held_days": {"k": ["no-date", None]},
            "complete_until": "2026-13-01",
        }
    )
    assert broken == StoreData()
    assert StoreData.from_dict(None) == StoreData()
