"""Shared fixtures for eg_austria tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any

from multidict import CIMultiDict
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load integrations from custom_components/."""
    return


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
