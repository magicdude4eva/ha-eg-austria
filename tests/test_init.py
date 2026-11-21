"""Integration tests: setup, unload, reload and services (cases 40-41).

The client factory is patched to return ``FakeClient``; nothing touches the
network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.eg_austria.config_flow import default_options
from custom_components.eg_austria.const import DOMAIN
from custom_components.eg_austria.coordinator import entry_store
from custom_components.eg_austria.importer import RunStatus, RunType
from custom_components.eg_austria.statistics import local_day_start

from .conftest import FakeClient, entry_data

NOW = datetime(2026, 6, 8, 4, 0, tzinfo=UTC)
TODAY = date(2026, 6, 8)


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


@pytest.fixture
def client() -> FakeClient:
    """Return the client double the integration gets."""

    return FakeClient()


@pytest.fixture
def session() -> MagicMock:
    """Stand-in for the aiohttp session."""

    mock = MagicMock()
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def patched(client: FakeClient, session: MagicMock) -> Iterator[MagicMock]:
    """Patch the client factory and the session factory."""

    with (
        patch(
            "custom_components.eg_austria.EgAustriaClient", return_value=client
        ) as factory,
        patch(
            "custom_components.eg_austria.create_portal_session", return_value=session
        ),
    ):
        yield factory


def add_entry(hass: HomeAssistant, *, exp_offset: int = 86400 * 300) -> MockConfigEntry:
    """Add an entry with a synthetic token."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EG Austria Home",
        data=entry_data(int(NOW.timestamp()) + exp_offset),
        options=default_options(),
    )
    entry.add_to_hass(hass)
    return entry


async def loaded_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return an entry set up after its initial import (no startup run)."""

    entry = add_entry(hass)
    await entry_store(hass, entry.entry_id).async_save(
        {
            "initial_done": True,
            "last_success": (NOW - timedelta(hours=2)).isoformat(),
            "last_long_success": (NOW - timedelta(days=5)).isoformat(),
        }
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def move(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, **delta: Any
) -> None:
    """Advance time, fire listeners, wait for background runs."""

    target = NOW + timedelta(**delta)
    freezer.move_to(target)
    async_fire_time_changed(hass, target)
    await hass.async_block_till_done(wait_background_tasks=True)


async def rows(hass: HomeAssistant, key: str) -> list[dict[str, Any]]:
    """Return the stored hourly rows of one statistic."""

    await async_wait_recording_done(hass)
    statistic_id = f"{DOMAIN}:{key}"
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2020, 1, 1, tzinfo=UTC),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return result.get(statistic_id, [])


def _hour(row: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(row["start"], UTC)


# ---------------------------------------------------------------------------
# 40. Setup → initial import → statistics → unload → reload


async def test_setup_initial_import_unload_reload(
    hass: HomeAssistant,
    env: FrozenDateTimeFactory,
    patched: MagicMock,
    client: FakeClient,
    session: MagicMock,
) -> None:
    """The full life cycle of an entry."""

    # A recent activation keeps the initial import (and the recorder) short.
    for mp in client.mp_raw[0]["memberMeteringpoints"]:
        for act in mp["activations"]:
            act["begin"] = "2026-05-25"
    entry = add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert client.calls == []  # setup never fetches
    kwargs = patched.call_args.kwargs
    assert kwargs["token_exp"] == entry.data["token_exp"]

    await move(hass, env, seconds=301)
    coordinator = entry.runtime_data
    summary = coordinator.data
    assert summary is not None
    assert summary.run_type is RunType.INITIAL
    assert summary.status is RunStatus.OK, summary.error
    assert summary.window == (date(2026, 5, 25), TODAY)
    assert summary.chunks == 2
    assert coordinator.state.initial_done
    assert coordinator.state.last_long_success == summary.started

    consumption = await rows(hass, "home_consumption")
    assert _hour(consumption[0]) == local_day_start(date(2026, 5, 25))
    assert consumption[-1]["sum"] == pytest.approx(sum(r["state"] for r in consumption))
    metadata = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_source=DOMAIN)
    )
    names = {sid: meta["name"] for sid, (_, meta) in metadata.items()}
    assert names["eg_austria:home_consumption"] == "Home Consumption"
    assert (
        names["eg_austria:home_beg_generation_to_community"]
        == "Example BEG Generation to community"
    )
    assert len(names) == 10  # 6 site + 2 x 2 community statistics

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    session.close.assert_awaited_once()
    saved = await entry_store(hass, entry.entry_id).async_load()
    assert saved is not None
    assert saved["initial_done"] is True

    calls = len(client.calls)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await move(hass, env, minutes=20)
    assert entry.state is ConfigEntryState.LOADED
    assert len(client.calls) == calls  # recent success: no startup run
    assert entry.runtime_data.state.initial_done

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert await entry_store(hass, entry.entry_id).async_load() is None


async def test_setup_expired_token_starts_reauth(
    hass: HomeAssistant, env: Any, patched: MagicMock
) -> None:
    """Past the stored expiry: setup fails with reauth, no client."""

    entry = add_entry(hass, exp_offset=-60)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    patched.assert_not_called()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_unload_during_run_writes_nothing(
    hass: HomeAssistant, env: Any, patched: MagicMock, client: FakeClient
) -> None:
    """Unload lets the current request finish, then the run stops unwritten."""

    entry = await loaded_entry(hass)
    gate, started = asyncio.Event(), asyncio.Event()

    async def before(name: str) -> None:
        if name == "get_analysis":
            started.set()
            await gate.wait()

    client.before = before
    await hass.services.async_call(
        DOMAIN, "refetch", {"config_entry_id": entry.entry_id}, blocking=True
    )
    await started.wait()
    unload = hass.async_create_task(hass.config_entries.async_unload(entry.entry_id))
    for _ in range(50):
        await asyncio.sleep(0)
    gate.set()
    assert await unload
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert len(client.analysis_calls()) == 1
    assert await rows(hass, "home_consumption") == []


# ---------------------------------------------------------------------------
# 41. Services


async def test_refetch_service(
    hass: HomeAssistant, env: Any, patched: MagicMock, client: FakeClient
) -> None:
    """``refetch`` runs the given range in the background."""

    entry = await loaded_entry(hass)
    await hass.services.async_call(
        DOMAIN,
        "refetch",
        {
            "config_entry_id": entry.entry_id,
            "start_date": "2026-05-30",
            "end_date": "2026-06-02",
        },
        blocking=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    summary = entry.runtime_data.data
    assert summary.run_type is RunType.MANUAL
    assert summary.status is RunStatus.OK
    assert summary.window == (date(2026, 5, 30), date(2026, 6, 2))
    assert summary.chunks == 2
    assert {(c[2], c[3]) for c in client.analysis_calls()} == {
        (date(2026, 5, 30), date(2026, 5, 31)),
        (date(2026, 6, 1), date(2026, 6, 2)),
    }
    rows_ = await rows(hass, "home_generation")
    assert _hour(rows_[0]) == local_day_start(date(2026, 5, 30))
    assert _hour(rows_[-1]) == local_day_start(date(2026, 6, 3)) - timedelta(hours=1)


async def test_refetch_service_defaults(
    hass: HomeAssistant, env: Any, patched: MagicMock, client: FakeClient
) -> None:
    """Without dates: the daily window length, up to today."""

    entry = await loaded_entry(hass)
    with patch(
        "custom_components.eg_austria.importer.async_import_window",
        AsyncMock(return_value={}),
    ):
        await hass.services.async_call(
            DOMAIN, "refetch", {"config_entry_id": entry.entry_id}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        assert entry.runtime_data.data.window == (date(2026, 4, 7), TODAY)
        await hass.services.async_call(
            DOMAIN,
            "refetch",
            {"config_entry_id": entry.entry_id, "end_date": "2026-05-31"},
            blocking=True,
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        summary = entry.runtime_data.data
        assert summary.window == (date(2026, 3, 30), date(2026, 5, 31))
        assert summary.run_type is RunType.MANUAL


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({"start_date": "2026-06-05", "end_date": "2026-06-01"}, "invalid_range"),
        ({"end_date": "2026-06-09"}, "invalid_range"),
    ],
)
async def test_refetch_service_validation(
    hass: HomeAssistant,
    env: Any,
    patched: MagicMock,
    client: FakeClient,
    data: dict[str, str],
    key: str,
) -> None:
    """Bad ranges are refused before any request."""

    entry = await loaded_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "refetch",
            {"config_entry_id": entry.entry_id, **data},
            blocking=True,
        )
    assert err.value.translation_key == key
    assert client.calls == []


async def test_refetch_range_too_long(
    hass: HomeAssistant,
    env: FrozenDateTimeFactory,
    patched: MagicMock,
    client: FakeClient,
) -> None:
    """More than 400 days are refused."""

    entry = await loaded_entry(hass)
    env.move_to(datetime(2027, 3, 1, 12, 0, tzinfo=UTC))
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "refetch",
            {
                "config_entry_id": entry.entry_id,
                "start_date": "2026-01-01",
                "end_date": "2027-02-15",
            },
            blocking=True,
        )
    assert err.value.translation_key == "range_too_long"
    assert err.value.translation_placeholders == {"days": "400"}
    assert client.calls == []


async def test_services_need_a_loaded_entry(
    hass: HomeAssistant, env: Any, patched: MagicMock
) -> None:
    """Unknown or unloaded entries are refused."""

    entry = await loaded_entry(hass)
    for service in ("refetch", "release_held"):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN, service, {"config_entry_id": "nope"}, blocking=True
            )
        assert err.value.translation_key == "entry_not_found"
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "refetch", {"config_entry_id": entry.entry_id}, blocking=True
        )
    assert err.value.translation_key == "entry_not_loaded"


async def test_refetch_while_running(
    hass: HomeAssistant, env: Any, patched: MagicMock, client: FakeClient
) -> None:
    """A second refetch during a run is refused, not queued."""

    entry = await loaded_entry(hass)
    gate, started = asyncio.Event(), asyncio.Event()

    async def before(name: str) -> None:
        started.set()
        await gate.wait()

    client.before = before
    call = {"config_entry_id": entry.entry_id, "start_date": "2026-06-01"}
    await hass.services.async_call(DOMAIN, "refetch", call, blocking=True)
    await started.wait()
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "refetch", call, blocking=True)
    assert err.value.translation_key == "run_in_progress"
    gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.data.status is RunStatus.OK


async def test_history_hold_and_release_held(
    hass: HomeAssistant, env: Any, patched: MagicMock, client: FakeClient
) -> None:
    """H1 holds a retroactively zeroed day; ``release_held`` lets it through."""

    entry = await loaded_entry(hass)
    coordinator = entry.runtime_data
    call = {
        "config_entry_id": entry.entry_id,
        "start_date": "2026-06-01",
        "end_date": "2026-06-07",
    }
    await hass.services.async_call(DOMAIN, "refetch", call, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data.status is RunStatus.OK
    day = date(2026, 6, 3)
    before = [r for r in await rows(hass, "home_consumption") if r["state"]]
    day_start, day_end = local_day_start(day), local_day_start(day + timedelta(days=1))

    # The portal now sends 0 for every hour of a stored, complete day.
    client.meter_missing = {day}
    await hass.services.async_call(DOMAIN, "refetch", call, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    summary = coordinator.data
    assert summary.status is RunStatus.PARTIAL
    assert summary.issues.implausible_data is True
    assert "H1" in summary.validation.rule_ids()
    assert coordinator.state.held_days["home_consumption"] == [day]
    assert summary.blocked["home_consumption"] == [day]
    after = await rows(hass, "home_consumption")
    kept = [r for r in after if day_start <= _hour(r) < day_end]
    assert kept
    assert all(r["state"] for r in kept)  # stored rows stay
    assert len([r for r in after if r["state"]]) == len(before)

    # Release one statistic; the next run does not hold it again.
    await hass.services.async_call(
        DOMAIN,
        "release_held",
        {"config_entry_id": entry.entry_id, "statistic_key": "home_consumption"},
        blocking=True,
    )
    assert "home_consumption" not in coordinator.state.held_days
    assert coordinator.state.released == {"home_consumption": [day]}
    assert "home_generation" in coordinator.state.held_days
    await hass.services.async_call(DOMAIN, "refetch", call, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    summary = coordinator.data
    assert "home_consumption" not in summary.blocked
    assert summary.blocked["home_generation"] == [day]
    assert coordinator.state.released == {}

    # Release everything (no key, given date).
    await hass.services.async_call(
        DOMAIN,
        "release_held",
        {"config_entry_id": entry.entry_id, "date": "2026-06-03"},
        blocking=True,
    )
    assert coordinator.state.held_days == {}


async def test_statistic_labels_translated(
    hass: HomeAssistant, env: Any, patched: MagicMock, client: FakeClient
) -> None:
    """Display names use the configured language (spec 04)."""

    hass.config.language = "de"
    entry = await loaded_entry(hass)
    await hass.services.async_call(
        DOMAIN,
        "refetch",
        {"config_entry_id": entry.entry_id, "start_date": "2026-06-07"},
        blocking=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    metadata = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_source=DOMAIN)
    )
    names = {sid: meta["name"] for sid, (_, meta) in metadata.items()}
    assert names["eg_austria:home_consumption"] == "Zuhause Bezug"
    assert (
        names["eg_austria:home_eeg_consumption_from_community"]
        == "Example EEG Bezug aus Gemeinschaft"
    )
