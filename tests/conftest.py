"""Shared fixtures for eg_austria tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import importlib.util
import json
import logging
from pathlib import Path
import secrets
import sys
from typing import Any

from multidict import CIMultiDict
import pytest

from custom_components.eg_austria.api import MIN_INTERVAL
from custom_components.eg_austria.models import (
    parse_analysis,
    parse_day_completeness,
    parse_maintenance,
    parse_metering_points,
    parse_version,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load integrations from custom_components/."""
    return


@pytest.fixture(autouse=True)
def quiet_sqlalchemy() -> None:
    """Undo the plugin's SQL echo logging (thousands of rows per import run)."""
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def load_fixture(name: str) -> Any:
    """Return a synthetic fixture as JSON (``None`` for an empty body)."""

    text = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(text) if text.strip() else None


def fixture_bytes(name: str) -> bytes:
    """Return a fixture's raw bytes (for mocked response bodies)."""

    return (FIXTURES / name).read_bytes()


class FakeClock:
    """Monotonic clock, sleep and UTC now under test control."""

    def __init__(self, now: datetime) -> None:
        """Start at ``now``; ``sleep`` advances both clocks."""
        self.mono = 1000.0
        self.now = now
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        """Return the fake monotonic time."""
        return self.mono

    async def sleep(self, seconds: float) -> None:
        """Record the sleep and advance time without waiting."""
        self.sleeps.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        """Move both clocks forward."""
        self.mono += seconds
        self.now += timedelta(seconds=seconds)

    def utcnow(self) -> datetime:
        """Return the fake UTC now."""
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    """Fake time, frozen at a synthetic date."""

    return FakeClock(datetime(2026, 6, 8, 4, 0, tzinfo=UTC))


@dataclass
class FakeResponse:
    """The part of ``aiohttp.ClientResponse`` the client uses."""

    status: int
    body: bytes
    content_type: str
    headers: CIMultiDict = field(default_factory=CIMultiDict)

    async def read(self) -> bytes:
        """Return the body."""
        return self.body


class _Ctx:
    def __init__(self, item: FakeResponse | BaseException) -> None:
        self._item = item

    async def __aenter__(self) -> FakeResponse:
        if isinstance(self._item, BaseException):
            raise self._item
        return self._item

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakePortal:
    """Stands in for the aiohttp session: queued responses, recorded calls.

    It only has ``get``; a client calling any other verb fails with
    ``AttributeError``. Nothing touches the network.
    """

    def __init__(self) -> None:
        """Start with an empty queue."""
        self.queue: list[FakeResponse | BaseException] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def add(
        self,
        *,
        status: int = 200,
        body: bytes = b"[]",
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
        exception: BaseException | None = None,
    ) -> None:
        """Queue one response (or an exception raised on send)."""
        if exception is not None:
            self.queue.append(exception)
            return
        self.queue.append(
            FakeResponse(status, body, content_type, CIMultiDict(headers or {}))
        )

    def get(self, url: object, **kwargs: Any) -> _Ctx:
        """Record a GET and return the next queued response."""
        self.calls.append(("GET", str(url), kwargs))
        if not self.queue:
            raise AssertionError("unexpected request")
        return _Ctx(self.queue.pop(0))


@pytest.fixture
def mock_portal() -> FakePortal:
    """Fake portal session; nothing reaches the network."""

    return FakePortal()


# ---------------------------------------------------------------------------
# S5: client double for coordinator and integration tests


def _generator() -> Any:
    """Return ``tests/fixtures/generate.py`` as a module (loaded once)."""

    name = "fixture_generate"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, FIXTURES / "generate.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class FakeClient:
    """Stands in for ``EgAustriaClient`` (same public methods, no HTTP).

    ``analysis-data`` and ``day-completeness`` come from the fixture generator
    for whatever range is asked, so any window can be tested. Knobs:

    * ``incomplete``: days flagged incomplete (and community-incomplete);
    * ``meter_missing``: days with meter values ``0`` + faulty flag;
    * ``future_from``: completeness null from this day on;
    * ``errors``: method name → exception raised on every call;
    * ``mutate``: ``fn(raw, aggregation, start, end, community_id)`` returning
      the raw response to parse (for bad-data cases);
    * ``before``: awaited before every request (concurrency/unload tests).

    Like the portal, a request for a community outside its activation (any
    day before ``begin`` or after ``end`` of every selected metering point)
    gets a reduced body without ``totalConsumption`` (issue #16), and the
    residual only subtracts the communities active on the request's first
    day.
    """

    def __init__(self) -> None:
        """Start with normal synthetic data."""

        self.gen = _generator()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.request_count = 0
        self.min_interval = MIN_INTERVAL
        self.intervals: list[float] = []
        self.maintenance_raw: Any = None
        self.version_raw: Any = load_fixture("version_a.json")
        self.mp_raw: Any = load_fixture("metering_point_single.json")
        self.incomplete: set[Any] = set()
        self.meter_missing: set[Any] = set()
        self.future_from: Any = None
        self.errors: dict[str, BaseException] = {}
        self.mutate: Any = None
        self.before: Any = None

    async def _call(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        self.request_count += 1
        self.intervals.append(self.min_interval)
        if self.before is not None:
            await self.before(name)
        if name in self.errors:
            raise self.errors[name]

    def analysis_calls(self, aggregation: str | None = None) -> list[tuple[Any, ...]]:
        """Arguments of every ``get_analysis`` call (optionally one aggregation)."""
        return [
            a
            for n, a in self.calls
            if n == "get_analysis" and (aggregation is None or a[1] == aggregation)
        ]

    async def get_maintenance(self) -> Any:
        """Return the parsed maintenance list."""

        await self._call("get_maintenance")
        return parse_maintenance(self.maintenance_raw)

    async def get_version(self) -> Any:
        """Return the parsed version."""

        await self._call("get_version")
        return parse_version(self.version_raw)

    async def get_metering_points(self, member: str) -> Any:
        """Return the parsed metering points."""

        await self._call("get_metering_points", member)
        return parse_metering_points(self.mp_raw)

    async def get_day_completeness(
        self, member: str, start: Any, end: Any, cids: Any, mps: Any
    ) -> Any:
        """Return generated completeness for the range."""

        await self._call("get_day_completeness", member, start, end, cids, mps)
        raw = self.gen.completeness(start, end, self.incomplete, self.future_from)
        return parse_day_completeness(raw)

    def _members(self, mps: Any) -> Any:
        """Return ``day -> communities any of mps is active in`` (``mp_raw``)."""

        acts = [
            act
            for mp, act in parse_metering_points(self.mp_raw).iter_activations()
            if mp.id in mps
        ]

        def on(day: Any) -> frozenset[int]:
            return frozenset(
                a.community_id
                for a in acts
                if a.active_from <= day and (a.active_to is None or day <= a.active_to)
            )

        return on

    async def get_analysis(
        self, member: str, aggregation: Any, start: Any, end: Any, cids: Any, mps: Any
    ) -> Any:
        """Return generated analysis-data for one community and range."""

        await self._call("get_analysis", member, str(aggregation), start, end, cids)
        assert len(cids) == 1, "one community per request (spec 03)"
        members = self._members(mps)
        scenario = self.gen.Scenario(
            start,
            end,
            cids[0],
            aggregation=str(aggregation),
            community_incomplete=set(self.incomplete),
            meter_missing=set(self.meter_missing),
            members=members(start),
        )
        raw = self.gen.analysis(scenario)
        if not all(cids[0] in members(d) for d in self.gen.days(start, end)):
            raw.pop("totalConsumption", None)  # reduced body (issue #16)
        if self.mutate is not None:
            raw = self.mutate(raw, str(aggregation), start, end, cids[0])
        return parse_analysis(raw)


ENTRY_DATA_BASE: dict[str, Any] = {
    "token_kind": "personal",
    "member_number": "9000001",
    "location_id": "5001",
    "mp_ids": [1001, 1002],
    "communities": [
        {"id": 2001, "slug": "beg", "name": "Example BEG"},
        {"id": 2002, "slug": "eeg", "name": "Example EEG"},
    ],
    "retired_slugs": [],
    "site_slug": "home",
    "run_minute": 240,
}


def entry_data(token_exp: int, **overrides: Any) -> dict[str, Any]:
    """Config entry data with a random synthetic personal token."""

    return {
        **ENTRY_DATA_BASE,
        "token": "ecapi_" + secrets.token_hex(24),
        "token_exp": token_exp,
        **overrides,
    }


# ---------------------------------------------------------------------------
# S6: a set-up entry with the client factory patched (repairs, sensors,
# diagnostics). Nothing reaches the network.


@dataclass
class LoadedEntry:
    """An entry set up with ``FakeClient``; ``coordinator`` = runtime data."""

    entry: Any
    client: FakeClient

    @property
    def coordinator(self) -> Any:
        """Return the entry's coordinator."""
        return self.entry.runtime_data


async def async_setup_fake_entry(
    hass: Any,
    *,
    token_exp: int,
    client: FakeClient | None = None,
    store: dict[str, Any] | None = None,
    **data: Any,
) -> LoadedEntry:
    """Add and set up an entry whose client is ``client`` (no startup run).

    The store marks the initial import done and a recent success unless
    ``store`` says otherwise, so no run starts by itself.
    """

    from unittest.mock import AsyncMock, MagicMock, patch

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.eg_austria.config_flow import default_options
    from custom_components.eg_austria.const import DOMAIN
    from custom_components.eg_austria.coordinator import entry_store

    client = client or FakeClient()
    session = MagicMock()
    session.close = AsyncMock()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EG Austria Home",
        data=entry_data(token_exp, **data),
        options=default_options(),
    )
    entry.add_to_hass(hass)
    now = dt_util.utcnow()
    await entry_store(hass, entry.entry_id).async_save(
        store
        if store is not None
        else {
            "initial_done": True,
            "last_success": (now - timedelta(hours=2)).isoformat(),
            "last_long_success": (now - timedelta(hours=2)).isoformat(),
        }
    )
    with (
        patch("custom_components.eg_austria.EgAustriaClient", return_value=client),
        patch("custom_components.eg_austria.get_portal_session", return_value=session),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return LoadedEntry(entry, client)
