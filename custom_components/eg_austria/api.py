"""Read-only client for the EG Austria member-portal API (spec 01, ADR 0002).

Safety by construction:

* one public method per whitelisted endpoint; there is no generic request
  method, and ``_get()`` only accepts path templates from ``WHITELIST``;
* the HTTP method is the constant ``GET`` (``session.get``); no other verb is
  referenced anywhere in this module;
* the token is sent explicitly per request and the session has no cookie jar,
  so ``Set-Cookie`` responses are ignored;
* logs show path templates (``{member}``), never headers, tokens or ids.

Seams for later slices: the coordinator (S5) owns scheduling, chunking of
``analysis-data`` windows and the ``min_interval`` doubling after a 429; the
config flow (S2) uses ``get_session()`` and ``get_metering_points()``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
import json
import logging
import re
import time
from typing import Any, Final

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from yarl import URL

from .const import VERSION
from .exceptions import (
    ApiChangedError,
    AuthError,
    ForbiddenError,
    NotWhitelistedError,
    PortalUnavailableError,
    RateLimitedError,
    StaleClientError,
    TokenExpiredError,
)
from .models import (
    Aggregation,
    AnalysisData,
    DayCompleteness,
    Maintenance,
    MeteringPointInfo,
    PortalVersion,
    SessionInfo,
    parse_analysis,
    parse_day_completeness,
    parse_maintenance,
    parse_metering_points,
    parse_session,
    parse_version,
)
from .redact import redact_text
from .token import TokenKind, jwt_expiry

_LOGGER = logging.getLogger(__name__)

BASE_URL: Final = "https://portal.eg-austria.at/api"
REPO_URL: Final = "https://github.com/magicdude4eva/ha-eg-austria"
USER_AGENT: Final = f"ha-eg-austria/{VERSION} (+{REPO_URL})"
COOKIE_NAME: Final = "portal.jwtToken"

REQUEST_TIMEOUT: Final = 60.0
MIN_INTERVAL: Final = 2.0
RETRY_DELAY: Final = 30.0
RETRY_STATUSES: Final = frozenset((502, 503, 504))
# Hour/FifteenMinutes: end - start <= 30 days (31 local days, both inclusive).
MAX_FINE_RANGE_DAYS: Final = 30
COMPLETENESS_CHUNK_MONTHS: Final = 13

PATH_METERING_POINT: Final = "/v1/portal/member/{member}/metering-point"
PATH_ANALYSIS: Final = "/v1/portal/member/{member}/analysis-data"
PATH_DAY_COMPLETENESS: Final = (
    "/v1/portal/member/{member}/analysis-data/day-completeness"
)
PATH_MAINTENANCE: Final = "/v1/portal/maintenance"
PATH_VERSION: Final = "/v1/version"
PATH_SESSION: Final = "/v1/portal/portal-session"

# ADR 0002: the only (method, path template) pairs this client can send.
WHITELIST: Final[frozenset[tuple[str, str]]] = frozenset(
    ("GET", path)
    for path in (
        PATH_METERING_POINT,
        PATH_ANALYSIS,
        PATH_DAY_COMPLETENESS,
        PATH_MAINTENANCE,
        PATH_VERSION,
        PATH_SESSION,
    )
)
_METHOD: Final = "GET"

_MEMBER_RE = re.compile(r"\d{1,20}")
_FINE = (Aggregation.HOUR, Aggregation.FIFTEEN_MINUTES)
# 403 bodies that are about the credential (reauth) rather than access rights.
_AUTH_WORDS = ("token", "auth", "jwt", "session", "login", "anmeld")
_JSON_TYPES = ("application/json", "application/problem+json")


def create_portal_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Create the dedicated session: HA's factory, no cookie jar (ADR 0007)."""

    return async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    # Clamp to the month's last day.
    for dd in (day.day, 30, 29, 28):
        try:
            return date(year, month, dd)
        except ValueError:
            continue
    raise AssertionError("unreachable")  # pragma: no cover


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class EgAustriaClient:
    """Async, strictly sequential, read-only client.

    ``token_exp`` is the stored expiry (Unix seconds). For a session JWT it
    defaults to the token's ``exp`` claim; for a personal token it is the
    user's stated expiry (config flow). Past the expiry no request is sent.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        kind: TokenKind,
        *,
        token_exp: int | None = None,
        min_interval: float = MIN_INTERVAL,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Set up the client; nothing is sent until a method is called."""

        self._session = session
        self._kind = TokenKind(kind)
        self._token = token
        if token_exp is None and self._kind is TokenKind.SESSION:
            token_exp = jwt_expiry(token)
        self.token_exp = token_exp
        self.min_interval = min_interval
        self._monotonic = monotonic
        self._sleep = sleep
        self._utcnow = utcnow
        self._lock = asyncio.Lock()
        self._last_start: float | None = None
        self._sensitive: set[str] = set()
        self._maintenance: list[Maintenance] = []
        self.request_count = 0

    def __repr__(self) -> str:
        """Never show the token."""

        return f"EgAustriaClient(kind={self._kind.value})"

    # ------------------------------------------------------------------
    # Public endpoints (one per whitelist entry)

    async def get_session(self) -> SessionInfo:
        """``GET portal-session``: the members this token may act for."""

        return parse_session(await self._get(PATH_SESSION))

    async def get_metering_points(self, member: str) -> MeteringPointInfo:
        """``GET metering-point`` for a member."""

        return parse_metering_points(
            await self._get(PATH_METERING_POINT, member=member)
        )

    async def get_analysis(
        self,
        member: str,
        aggregation: Aggregation,
        start: date,
        end: date,
        community_ids: Sequence[int],
        mp_ids: Sequence[int],
    ) -> AnalysisData:
        """``GET analysis-data`` for local dates ``start``..``end`` (inclusive)."""

        aggregation = Aggregation(aggregation)
        self._check_range(start, end)
        if aggregation in _FINE and (end - start).days > MAX_FINE_RANGE_DAYS:
            raise ValueError(
                f"{aggregation.value} range is limited to "
                f"{MAX_FINE_RANGE_DAYS + 1} days"
            )
        params = [
            ("aggregation", aggregation.value),
            ("startDate", start.isoformat()),
            ("endDate", end.isoformat()),
            *self._id_params(community_ids, mp_ids),
        ]
        return parse_analysis(await self._get(PATH_ANALYSIS, params, member=member))

    async def get_day_completeness(
        self,
        member: str,
        start: date,
        end: date,
        community_ids: Sequence[int],
        mp_ids: Sequence[int],
    ) -> list[DayCompleteness]:
        """``GET day-completeness``; long ranges are chunked to 13 months."""

        self._check_range(start, end)
        ids = self._id_params(community_ids, mp_ids)
        result: list[DayCompleteness] = []
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(
                _add_months(chunk_start, COMPLETENESS_CHUNK_MONTHS) - timedelta(days=1),
                end,
            )
            params = [
                ("startDate", chunk_start.isoformat()),
                ("endDate", chunk_end.isoformat()),
                *ids,
            ]
            result.extend(
                parse_day_completeness(
                    await self._get(PATH_DAY_COMPLETENESS, params, member=member)
                )
            )
            chunk_start = chunk_end + timedelta(days=1)
        return result

    async def get_maintenance(self) -> list[Maintenance]:
        """``GET maintenance``; an empty body means no announced window."""

        windows = parse_maintenance(await self._get(PATH_MAINTENANCE, allow_empty=True))
        self._maintenance = windows
        return windows

    async def get_version(self) -> PortalVersion:
        """``GET version``."""

        return parse_version(await self._get(PATH_VERSION))

    # ------------------------------------------------------------------
    # Helpers

    def maintenance_active(self, now: datetime | None = None) -> bool:
        """Return True if the last fetched maintenance list covers ``now``."""

        now = now or self._utcnow()
        return any(w.active_at(now) for w in self._maintenance)

    @staticmethod
    def _check_range(start: date, end: date) -> None:
        if end < start:
            raise ValueError("end date before start date")

    def _id_params(
        self, community_ids: Sequence[int], mp_ids: Sequence[int]
    ) -> list[tuple[str, str]]:
        # Always explicit (spec 01): results must not change when the member
        # joins another community.
        if not community_ids or not mp_ids:
            raise ValueError("community_ids and mp_ids are required")
        params = []
        for name, values in (("energyCommunityIds", community_ids), ("mpIds", mp_ids)):
            for value in values:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise TypeError(f"{name} must be integers")
                self._sensitive.add(str(value))
                params.append((name, str(value)))
        return params

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self._kind is TokenKind.PERSONAL:
            headers["Authorization"] = f"Bearer {self._token}"
        else:
            headers["Cookie"] = f"{COOKIE_NAME}={self._token}"
        return headers

    def _redact(self, text: str) -> str:
        return redact_text(text, self._sensitive)

    async def _space(self) -> None:
        if self._last_start is not None:
            wait = self._last_start + self.min_interval - self._monotonic()
            if wait > 0:
                await self._sleep(wait)
        self._last_start = self._monotonic()

    async def _get(
        self,
        template: str,
        params: Iterable[tuple[str, str]] = (),
        *,
        allow_empty: bool = False,
        member: str | None = None,
    ) -> Any:
        """Send one whitelisted GET and return the decoded JSON body."""

        if (_METHOD, template) not in WHITELIST:
            raise NotWhitelistedError("path template is not whitelisted")
        path_args: dict[str, str] = {}
        if "{member}" in template:
            if member is None or not _MEMBER_RE.fullmatch(str(member)):
                raise ValueError("member number must be digits")
            path_args["member"] = str(member)
            self._sensitive.add(str(member))
        elif member is not None:
            raise ValueError("this endpoint takes no member number")
        url = URL(BASE_URL + template.format(**path_args)).with_query(list(params))

        async with self._lock:
            for attempt in (1, 2):
                try:
                    return await self._send(url, template, allow_empty=allow_empty)
                except PortalUnavailableError as err:
                    if attempt == 2 or not err.retryable:
                        raise
                    _LOGGER.debug(
                        "GET %s: %s, retrying in %.0f s", template, err, RETRY_DELAY
                    )
                    await self._sleep(RETRY_DELAY)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _send(self, url: URL, template: str, *, allow_empty: bool) -> Any:
        if self.token_exp is not None and self._utcnow().timestamp() >= self.token_exp:
            raise TokenExpiredError("stored token expiry has passed")
        await self._space()
        self.request_count += 1
        try:
            async with self._session.get(
                url,
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                allow_redirects=False,
            ) as resp:
                status = resp.status
                content_type = resp.content_type
                retry_after = resp.headers.get("Retry-After")
                body = await resp.read()
        except TimeoutError as err:
            raise PortalUnavailableError("request timed out", retryable=True) from err
        except aiohttp.ClientError as err:
            raise PortalUnavailableError(
                f"connection error ({type(err).__name__})", retryable=True
            ) from err

        _LOGGER.debug("GET %s -> %s (%d bytes)", template, status, len(body))
        if status >= 300:
            self._raise_for_status(status, body, retry_after)
        if not body.strip():
            if allow_empty:
                return None
            raise self._not_json("empty body")
        if content_type not in _JSON_TYPES:
            raise self._not_json(f"unexpected content type {content_type!r}")
        try:
            return json.loads(body)
        except ValueError as err:
            raise self._not_json("invalid JSON") from err

    def _not_json(self, reason: str) -> Exception:
        if self.maintenance_active():
            return PortalUnavailableError(f"{reason} during maintenance")
        return ApiChangedError(reason)

    def _raise_for_status(
        self, status: int, body: bytes, retry_after: str | None
    ) -> None:
        detail = self._error_detail(body)
        message = f"HTTP {status}" + (f": {detail}" if detail else "")
        if detail:
            _LOGGER.debug("Portal error body: %s", detail)
        if status == 401:
            raise AuthError(message)
        if status == 403:
            if any(word in detail.lower() for word in _AUTH_WORDS):
                raise AuthError(message)
            raise ForbiddenError(message)
        if status == 429:
            raise RateLimitedError(message, retry_after=_retry_after(retry_after))
        if status >= 500:
            raise PortalUnavailableError(
                message, status=status, retryable=status in RETRY_STATUSES
            )
        if status == 400 and "aktualisiert" in detail.lower():
            raise StaleClientError(message, status=status)
        # Other 4xx (400, 404, 405, 422, ...) and unexpected 3xx redirects.
        raise ApiChangedError(message, status=status)

    def _error_detail(self, body: bytes) -> str:
        """Return the redacted ``detail``/``message`` (never ``instance``)."""

        try:
            data = json.loads(body) if body.strip() else None
        except ValueError:
            return ""
        if not isinstance(data, dict):
            return ""
        texts = []
        for key in ("detail", "message"):
            value = data.get(key)
            if isinstance(value, str) and value and value not in texts:
                texts.append(value)
        return self._redact(" / ".join(texts))[:300]
