"""Repair issues (test plan case 37, spec 06, ADR 0006/0007).

Every issue is created and cleared on its condition through the coordinator
hooks of S5. ``FakeClient`` stands in for the portal; statistics writes are
patched out (only validation decides about the issues).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.eg_austria.const import CONF_SURPLUS_PRICE, DOMAIN
from custom_components.eg_austria.coordinator import RunRequest
from custom_components.eg_austria.exceptions import (
    AuthError,
    PortalUnavailableError,
    StaleClientError,
)
from custom_components.eg_austria.importer import RunStatus, RunType
from custom_components.eg_austria.repairs import (
    ISSUE_API_CHANGED,
    ISSUE_IMPLAUSIBLE,
    ISSUE_PRICE_MISSING,
    ISSUE_TOKEN_EXPIRING,
    ISSUE_UNREACHABLE,
    EntryRepairs,
    _date_range,
    issue_id,
    token_expiry,
    token_warning_start,
)
from custom_components.eg_austria.statistics import WriteResult

from .conftest import LoadedEntry, async_setup_fake_entry

NOW = datetime(2026, 6, 8, 4, 0, tzinfo=UTC)  # 06:00 in Vienna
DAY = 86400


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before ``hass`` (see test_statistics.py)."""


@pytest.fixture(autouse=True)
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


def issue(hass: HomeAssistant, name: str, loaded: LoadedEntry) -> ir.IssueEntry | None:
    """Return the entry's issue ``name`` or None."""

    return ir.async_get(hass).async_get_issue(
        DOMAIN, issue_id(name, loaded.entry.entry_id)
    )


async def setup(hass: HomeAssistant, exp_days: float = 300, **data: Any) -> LoadedEntry:
    """Set up an entry whose token expires in ``exp_days``."""

    return await async_setup_fake_entry(
        hass, token_exp=int(NOW.timestamp() + exp_days * DAY), **data
    )


async def run(loaded: LoadedEntry) -> Any:
    """One daily run; returns the summary."""

    summary = await loaded.coordinator.async_run(RunRequest(RunType.DAILY))
    await loaded.coordinator.hass.async_block_till_done()
    return summary


def _not_an_object(raw: Any, agg: str, start: date, end: date, cid: int) -> Any:
    return [raw]  # S1: shape failure


def _negative_june(raw: Any, agg: str, start: date, end: date, cid: int) -> Any:
    if start.month == 6 and cid == 2001:
        for point in raw["generationDataPoints"]:
            if point["generation"]:
                point["generation"] = -abs(point["generation"])
                break
    return raw


# ---------------------------------------------------------------------------
# token_expiring (ADR 0007)


@pytest.mark.parametrize(
    ("kind", "quiet_days", "due_days"),
    [("personal", 31, 29), ("session", 8, 6)],
)
async def test_token_expiring_due_at_setup(
    hass: HomeAssistant, env: Any, kind: str, quiet_days: int, due_days: int
) -> None:
    """30 days (personal) or 7 days (session JWT) before the stored expiry."""

    quiet = await setup(hass, quiet_days, token_kind=kind)
    assert issue(hass, ISSUE_TOKEN_EXPIRING, quiet) is None
    due = await setup(hass, due_days, token_kind=kind)
    found = issue(hass, ISSUE_TOKEN_EXPIRING, due)
    assert found is not None
    assert found.severity is ir.IssueSeverity.WARNING
    assert not found.is_fixable
    assert found.translation_key == ISSUE_TOKEN_EXPIRING
    assert found.translation_placeholders == {
        "title": "EG Austria Home",
        "expires": (NOW + timedelta(days=due_days)).date().isoformat(),
        "days": str(due_days),
    }
    assert found.learn_more_url
    assert found.learn_more_url.startswith("https://github.com/")


async def test_token_expiring_raised_when_period_starts(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """No restart needed: a timer raises the issue when the period starts."""

    loaded = await setup(hass, 32)
    assert issue(hass, ISSUE_TOKEN_EXPIRING, loaded) is None
    env.move_to(NOW + timedelta(days=2, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert issue(hass, ISSUE_TOKEN_EXPIRING, loaded) is not None


async def test_token_expiring_timer_fires_between_runs(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """The timer itself raises the issue when no run happens meanwhile."""

    loaded = await setup(hass, 30 + 1 / 24)  # warning period starts in 1 h
    assert issue(hass, ISSUE_TOKEN_EXPIRING, loaded) is None
    calls = len(loaded.client.calls)
    env.move_to(NOW + timedelta(hours=1, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(loaded.client.calls) == calls  # no run raised it
    found = issue(hass, ISSUE_TOKEN_EXPIRING, loaded)
    assert found is not None
    assert found.translation_placeholders["days"] == "30"


@pytest.mark.parametrize(
    ("data", "warning_days"),
    [
        ({"token_exp": int(NOW.timestamp()), "token_kind": "personal"}, 30),
        ({"token_exp": int(NOW.timestamp()), "token_kind": "session"}, 7),
        # An unknown kind gets the shorter session warning.
        ({"token_exp": int(NOW.timestamp()), "token_kind": "other"}, 7),
        ({"token_exp": int(NOW.timestamp())}, 7),
    ],
)
def test_token_warning_start(data: dict[str, Any], warning_days: int) -> None:
    """Expiry minus 30 days (personal) or 7 days (session, unknown)."""

    entry = MockConfigEntry(domain=DOMAIN, data=data)
    assert token_expiry(entry) == NOW
    assert token_warning_start(entry) == NOW - timedelta(days=warning_days)


@pytest.mark.parametrize("exp", [None, True, "2026-06-08"])
def test_token_without_stored_expiry(exp: Any) -> None:
    """No usable stored expiry: no expiry and no warning start."""

    entry = MockConfigEntry(
        domain=DOMAIN, data={"token_exp": exp, "token_kind": "personal"}
    )
    assert token_expiry(entry) is None
    assert token_warning_start(entry) is None


async def test_token_check_without_expiry(hass: HomeAssistant, env: Any) -> None:
    """Without a stored expiry nothing is raised and no timer is set."""

    entry = MockConfigEntry(domain=DOMAIN, data={"token_kind": "personal"})
    entry.add_to_hass(hass)
    repairs = EntryRepairs(hass, entry, MagicMock())
    repairs.async_check_token()
    assert repairs._token_unsub is None
    assert not ir.async_get(hass).issues


def test_date_range() -> None:
    """Empty, one day, or first - last."""

    assert _date_range([]) == "-"
    assert _date_range([date(2026, 6, 1)] * 2) == "2026-06-01"
    assert _date_range([date(2026, 6, 3), date(2026, 6, 1)]) == (
        "2026-06-01 - 2026-06-03"
    )


async def test_token_expiring_cleared_by_new_token(
    hass: HomeAssistant, env: Any
) -> None:
    """Storing a new token (reauth or reconfigure) clears the issue."""

    loaded = await setup(hass, 5)
    assert issue(hass, ISSUE_TOKEN_EXPIRING, loaded) is not None
    # The config flow deletes the issue and reloads the entry (S2); the
    # reloaded entry must not raise it again for the new expiry.
    ir.async_delete_issue(
        hass, DOMAIN, issue_id(ISSUE_TOKEN_EXPIRING, loaded.entry.entry_id)
    )
    with (
        patch(
            "custom_components.eg_austria.EgAustriaClient", return_value=loaded.client
        ),
        patch(
            "custom_components.eg_austria.get_portal_session",
            return_value=MagicMock(close=AsyncMock()),
        ),
    ):
        hass.config_entries.async_update_entry(
            loaded.entry,
            data={**loaded.entry.data, "token_exp": int(NOW.timestamp()) + 365 * DAY},
        )
        assert await hass.config_entries.async_reload(loaded.entry.entry_id)
        await hass.async_block_till_done()
    assert issue(hass, ISSUE_TOKEN_EXPIRING, loaded) is None


async def test_token_timer_cancelled_on_unload(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """After unload the pending token check does not fire."""

    loaded = await setup(hass, 32)
    assert await hass.config_entries.async_unload(loaded.entry.entry_id)
    env.move_to(NOW + timedelta(days=3))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert issue(hass, ISSUE_TOKEN_EXPIRING, loaded) is None


# ---------------------------------------------------------------------------
# Reauth (core flow)


async def test_auth_failure_starts_reauth_without_issue(
    hass: HomeAssistant, env: Any
) -> None:
    """401 → core reauth flow; none of the integration's issues."""

    loaded = await setup(hass)
    loaded.client.errors["get_version"] = AuthError("HTTP 401")
    summary = await run(loaded)
    assert summary.status is RunStatus.AUTH_FAILED
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    for name in (ISSUE_API_CHANGED, ISSUE_IMPLAUSIBLE, ISSUE_UNREACHABLE):
        assert issue(hass, name, loaded) is None


# ---------------------------------------------------------------------------
# api_changed (ADR 0006)


async def test_api_changed_created_and_cleared(hass: HomeAssistant, env: Any) -> None:
    """Shape failure → error issue with the rule id; a valid run clears it."""

    loaded = await setup(hass)
    loaded.client.mutate = _not_an_object
    summary = await run(loaded)
    assert summary.status is RunStatus.API_CHANGED
    found = issue(hass, ISSUE_API_CHANGED, loaded)
    assert found is not None
    assert found.severity is ir.IssueSeverity.ERROR
    assert found.is_persistent
    assert found.translation_key == ISSUE_API_CHANGED
    assert found.translation_placeholders == {
        "title": "EG Austria Home",
        "rules": "S1",
        "date": "2026-06-08",
    }

    # Unreachable says nothing about api_changed: the issue stays.
    loaded.client.mutate = None
    loaded.client.errors["get_maintenance"] = PortalUnavailableError("HTTP 503")
    await run(loaded)
    assert issue(hass, ISSUE_API_CHANGED, loaded) is not None

    del loaded.client.errors["get_maintenance"]
    summary = await run(loaded)
    assert summary.status is RunStatus.OK
    assert issue(hass, ISSUE_API_CHANGED, loaded) is None


async def test_stale_client_text(hass: HomeAssistant, env: Any) -> None:
    """A "portal updated" 400 is api_changed with its own text."""

    loaded = await setup(hass)
    loaded.client.errors["get_version"] = StaleClientError("HTTP 400", status=400)
    await run(loaded)
    found = issue(hass, ISSUE_API_CHANGED, loaded)
    assert found is not None
    assert found.translation_key == "stale_client"
    assert found.translation_placeholders is not None
    assert found.translation_placeholders["rules"] == "-"


# ---------------------------------------------------------------------------
# implausible_data


async def test_implausible_created_and_cleared_by_run(
    hass: HomeAssistant, env: Any
) -> None:
    """A blocking plausibility rule → warning; the next clean run clears it."""

    loaded = await setup(hass)
    loaded.client.mutate = _negative_june
    summary = await run(loaded)
    assert summary.status is RunStatus.PARTIAL
    found = issue(hass, ISSUE_IMPLAUSIBLE, loaded)
    assert found is not None
    assert found.severity is ir.IssueSeverity.WARNING
    placeholders = found.translation_placeholders or {}
    assert "P1" in placeholders["rules"].split(", ")
    assert "home_generation" in placeholders["statistics"]
    assert placeholders["dates"] == "2026-06-01 - 2026-06-08"
    assert issue(hass, ISSUE_API_CHANGED, loaded) is None

    loaded.client.mutate = None
    await run(loaded)
    assert issue(hass, ISSUE_IMPLAUSIBLE, loaded) is None


async def test_implausible_cleared_by_release_held(
    hass: HomeAssistant, env: Any
) -> None:
    """``release_held`` with nothing left held clears the issue."""

    loaded = await setup(hass)
    loaded.client.mutate = _negative_june
    await run(loaded)
    assert issue(hass, ISSUE_IMPLAUSIBLE, loaded) is not None
    coordinator = loaded.coordinator
    coordinator.state.held_days = {
        "home_generation": [date(2026, 6, 2)],
        "home_consumption": [date(2026, 6, 3)],
    }
    # Releasing part of it keeps the issue.
    assert coordinator.async_release("home_generation", None) == 1
    assert issue(hass, ISSUE_IMPLAUSIBLE, loaded) is not None
    await hass.services.async_call(
        DOMAIN,
        "release_held",
        {"config_entry_id": loaded.entry.entry_id},
        blocking=True,
    )
    assert issue(hass, ISSUE_IMPLAUSIBLE, loaded) is None


# ---------------------------------------------------------------------------
# portal_unreachable


async def test_portal_unreachable_created_and_cleared(
    hass: HomeAssistant, env: FrozenDateTimeFactory
) -> None:
    """Third calendar day without success → warning; a success clears it."""

    loaded = await setup(hass)
    loaded.client.errors["get_maintenance"] = PortalUnavailableError("timeout")
    for day in range(3):
        env.move_to(NOW + timedelta(days=day))
        await run(loaded)
        expected = day == 2
        assert (issue(hass, ISSUE_UNREACHABLE, loaded) is not None) is expected
    found = issue(hass, ISSUE_UNREACHABLE, loaded)
    assert found is not None
    assert found.translation_placeholders == {
        "title": "EG Austria Home",
        "days": "3",
        "last_success": "2026-06-08",
    }
    del loaded.client.errors["get_maintenance"]
    await run(loaded)
    assert issue(hass, ISSUE_UNREACHABLE, loaded) is None


async def test_maintenance_skip_raises_nothing(hass: HomeAssistant, env: Any) -> None:
    """An announced maintenance window is no repair (spec 06)."""

    loaded = await setup(hass)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    loaded.client.maintenance_raw = [
        {
            "start": (NOW - timedelta(hours=1)).strftime(fmt),
            "end": (NOW + timedelta(hours=1)).strftime(fmt),
        }
    ]
    summary = await run(loaded)
    assert summary.status is RunStatus.SKIPPED_MAINTENANCE
    assert [k for k in ir.async_get(hass).issues if k[0] == DOMAIN] == []


# ---------------------------------------------------------------------------
# Entry removal


async def test_remove_entry_deletes_issues(hass: HomeAssistant, env: Any) -> None:
    """Removing the entry deletes all of its issues."""

    loaded = await setup(hass, 3)
    loaded.client.mutate = _not_an_object
    await run(loaded)
    assert issue(hass, ISSUE_TOKEN_EXPIRING, loaded) is not None
    assert issue(hass, ISSUE_API_CHANGED, loaded) is not None
    await hass.config_entries.async_remove(loaded.entry.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, ISSUE_TOKEN_EXPIRING, loaded) is None
    assert issue(hass, ISSUE_API_CHANGED, loaded) is None


async def test_price_missing_created_and_cleared(
    hass: HomeAssistant, env: FrozenDateTimeFactory, no_writes: AsyncMock
) -> None:
    """Surplus kWh without a price → warning; a run with prices clears it."""

    price = {"source": "number", "periods": [["2020-01-01", 8.0]]}
    loaded = await async_setup_fake_entry(
        hass,
        token_exp=int(NOW.timestamp() + 300 * DAY),
        options={CONF_SURPLUS_PRICE: price},
    )
    hour = datetime(2026, 5, 2, 10, tzinfo=UTC)
    no_writes.return_value = {
        "home_surplus_eur": WriteResult(key="home_surplus_eur", unpriced=[hour])
    }
    summary = await run(loaded)
    assert summary.price_missing_from == date(2026, 5, 2)
    found = issue(hass, ISSUE_PRICE_MISSING, loaded)
    assert found is not None
    assert found.translation_placeholders == {
        "title": "EG Austria Home",
        "date": "2026-05-02",
    }
    no_writes.return_value = {"home_surplus_eur": WriteResult(key="home_surplus_eur")}
    summary = await run(loaded)
    assert summary.price_missing_from is None
    assert issue(hass, ISSUE_PRICE_MISSING, loaded) is None
