"""Repair issues (spec 06, ADR 0006/0007).

The coordinator (S5) says per run whether a repair is raised, cleared or not
affected (``RunSummary.issues``); ``EntryRepairs`` turns that into issue
registry entries. ``token_expiring`` depends only on the stored expiry and is
checked at setup, after every run and at the moment the warning period
starts. Reauth itself is the core flow, started by the coordinator.

Issue ids are ``<issue>_<entry_id>``. Placeholders contain only the entry
title, rule ids, statistic keys, counts and dates (spec 06).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import CONF_TOKEN_EXP, CONF_TOKEN_KIND, DOMAIN
from .token import TokenKind

if TYPE_CHECKING:
    from .coordinator import EgAustriaCoordinator, RunSummary

ISSUE_TOKEN_EXPIRING: Final = "token_expiring"  # noqa: S105 - issue name
ISSUE_API_CHANGED: Final = "api_changed"
ISSUE_IMPLAUSIBLE: Final = "implausible_data"
ISSUE_UNREACHABLE: Final = "portal_unreachable"
ISSUE_PRICE_MISSING: Final = "price_missing"
ALL_ISSUES: Final = (
    ISSUE_TOKEN_EXPIRING,
    ISSUE_API_CHANGED,
    ISSUE_IMPLAUSIBLE,
    ISSUE_UNREACHABLE,
    ISSUE_PRICE_MISSING,
)
# Translation key of the api_changed issue when the portal said "updated".
TRANSLATION_STALE_CLIENT: Final = "stale_client"

README_URL: Final = "https://github.com/magicdude4eva/ha-eg-austria"
LEARN_MORE: Final = {
    ISSUE_TOKEN_EXPIRING: f"{README_URL}#token-expiry",
    ISSUE_API_CHANGED: f"{README_URL}#portal-changed",
    ISSUE_IMPLAUSIBLE: f"{README_URL}#held-data",
    ISSUE_UNREACHABLE: f"{README_URL}#portal-unreachable",
    ISSUE_PRICE_MISSING: f"{README_URL}#money",
}

# ADR 0007: warning period before the stored expiry.
TOKEN_WARNING: Final = {
    TokenKind.SESSION: timedelta(days=7),
    TokenKind.PERSONAL: timedelta(days=30),
}
MAX_LIST: Final = 5  # items shown per placeholder list


def issue_id(issue: str, entry_id: str) -> str:
    """``<issue>_<entry_id>``."""

    return f"{issue}_{entry_id}"


def token_expiry(entry: ConfigEntry) -> datetime | None:
    """Return the stored token expiry as an aware UTC datetime."""

    exp = entry.data.get(CONF_TOKEN_EXP)
    if isinstance(exp, bool) or not isinstance(exp, int | float):
        return None
    return datetime.fromtimestamp(exp, UTC)


def token_warning_start(entry: ConfigEntry) -> datetime | None:
    """Return when ``token_expiring`` is due (expiry minus 7 or 30 days)."""

    exp = token_expiry(entry)
    if exp is None:
        return None
    try:
        kind = TokenKind(entry.data.get(CONF_TOKEN_KIND, ""))
    except ValueError:
        kind = TokenKind.SESSION  # the shorter warning is the safe default
    return exp - TOKEN_WARNING[kind]


def _short_list(items: Iterable[object]) -> str:
    values = sorted({str(i) for i in items})
    if not values:
        return "-"
    more = len(values) - MAX_LIST
    text = ", ".join(values[:MAX_LIST])
    return f"{text} (+{more})" if more > 0 else text


def _date_range(days: Iterable[date]) -> str:
    days = sorted(set(days))
    if not days:
        return "-"
    if days[0] == days[-1]:
        return days[0].isoformat()
    return f"{days[0].isoformat()} - {days[-1].isoformat()}"


@callback
def async_delete_all(hass: HomeAssistant, entry_id: str) -> None:
    """Delete every issue of an entry (entry removed)."""

    for issue in ALL_ISSUES:
        ir.async_delete_issue(hass, DOMAIN, issue_id(issue, entry_id))


class EntryRepairs:
    """Raises and clears the repair issues of one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: EgAustriaCoordinator,
    ) -> None:
        """Bind to the entry; nothing happens until ``async_start()``."""

        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._last_summary: RunSummary | None = None
        self._token_unsub: CALLBACK_TYPE | None = None

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Check the token now, follow the coordinator; returns the unsubscriber."""

        self.async_check_token()
        remove = self.coordinator.async_add_listener(self._async_coordinator_update)

        @callback
        def _stop() -> None:
            remove()
            if self._token_unsub is not None:
                self._token_unsub()
                self._token_unsub = None

        return _stop

    # ------------------------------------------------------------------
    # Token

    @callback
    def async_check_token(self, now: datetime | None = None) -> None:
        """Raise ``token_expiring`` when due, else schedule the check.

        It is cleared by reauth or reconfigure when a new token is stored
        (config flow), never here: an expiring token stays a problem until
        it is replaced.
        """

        if self._token_unsub is not None:
            self._token_unsub()
            self._token_unsub = None
        start = token_warning_start(self.entry)
        exp = token_expiry(self.entry)
        if start is None or exp is None:
            return
        now = now or dt_util.utcnow()
        if now < start:
            self._token_unsub = async_track_point_in_utc_time(
                self.hass, self._async_token_due, start
            )
            return
        days = max(0, (exp - now).days)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id(ISSUE_TOKEN_EXPIRING, self.entry.entry_id),
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_TOKEN_EXPIRING,
            translation_placeholders={
                "title": self.entry.title,
                "expires": dt_util.as_local(exp).date().isoformat(),
                "days": str(days),
            },
            learn_more_url=LEARN_MORE[ISSUE_TOKEN_EXPIRING],
        )

    @callback
    def _async_token_due(self, now: datetime) -> None:
        self._token_unsub = None
        self.async_check_token(now)

    # ------------------------------------------------------------------
    # Run results

    @callback
    def _async_coordinator_update(self) -> None:
        summary = self.coordinator.data
        if summary is not None and summary is not self._last_summary:
            self._last_summary = summary
            self.async_apply_summary(summary)
            self.async_check_token()
        elif not self.coordinator.state.held_days:
            # release_held (no new run): nothing held any more.
            self._delete(ISSUE_IMPLAUSIBLE)

    @callback
    def async_apply_summary(self, summary: RunSummary) -> None:
        """Create or delete issues as the run's ``IssueHints`` say."""

        hints = summary.issues
        if hints.api_changed is True:
            self._create_api_changed(summary)
        elif hints.api_changed is False:
            self._delete(ISSUE_API_CHANGED)
        if hints.implausible_data is True:
            self._create_implausible(summary)
        elif hints.implausible_data is False:
            self._delete(ISSUE_IMPLAUSIBLE)
        if hints.portal_unreachable is True:
            self._create_unreachable()
        elif hints.portal_unreachable is False:
            self._delete(ISSUE_UNREACHABLE)
        if hints.price_missing is True:
            missing = summary.price_missing_from
            self._create(
                ISSUE_PRICE_MISSING,
                ir.IssueSeverity.WARNING,
                {"date": missing.isoformat() if missing else "-"},
            )
        elif hints.price_missing is False:
            self._delete(ISSUE_PRICE_MISSING)

    def _create(
        self,
        issue: str,
        severity: ir.IssueSeverity,
        placeholders: dict[str, str],
        translation_key: str | None = None,
    ) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id(issue, self.entry.entry_id),
            is_fixable=False,
            # Run-based issues survive a restart until a run clears them.
            is_persistent=True,
            severity=severity,
            translation_key=translation_key or issue,
            translation_placeholders={"title": self.entry.title, **placeholders},
            learn_more_url=LEARN_MORE[issue],
        )

    def _delete(self, issue: str) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, issue_id(issue, self.entry.entry_id))

    def _create_api_changed(self, summary: RunSummary) -> None:
        rules: set[str] = set()
        if summary.validation is not None:
            rules = {f.rule for f in summary.validation.failures}
        self._create(
            ISSUE_API_CHANGED,
            ir.IssueSeverity.ERROR,
            {
                "rules": _short_list(rules),
                "date": dt_util.as_local(summary.started).date().isoformat(),
            },
            TRANSLATION_STALE_CLIENT if summary.stale_client else None,
        )

    def _create_implausible(self, summary: RunSummary) -> None:
        validation = summary.validation
        rules: set[str] = set()
        # Money keys follow their kWh keys (ADR 0008); name only the kWh ones.
        keys: set[str] = {k for k in summary.blocked if not k.endswith("_eur")}
        days: list[date] = [d for v in summary.blocked.values() for d in v]
        if validation is not None:
            blocking = [f for f in validation.failures if not f.is_shape]
            rules = {f.rule for f in blocking}
            keys |= {f.statistic for f in blocking if f.statistic}
            for f in blocking:
                if f.day is not None:
                    days.append(f.day)
                elif f.chunk is not None:
                    days.extend(f.chunk)
        self._create(
            ISSUE_IMPLAUSIBLE,
            ir.IssueSeverity.WARNING,
            {
                "rules": _short_list(rules),
                "statistics": _short_list(keys),
                "dates": _date_range(days),
            },
        )

    def _create_unreachable(self) -> None:
        state = self.coordinator.state
        last = state.last_success
        self._create(
            ISSUE_UNREACHABLE,
            ir.IssueSeverity.WARNING,
            {
                "days": str(state.consecutive_failure_days),
                "last_success": (
                    dt_util.as_local(last).date().isoformat() if last else "-"
                ),
            },
        )
