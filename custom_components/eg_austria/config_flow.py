"""Config flow for EG Austria (spec 02, ADR 0007, ADR 0012).

Steps of a new entry: ``user`` (session cookie or token) → ``token_expiry``
(pasted personal token only) → ``member`` / ``accept_terms`` (only when
needed) → ``location`` (several locations only) → ``create_token`` (session
cookie: consent, then one create call; ``create_failed`` falls back to the
cookie) → ``expiring_soon`` (cookie kept, < 7 days) → ``site`` →
``communities`` → ``community_slugs`` (only when asked for).

Privacy: tokens and cookies are never logged or shown back; a cookie used to
create or revoke a token is not stored (ADR 0007). Member display names,
addresses, metering point numbers and names and community names appear only
as labels in the flow; only the community name is stored (as ``name``, the
statistic display name, ADR 0012).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import hashlib
import logging
import random
import re
from typing import Any, Final

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    StatisticSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
import probatio as vol

from .api import REPO_URL, EgAustriaClient, get_portal_session
from .const import (
    CONF_COMMUNITIES,
    CONF_DAILY_WINDOW_DAYS,
    CONF_IMPORT_HISTORY,
    CONF_LOCATION_ID,
    CONF_LONG_REFETCH_MONTHS,
    CONF_MAX_HOURLY_KWH,
    CONF_MEMBER_NUMBER,
    CONF_MP_IDS,
    CONF_RETIRED_SLUGS,
    CONF_RUN_MINUTE,
    CONF_SITE_SLUG,
    CONF_SUPPLIER_PRICE,
    CONF_SURPLUS_PRICE,
    CONF_TOKEN,
    CONF_TOKEN_EXP,
    CONF_TOKEN_ID,
    CONF_TOKEN_KIND,
    CREATED_TOKEN_NAME,
    DEFAULT_DAILY_WINDOW_DAYS,
    DEFAULT_IMPORT_HISTORY,
    DEFAULT_LONG_REFETCH_MONTHS,
    DEFAULT_MAX_HOURLY_KWH,
    DOMAIN,
    PERSONAL_TOKEN_DEFAULT_DAYS,
    SOURCE_KEYS,
)
from .exceptions import (
    ApiChangedError,
    AuthError,
    ForbiddenError,
    PortalUnavailableError,
    RateLimitedError,
    SchemaError,
    TokenExpiredError,
)
from .models import (
    Direction,
    Location,
    Member,
    MeteringPoint,
    MeteringPointInfo,
    SessionInfo,
)
from .prices import (
    DEFAULT_TABLE_ATTRIBUTE,
    UNIT_CT,
    UNIT_EUR,
    PriceSource,
    SourceType,
    format_periods,
    month_table,
    parse_periods,
    unit_factor,
)
from .sources import (
    async_energy_defaults,
    async_validate_sources,
    configured_sources,
    sources_configured,
)
from .token import ParsedToken, TokenFormatError, TokenKind, parse_token
from .token_api import PortalTokenClient

_LOGGER = logging.getLogger(__name__)

SLUG_RE: Final = re.compile(r"[a-z][a-z0-9_]{0,19}")
DEFAULT_SITE_SLUG: Final = "home"
ENTRY_TITLE: Final = "EG Austria Home"
SESSION_NOTICE_DAYS: Final = 7
MAX_COMMUNITIES: Final = 9  # rows with translated labels in strings.json
RUN_MINUTE_RANGE: Final = (180, 359)  # ADR 0004
README_TOKEN_URL: Final = f"{REPO_URL}#token"
PORTAL_URL: Final = "https://portal.eg-austria.at"
_TOKEN_PLACEHOLDERS: Final = {"readme_url": README_TOKEN_URL, "portal_url": PORTAL_URL}
_EXPIRES: Final = "token_expires"

# Community type code → default slug stem and label (spec 02, ADR 0012).
_TYPE_SLUG: Final = {"CC": "beg", "RC_R": "eeg", "RC_L": "eeg"}
_TYPE_LABEL: Final = {"CC": "BEG", "RC_R": "EEG", "RC_L": "EEG"}
_DIRECTION_LABEL: Final = {
    "en": {Direction.CONSUMPTION: "Consumption", Direction.GENERATION: "Generation"},
    "de": {Direction.CONSUMPTION: "Bezug", Direction.GENERATION: "Einspeisung"},
}
_SINCE_LABEL: Final = {"en": "member since", "de": "Mitglied seit"}

_TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_SLUG_SELECTOR = TextSelector()


def entry_unique_id(member: str, location_id: str) -> str:
    """Hash of the member number plus hash of the location (no ids in clear)."""

    m = hashlib.sha256(f"eg_austria:{member}".encode()).hexdigest()[:16]
    loc = hashlib.sha256(f"eg_austria:{member}:{location_id}".encode()).hexdigest()
    return f"{m}_{loc[:16]}"


def default_slug(community_type: str, taken: set[str]) -> str:
    """``beg``, ``eeg``, then ``eeg2``, ``eeg3`` ... avoiding ``taken``."""

    stem = _TYPE_SLUG.get(community_type, "ec")
    if stem not in taken:
        return stem
    n = 2
    while f"{stem}{n}" in taken:
        n += 1
    return f"{stem}{n}"


def end_of_day_timestamp(day: date) -> int:
    """Unix timestamp of 23:59:59 local time on ``day``."""

    tz = dt_util.get_default_time_zone()
    return int(datetime.combine(day, time(23, 59, 59), tzinfo=tz).timestamp())


def _now_ts() -> int:
    return int(dt_util.utcnow().timestamp())


def _error_key(err: Exception) -> str:
    """Map a client error to a flow error key (spec 02)."""

    if isinstance(err, TokenExpiredError):
        return "token_expired"
    if isinstance(err, AuthError):
        return "invalid_auth"
    if isinstance(err, ApiChangedError | SchemaError):
        return "api_changed"
    if isinstance(err, PortalUnavailableError | RateLimitedError):
        return "cannot_connect"
    # Type only: an unexpected message could carry anything.
    _LOGGER.error("Unexpected error in the config flow: %s", type(err).__name__)
    return "unknown"


def _create_error_key(err: Exception) -> str:
    """Map a token-create error to a ``create_failed`` error key (spec 02)."""

    if isinstance(err, AuthError):
        return "create_failed_auth"
    if isinstance(err, PortalUnavailableError | RateLimitedError):
        return "create_failed_connect"
    if not isinstance(err, ApiChangedError | SchemaError):
        _LOGGER.error("Unexpected error creating the token: %s", type(err).__name__)
    return "create_failed_response"


@asynccontextmanager
async def _portal_client(
    hass: HomeAssistant, token: str, kind: TokenKind, token_exp: int | None
) -> AsyncIterator[EgAustriaClient]:
    """Yield a client on the shared portal session (HA owns and closes it)."""

    yield EgAustriaClient(get_portal_session(hass), token, kind, token_exp=token_exp)


def _is_active(mp: MeteringPoint, today: date) -> bool:
    return any(
        act.status == "Active" and (act.active_to is None or act.active_to >= today)
        for act in mp.activations
    )


_PRICE_OPTION: Final = {"surplus": CONF_SURPLUS_PRICE, "supplier": CONF_SUPPLIER_PRICE}


@dataclass(slots=True)
class _Community:
    """A community found in the activations of the selected metering points."""

    id: int
    type: str
    name: str
    active_from: date


def _communities_of(location: Location, mp_ids: list[int]) -> list[_Community]:
    """Distinct communities of the selected metering points, in portal order."""

    found: dict[int, _Community] = {}
    for mp in location.metering_points:
        if mp.id not in mp_ids:
            continue
        for act in mp.activations:
            seen = found.get(act.community_id)
            if seen is None:
                found[act.community_id] = _Community(
                    act.community_id,
                    act.community_type,
                    act.community_name,
                    act.active_from,
                )
            elif act.active_from < seen.active_from:
                seen.active_from = act.active_from
    return list(found.values())


def _lang(hass: HomeAssistant) -> str:
    return "de" if hass.config.language.startswith("de") else "en"


def _format_day(day: date, lang: str) -> str:
    return day.strftime("%d.%m.%Y") if lang == "de" else day.isoformat()


def _community_label(c: _Community, slug: str, lang: str) -> str:
    """``<name> (BEG, member since <date>) → <slug>`` (issue #20)."""

    kind = _TYPE_LABEL.get(c.type, c.type)
    since = f"{_SINCE_LABEL[lang]} {_format_day(c.active_from, lang)}"
    return f"{c.name} ({kind}, {since}) → {slug}"


def _slug_lines(rows: list[_Community]) -> str:
    """Map each slug field number to its community (step description)."""

    return "\n".join(
        f"{n}: {c.name} ({_TYPE_LABEL.get(c.type, c.type)})"
        for n, c in enumerate(rows, start=1)
    )


def _factor(value: float) -> str:
    return f"{value:g} %"


def _mp_label(mp: MeteringPoint, lang: str, today: date) -> str:
    """Direction, full number, facility name and type, part factors (#19)."""

    parts = [f"{_DIRECTION_LABEL[lang][mp.direction]} {mp.number or mp.number_tail}"]
    facility = mp.name or ""
    if mp.source_of_production:
        facility = f"{facility} ({mp.source_of_production})".strip()
    if facility:
        parts.append(facility)
    factors: dict[int, str] = {}
    for act in mp.activations:
        current = act.status == "Active" and (
            act.active_to is None or act.active_to >= today
        )
        if current and act.active_part_factor is not None:
            factors[act.community_id] = (
                f"{act.community_name} {_factor(act.active_part_factor)}"
            )
    if factors:
        parts.append(", ".join(factors.values()))
    return " · ".join(parts)


class EgAustriaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the EG Austria config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Start with no state; nothing is persisted until the entry is made."""

        self._parsed: ParsedToken | None = None
        self._token_exp: int | None = None
        self._token_id: str | None = None  # set only for a created token
        self._token_decided = False  # consent step answered
        self._create_error = "create_failed_response"
        self._flow_step = "user"  # step that asked for the token
        self._session: SessionInfo | None = None
        self._member: Member | None = None
        self._mp_info: MeteringPointInfo | None = None
        self._location: Location | None = None
        self._mp_ids: list[int] = []
        self._site_slug = DEFAULT_SITE_SLUG
        self._found: list[_Community] = []
        self._slugs: dict[int, str] = {}  # community id → default slug
        self._chosen: list[_Community] = []
        self._notice_expiring = False
        self._notice_terms = False
        # Reconfigure only.
        self._kept: list[dict[str, Any]] = []
        self._orphans: list[dict[str, Any]] = []
        self._new: list[_Community] = []

    # ------------------------------------------------------------------
    # Token handling shared by user, reauth and reconfigure

    def _accept_token(self, raw: str) -> str | None:
        """Parse and check the pasted token locally; return an error key."""

        try:
            parsed = parse_token(raw)
        except TokenFormatError:
            return "invalid_token_format"
        if parsed.kind is TokenKind.SESSION:
            assert parsed.exp is not None  # noqa: S101
            if parsed.exp <= _now_ts():
                return "token_expired"
            self._notice_expiring = parsed.exp - _now_ts() < SESSION_NOTICE_DAYS * 86400
        else:
            self._notice_expiring = False
        self._parsed = parsed
        self._token_exp = parsed.exp
        self._token_id = None
        self._token_decided = False
        return None

    def _client(self) -> Any:
        assert self._parsed is not None  # noqa: S101
        return _portal_client(
            self.hass, self._parsed.token, self._parsed.kind, self._token_exp
        )

    async def async_step_token_expiry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Personal token: the user states its expiry date (ADR 0007)."""

        errors: dict[str, str] = {}
        default = dt_util.now().date() + timedelta(days=PERSONAL_TOKEN_DEFAULT_DAYS)
        if user_input is not None:
            try:
                day = date.fromisoformat(str(user_input[_EXPIRES]))
            except ValueError:  # pragma: no cover - the selector validates
                errors[_EXPIRES] = "invalid_date"
            else:
                default = day
                exp = end_of_day_timestamp(day)
                if exp <= _now_ts():
                    errors[_EXPIRES] = "token_expired"
                else:
                    self._token_exp = exp
                    error = await self._async_validate_online()
                    if error is None:
                        return await self._async_after_check()
                    errors["base"] = error
        return self.async_show_form(
            step_id="token_expiry",
            data_schema=vol.Schema(
                {vol.Required(_EXPIRES, default=default.isoformat()): DateSelector()}
            ),
            errors=errors,
        )

    async def _async_token_submitted(
        self, step_id: str, raw: str
    ) -> ConfigFlowResult | str:
        """Local checks, then expiry step or online validation."""

        self._flow_step = step_id
        if error := self._accept_token(raw):
            return error
        assert self._parsed is not None  # noqa: S101
        if self._parsed.kind is TokenKind.PERSONAL:
            return await self.async_step_token_expiry()
        if error := await self._async_validate_online():
            return error
        return await self._async_after_check()

    async def _async_validate_online(self) -> str | None:
        """Call the portal for the step that asked for the token."""

        if self._flow_step == "user":
            return await self._async_validate_new()
        return await self._async_validate_same_member()

    async def _async_after_check(self) -> ConfigFlowResult:
        """Next step once the pasted value passed its checks.

        A new entry asks for consent only after the location is known and
        the duplicate check passed (``_async_after_location``), so a
        duplicate never creates a token.
        """

        assert self._parsed is not None  # noqa: S101
        if self._flow_step == "user":
            return await self._async_next_new()
        if self._parsed.kind is TokenKind.SESSION:
            return await self.async_step_create_token()
        return await self._async_after_token()

    async def _async_after_token(self) -> ConfigFlowResult:
        """Next step once the token to store is decided."""

        self._token_decided = True
        if self._flow_step == "user":
            return await self._async_after_location()
        if self._flow_step == "reauth_confirm":
            return self._finish_reauth()
        if self._flow_step == "revoke_token":
            return self._finish_revoke()
        return await self._async_reconfigure_location()

    # ------------------------------------------------------------------
    # Token creation from a session cookie (ADR 0007, issue #18)

    async def async_step_create_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Consent: create a read-only token, or keep the session cookie."""

        assert self._token_exp is not None  # noqa: S101
        days = max(0, (self._token_exp - _now_ts()) // 86400)
        return self.async_show_menu(
            step_id="create_token",
            menu_options=["create_token_confirm", "use_session"],
            description_placeholders={
                "token_name": CREATED_TOKEN_NAME,
                "days": str(days),
            },
        )

    async def async_step_create_token_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Exactly one create call with the cookie; the cookie is then dropped."""

        assert self._parsed is not None  # noqa: S101
        assert self._parsed.kind is TokenKind.SESSION  # noqa: S101
        client = PortalTokenClient(get_portal_session(self.hass), self._parsed.token)
        expires = dt_util.utcnow() + timedelta(days=PERSONAL_TOKEN_DEFAULT_DAYS)
        try:
            created = await client.create_token(CREATED_TOKEN_NAME, expires)
        except Exception as err:
            self._create_error = _create_error_key(err)
            return await self.async_step_create_failed()
        self._parsed = ParsedToken(created.token, TokenKind.PERSONAL)
        self._token_exp = int(created.expires_at.timestamp())
        self._token_id = created.id
        self._notice_expiring = False
        return await self._async_after_token()

    async def async_step_create_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the create error, then continue with the session cookie."""

        if user_input is not None:
            return await self.async_step_use_session()
        return self.async_show_form(
            step_id="create_failed",
            data_schema=vol.Schema({}),
            errors={"base": self._create_error},
        )

    async def async_step_use_session(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep the session cookie as the stored token (fallback)."""

        self._token_id = None
        return await self._async_after_token()

    # ------------------------------------------------------------------
    # New entry

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the session cookie (or a personal token)."""

        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_token_submitted("user", user_input[CONF_TOKEN])
            if not isinstance(result, str):
                return result
            errors["base"] = result
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _TOKEN_SELECTOR}),
            errors=errors,
            description_placeholders=_TOKEN_PLACEHOLDERS,
        )

    async def _async_validate_new(self) -> str | None:
        """``get_session``; with one member also ``get_metering_points``."""

        self._member = None
        try:
            async with self._client() as client:
                self._session = await client.get_session()
                if not self._session.members:
                    return "no_member"
                if len(self._session.members) == 1:
                    self._member = self._session.members[0]
                    self._mp_info = await client.get_metering_points(
                        self._member.member_id
                    )
        except Exception as err:
            return self._member_error(err)
        return None

    def _member_error(self, err: Exception) -> str:
        if (
            self._member is not None
            and self._member.must_accept_platform_terms
            and isinstance(err, AuthError)
            and not isinstance(err, TokenExpiredError)
        ):
            return "terms_not_accepted"
        return _error_key(err)

    async def _async_next_new(self) -> ConfigFlowResult:
        """Member choice, terms notice and location in that order."""

        if self._member is None:
            return await self.async_step_member()
        if self._member.must_accept_platform_terms and not self._notice_terms:
            return await self.async_step_accept_terms()
        return await self.async_step_location()

    async def async_step_expiring_soon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Notice: the session JWT expires within 7 days."""

        if user_input is not None:
            self._notice_expiring = False
            return await self._async_after_location()
        assert self._token_exp is not None  # noqa: S101
        days = max(0, (self._token_exp - _now_ts()) // 86400)
        return self.async_show_form(
            step_id="expiring_soon",
            data_schema=vol.Schema({}),
            description_placeholders={"days": str(days)},
        )

    async def async_step_member(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Several members: choose one (labels are shown, never stored)."""

        assert self._session is not None  # noqa: S101
        members = self._session.members
        errors: dict[str, str] = {}
        if user_input is not None:
            idx = int(user_input["member"])
            self._member = members[idx]
            try:
                async with self._client() as client:
                    self._mp_info = await client.get_metering_points(
                        self._member.member_id
                    )
            except Exception as err:
                errors["base"] = self._member_error(err)
            else:
                return await self._async_next_new()
            self._member = None
        options = [
            SelectOptionDict(value=str(i), label=m.display_name or f"#{i + 1}")
            for i, m in enumerate(members)
        ]
        return self.async_show_form(
            step_id="member",
            data_schema=vol.Schema(
                {
                    vol.Required("member", default="0"): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_accept_terms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Notice: the portal terms are still to be accepted in the browser."""

        if user_input is not None:
            self._notice_terms = True
            return await self.async_step_location()
        return self.async_show_form(step_id="accept_terms", data_schema=vol.Schema({}))

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the location; skipped when the member has one."""

        assert self._mp_info is not None  # noqa: S101
        assert self._member is not None  # noqa: S101
        locations = self._mp_info.locations
        if not locations:
            return self.async_abort(reason="no_location")
        if user_input is None and len(locations) > 1:
            return self.async_show_form(
                step_id="location",
                data_schema=vol.Schema(
                    {
                        vol.Required("location"): SelectSelector(
                            SelectSelectorConfig(options=_location_options(locations))
                        )
                    }
                ),
            )
        location_id = user_input["location"] if user_input else locations[0].id
        self._location = self._mp_info.location(location_id)
        if self._location is None:  # pragma: no cover - select limits values
            return self.async_abort(reason="no_location")
        await self.async_set_unique_id(
            entry_unique_id(self._member.member_id, self._location.id)
        )
        self._abort_if_unique_id_configured()
        if not self._location.metering_points:
            return self.async_abort(reason="no_metering_points")
        return await self._async_after_location()

    async def _async_after_location(self) -> ConfigFlowResult:
        """Ask for consent (session cookie), show the 7-day notice, then site."""

        assert self._parsed is not None  # noqa: S101
        if self._parsed.kind is TokenKind.SESSION and not self._token_decided:
            return await self.async_step_create_token()
        if self._notice_expiring:
            return await self.async_step_expiring_soon()
        return await self.async_step_site()

    def _mp_schema(self, default: list[str]) -> vol.Schema:
        assert self._location is not None  # noqa: S101
        lang = _lang(self.hass)
        today = dt_util.now().date()
        options = [
            SelectOptionDict(value=str(mp.id), label=_mp_label(mp, lang, today))
            for mp in self._location.metering_points
        ]
        return vol.Schema(
            {
                vol.Required("metering_points", default=default): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                )
            }
        )

    def _default_mps(self, wanted: list[int] | None = None) -> list[str]:
        assert self._location is not None  # noqa: S101
        mps = self._location.metering_points
        if wanted:
            kept = [str(mp.id) for mp in mps if mp.id in wanted]
            if kept:
                return kept
        today = dt_util.now().date()
        active = [str(mp.id) for mp in mps if _is_active(mp, today)]
        return active or [str(mp.id) for mp in mps]

    async def async_step_site(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Metering points of the location and the site slug."""

        assert self._location is not None  # noqa: S101
        errors: dict[str, str] = {}
        default_mps = self._default_mps()
        slug = DEFAULT_SITE_SLUG
        if user_input is not None:
            default_mps = list(user_input.get("metering_points", []))
            slug = str(user_input.get(CONF_SITE_SLUG, "")).strip()
            mp_ids = [int(v) for v in default_mps]
            if not mp_ids:
                errors["metering_points"] = "no_metering_point"
            elif not SLUG_RE.fullmatch(slug):
                errors[CONF_SITE_SLUG] = "invalid_slug"
            elif slug in self._site_slugs_in_use():
                errors[CONF_SITE_SLUG] = "slug_in_use"
            else:
                found = _communities_of(self._location, mp_ids)
                if not found:
                    errors["base"] = "no_community"
                elif len(found) > MAX_COMMUNITIES:
                    return self.async_abort(reason="too_many_communities")
                else:
                    self._mp_ids, self._site_slug, self._found = mp_ids, slug, found
                    return await self.async_step_communities()
        schema = self._mp_schema(default_mps).extend(
            {vol.Required(CONF_SITE_SLUG, default=slug): _SLUG_SELECTOR}
        )
        return self.async_show_form(step_id="site", data_schema=schema, errors=errors)

    def _site_slugs_in_use(self) -> set[str]:
        return {
            slug
            for e in self._async_current_entries(include_ignore=False)
            if isinstance(slug := e.data.get(CONF_SITE_SLUG), str)
        }

    def _community_schema(
        self, rows: list[_Community], selected: list[str], edit: bool
    ) -> vol.Schema:
        """Multi-select labelled per community, plus the slug-edit checkbox."""

        lang = _lang(self.hass)
        options = [
            SelectOptionDict(
                value=str(c.id), label=_community_label(c, self._slugs[c.id], lang)
            )
            for c in rows
        ]
        return vol.Schema(
            {
                vol.Required("communities", default=selected): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                ),
                vol.Required("edit_slugs", default=edit): BooleanSelector(),
            }
        )

    async def async_step_communities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose communities (labelled per community, issue #20)."""

        errors: dict[str, str] = {}
        taken: set[str] = set()
        self._slugs = {}
        for c in self._found:
            self._slugs[c.id] = default_slug(c.type, taken)
            taken.add(self._slugs[c.id])
        selected = [str(c.id) for c in self._found]
        edit = False
        if user_input is not None:
            selected = [str(v) for v in user_input.get("communities", [])]
            edit = bool(user_input.get("edit_slugs", False))
            chosen = [c for c in self._found if str(c.id) in selected]
            if not chosen:
                errors["base"] = "no_community"
            elif edit:
                self._chosen = chosen
                return await self.async_step_community_slugs()
            else:
                return self._create_entry([(c, self._slugs[c.id]) for c in chosen])
        return self.async_show_form(
            step_id="communities",
            data_schema=self._community_schema(self._found, selected, edit),
            errors=errors,
        )

    async def async_step_community_slugs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Slugs of the chosen communities (frozen after setup)."""

        errors: dict[str, str] = {}
        defaults = [self._slugs[c.id] for c in self._chosen]
        if user_input is not None:
            chosen, errors = _read_slugs(user_input, self._chosen, reserved=set())
            if not errors:
                return self._create_entry(chosen)
            defaults = _entered_slugs(user_input, defaults)
        return self.async_show_form(
            step_id="community_slugs",
            data_schema=_slug_schema(defaults),
            errors=errors,
            description_placeholders={"communities": _slug_lines(self._chosen)},
        )

    def _create_entry(self, chosen: list[tuple[_Community, str]]) -> ConfigFlowResult:
        assert self._parsed is not None  # noqa: S101
        assert self._member is not None  # noqa: S101
        assert self._location is not None  # noqa: S101
        assert self._token_exp is not None  # noqa: S101
        data = {
            CONF_TOKEN: self._parsed.token,
            CONF_TOKEN_KIND: self._parsed.kind.value,
            CONF_TOKEN_EXP: self._token_exp,
            CONF_MEMBER_NUMBER: self._member.member_id,
            CONF_LOCATION_ID: self._location.id,
            CONF_MP_IDS: self._mp_ids,
            CONF_COMMUNITIES: [
                {"id": c.id, "slug": slug, "name": c.name} for c, slug in chosen
            ],
            CONF_RETIRED_SLUGS: [],
            CONF_SITE_SLUG: self._site_slug,
            CONF_RUN_MINUTE: random.randint(*RUN_MINUTE_RANGE),  # noqa: S311
        }
        if self._token_id is not None:
            data[CONF_TOKEN_ID] = self._token_id
        return self.async_create_entry(
            title=ENTRY_TITLE, data=data, options=default_options()
        )

    # ------------------------------------------------------------------
    # Reauth

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth after ``ConfigEntryAuthFailed`` (401/403, revoked, expired)."""

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Paste a new session cookie (or token) of the same member."""

        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_token_submitted(
                "reauth_confirm", user_input[CONF_TOKEN]
            )
            if not isinstance(result, str):
                return result
            errors["base"] = result
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _TOKEN_SELECTOR}),
            errors=errors,
            description_placeholders=_TOKEN_PLACEHOLDERS,
        )

    def _existing_entry(self) -> ConfigEntry:
        if self._flow_step == "reauth_confirm":
            return self._get_reauth_entry()
        return self._get_reconfigure_entry()

    async def _async_validate_same_member(self) -> str | None:
        """Check that the new token belongs to the stored member (spec 02)."""

        member = self._existing_entry().data[CONF_MEMBER_NUMBER]
        try:
            async with self._client() as client:
                session = await client.get_session()
                if member not in session.member_ids():
                    return "wrong_account"
                self._mp_info = await client.get_metering_points(member)
        except ForbiddenError:
            return "wrong_account"
        except Exception as err:
            return _error_key(err)
        return None

    def _with_token(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """Return ``data`` with the new token fields (``token_id`` if created)."""

        assert self._parsed is not None  # noqa: S101
        new = {
            **data,
            CONF_TOKEN: self._parsed.token,
            CONF_TOKEN_KIND: self._parsed.kind.value,
            CONF_TOKEN_EXP: self._token_exp,
        }
        new.pop(CONF_TOKEN_ID, None)
        if self._token_id is not None:
            new[CONF_TOKEN_ID] = self._token_id
        return new

    def _clear_token_expiring(self, entry: ConfigEntry) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, f"token_expiring_{entry.entry_id}")

    def _finish_reauth(self) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        self._clear_token_expiring(entry)
        return self.async_update_reload_and_abort(
            entry, data=self._with_token(entry.data)
        )

    def _finish_revoke(self) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        self._clear_token_expiring(entry)
        return self.async_update_reload_and_abort(
            entry, data=self._with_token(entry.data), reason="token_revoked"
        )

    # ------------------------------------------------------------------
    # Reconfigure

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Menu with "revoke token" when the flow created the stored token."""

        if self._get_reconfigure_entry().data.get(CONF_TOKEN_ID):
            return self.async_show_menu(
                step_id="reconfigure",
                menu_options=["reconfigure_token", "revoke_token"],
            )
        return await self.async_step_reconfigure_token()

    async def async_step_reconfigure_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally paste a new token; then metering points and communities."""

        errors: dict[str, str] = {}
        if user_input is not None:
            raw = str(user_input.get(CONF_TOKEN) or "").strip()
            if raw:
                result = await self._async_token_submitted("reconfigure_token", raw)
            else:
                self._flow_step = "reconfigure_token"
                entry = self._get_reconfigure_entry()
                stored = ParsedToken(
                    entry.data[CONF_TOKEN], TokenKind(entry.data[CONF_TOKEN_KIND])
                )
                self._parsed, self._token_exp = stored, entry.data[CONF_TOKEN_EXP]
                error = await self._async_validate_same_member()
                self._parsed = None  # no token change
                result = error or await self._async_reconfigure_location()
            if not isinstance(result, str):
                return result
            errors["base"] = result
        return self.async_show_form(
            step_id="reconfigure_token",
            data_schema=vol.Schema({vol.Optional(CONF_TOKEN): _TOKEN_SELECTOR}),
            errors=errors,
            description_placeholders={"readme_url": README_TOKEN_URL},
        )

    async def async_step_revoke_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Revoke the token the flow created, with a fresh session cookie."""

        errors: dict[str, str] = {}
        if user_input is not None:
            self._flow_step = "revoke_token"
            error = self._accept_token(user_input[CONF_TOKEN])
            if error is None:
                assert self._parsed is not None  # noqa: S101
                if self._parsed.kind is not TokenKind.SESSION:
                    error = "session_cookie_required"
            error = (
                error
                or await self._async_validate_same_member()
                or await self._async_revoke()
            )
            if error is None:
                return await self.async_step_create_token()
            errors["base"] = error
        return self.async_show_form(
            step_id="revoke_token",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _TOKEN_SELECTOR}),
            errors=errors,
            description_placeholders=_TOKEN_PLACEHOLDERS,
        )

    async def _async_revoke(self) -> str | None:
        """Exactly one revoke call for the stored token id."""

        assert self._parsed is not None  # noqa: S101
        token_id = str(self._get_reconfigure_entry().data[CONF_TOKEN_ID])
        client = PortalTokenClient(get_portal_session(self.hass), self._parsed.token)
        try:
            await client.revoke_token(token_id)
        except Exception as err:
            return _error_key(err)
        return None

    async def _async_reconfigure_location(self) -> ConfigFlowResult:
        assert self._mp_info is not None  # noqa: S101
        entry = self._get_reconfigure_entry()
        self._location = self._mp_info.location(entry.data[CONF_LOCATION_ID])
        if self._location is None:
            return await self.async_step_reconfigure_location()
        return await self.async_step_reconfigure_site()

    async def async_step_reconfigure_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Map the site to a current location when its stored id is gone."""

        assert self._mp_info is not None  # noqa: S101
        locations = self._mp_info.locations
        if not locations:
            return self.async_abort(reason="no_location")
        if user_input is not None:
            self._location = self._mp_info.location(user_input["location"])
            if self._location is not None:
                entry = self._get_reconfigure_entry()
                uid = entry_unique_id(entry.data[CONF_MEMBER_NUMBER], self._location.id)
                if any(
                    e.unique_id == uid and e.entry_id != entry.entry_id
                    for e in self._async_current_entries(include_ignore=False)
                ):
                    return self.async_abort(reason="already_configured")
                return await self.async_step_reconfigure_site()
        return self.async_show_form(
            step_id="reconfigure_location",
            data_schema=vol.Schema(
                {
                    vol.Required("location"): SelectSelector(
                        SelectSelectorConfig(options=_location_options(locations))
                    )
                }
            ),
        )

    async def async_step_reconfigure_site(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the metering point selection; the site slug is read-only."""

        assert self._location is not None  # noqa: S101
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        default_mps = self._default_mps(entry.data[CONF_MP_IDS])
        if user_input is not None:
            default_mps = list(user_input.get("metering_points", []))
            mp_ids = [int(v) for v in default_mps]
            if not mp_ids:
                errors["metering_points"] = "no_metering_point"
            else:
                self._mp_ids = mp_ids
                self._split_communities(entry)
                if len(self._new) > MAX_COMMUNITIES:
                    return self.async_abort(reason="too_many_communities")
                if not self._kept and not self._new:
                    errors["base"] = "no_community"
                elif not self._new and not self._orphans:
                    return self._finish_reconfigure([])
                else:
                    return await self.async_step_reconfigure_communities()
        return self.async_show_form(
            step_id="reconfigure_site",
            data_schema=self._mp_schema(default_mps),
            errors=errors,
            description_placeholders={"site_slug": entry.data[CONF_SITE_SLUG]},
        )

    def _split_communities(self, entry: ConfigEntry) -> None:
        """Split into stored and present (kept), stored and gone (orphans), new."""

        assert self._location is not None  # noqa: S101
        found = _communities_of(self._location, self._mp_ids)
        by_id = {c.id: c for c in found}
        stored = list(entry.data[CONF_COMMUNITIES])
        stored_ids = {s["id"] for s in stored}
        self._kept = [
            {**s, "name": by_id[s["id"]].name} for s in stored if s["id"] in by_id
        ]
        self._orphans = [s for s in stored if s["id"] not in by_id]
        self._new = [c for c in found if c.id not in stored_ids]

    async def async_step_reconfigure_communities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add new communities; an orphaned or retired slug remaps its history."""

        entry = self._get_reconfigure_entry()
        retired = set(entry.data.get(CONF_RETIRED_SLUGS, []))
        kept_slugs = {k["slug"] for k in self._kept}
        orphan_slugs = {o["slug"] for o in self._orphans}
        taken = kept_slugs | orphan_slugs | retired
        self._slugs = {}
        for c in self._new:
            match = next(
                (o["slug"] for o in self._orphans if o.get("name") == c.name), None
            )
            if match is not None and match not in self._slugs.values():
                self._slugs[c.id] = match
                continue
            self._slugs[c.id] = default_slug(c.type, taken)
            taken.add(self._slugs[c.id])
        errors: dict[str, str] = {}
        selected = [str(c.id) for c in self._new]
        edit = False
        if user_input is not None:
            selected = [str(v) for v in user_input.get("communities", [])]
            edit = bool(user_input.get("edit_slugs", False))
            chosen = [c for c in self._new if str(c.id) in selected]
            if not chosen and not self._kept:
                errors["base"] = "no_community"
            elif edit and chosen:
                self._chosen = chosen
                return await self.async_step_reconfigure_community_slugs()
            else:
                return self._finish_reconfigure(
                    [(c, self._slugs[c.id]) for c in chosen]
                )
        schema = (
            self._community_schema(self._new, selected, edit)
            if self._new
            else vol.Schema({})
        )
        kept_lines = [f"- {k['name']} → `{k['slug']}`" for k in self._kept]
        orphan_lines = [f"- {o['name']} → `{o['slug']}`" for o in self._orphans]
        return self.async_show_form(
            step_id="reconfigure_communities",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "kept": "\n".join(kept_lines) or "-",
                "orphans": "\n".join(orphan_lines) or "-",
                "retired": ", ".join(f"`{s}`" for s in sorted(retired)) or "-",
            },
        )

    async def async_step_reconfigure_community_slugs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Slugs of the new communities; kept slugs can't be reused."""

        kept_slugs = {k["slug"] for k in self._kept}
        errors: dict[str, str] = {}
        defaults = [self._slugs[c.id] for c in self._chosen]
        if user_input is not None:
            chosen, errors = _read_slugs(user_input, self._chosen, reserved=kept_slugs)
            if not errors:
                return self._finish_reconfigure(chosen)
            defaults = _entered_slugs(user_input, defaults)
        return self.async_show_form(
            step_id="reconfigure_community_slugs",
            data_schema=_slug_schema(defaults),
            errors=errors,
            description_placeholders={"communities": _slug_lines(self._chosen)},
        )

    def _finish_reconfigure(
        self, chosen: list[tuple[_Community, str]]
    ) -> ConfigFlowResult:
        assert self._location is not None  # noqa: S101
        entry = self._get_reconfigure_entry()
        remapped = {slug for _, slug in chosen}
        retired = set(entry.data.get(CONF_RETIRED_SLUGS, []))
        retired |= {o["slug"] for o in self._orphans}
        retired -= remapped
        communities = self._kept + [
            {"id": c.id, "slug": slug, "name": c.name} for c, slug in chosen
        ]
        data: dict[str, Any] = {
            **entry.data,
            CONF_LOCATION_ID: self._location.id,
            CONF_MP_IDS: self._mp_ids,
            CONF_COMMUNITIES: communities,
            CONF_RETIRED_SLUGS: sorted(retired),
        }
        if self._parsed is not None:
            data = self._with_token(data)
            self._clear_token_expiring(entry)
        unique_id = entry_unique_id(entry.data[CONF_MEMBER_NUMBER], self._location.id)
        return self.async_update_reload_and_abort(entry, unique_id=unique_id, data=data)

    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EgAustriaOptionsFlow:
        """Return the options flow."""

        return EgAustriaOptionsFlow()


def _location_options(locations: tuple[Location, ...]) -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=loc.id, label=loc.label or f"#{i + 1}")
        for i, loc in enumerate(locations)
    ]


def _slug_schema(defaults: list[str]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(f"slug_{i}", default=slug): _SLUG_SELECTOR
            for i, slug in enumerate(defaults, start=1)
        }
    )


def _entered_slugs(user_input: Mapping[str, Any], defaults: list[str]) -> list[str]:
    return [
        str(user_input.get(f"slug_{i}", d)) for i, d in enumerate(defaults, start=1)
    ]


def _read_slugs(
    user_input: Mapping[str, Any], rows: list[_Community], *, reserved: set[str]
) -> tuple[list[tuple[_Community, str]], dict[str, str]]:
    """Read ``slug_<n>`` fields; slugs must match the pattern and be unique."""

    chosen: list[tuple[_Community, str]] = []
    errors: dict[str, str] = {}
    seen = set(reserved)
    for i, c in enumerate(rows, start=1):
        slug = str(user_input.get(f"slug_{i}", "")).strip()
        if not SLUG_RE.fullmatch(slug):
            errors[f"slug_{i}"] = "invalid_slug"
        elif slug in seen:
            errors[f"slug_{i}"] = "duplicate_slug"
        else:
            seen.add(slug)
            chosen.append((c, slug))
    return chosen, errors


def default_options() -> dict[str, Any]:
    """Options of a new entry (spec 02 "Options")."""

    return {
        CONF_MAX_HOURLY_KWH: DEFAULT_MAX_HOURLY_KWH,
        CONF_DAILY_WINDOW_DAYS: DEFAULT_DAILY_WINDOW_DAYS,
        CONF_LONG_REFETCH_MONTHS: DEFAULT_LONG_REFETCH_MONTHS,
        CONF_IMPORT_HISTORY: DEFAULT_IMPORT_HISTORY,
    }


def _number(low: float, high: float, step: float = 1) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=low, max=high, step=step, mode=NumberSelectorMode.BOX)
    )


class EgAustriaOptionsFlow(OptionsFlowWithReload):
    """Import settings and comparison sources (spec 02, issue #50).

    ``init`` is a menu; each step saves only its own fields and keeps the
    others. The entry reloads when the options change.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which options to change."""

        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "import_settings",
                "sources",
                "surplus_price",
                "supplier_price",
            ],
        )

    async def async_step_import_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Plausibility bound, fetch windows and history import."""

        if user_input is not None:
            return self.async_create_entry(
                data=dict(self.config_entry.options)
                | {
                    CONF_MAX_HOURLY_KWH: float(user_input[CONF_MAX_HOURLY_KWH]),
                    CONF_DAILY_WINDOW_DAYS: int(user_input[CONF_DAILY_WINDOW_DAYS]),
                    CONF_LONG_REFETCH_MONTHS: int(user_input[CONF_LONG_REFETCH_MONTHS]),
                    CONF_IMPORT_HISTORY: bool(user_input[CONF_IMPORT_HISTORY]),
                }
            )
        current = default_options() | dict(self.config_entry.options)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MAX_HOURLY_KWH, default=current[CONF_MAX_HOURLY_KWH]
                ): _number(1, 1000, 0.5),
                vol.Required(
                    CONF_DAILY_WINDOW_DAYS, default=current[CONF_DAILY_WINDOW_DAYS]
                ): _number(31, 400),
                vol.Required(
                    CONF_LONG_REFETCH_MONTHS,
                    default=current[CONF_LONG_REFETCH_MONTHS],
                ): _number(1, 36),
                vol.Required(
                    CONF_IMPORT_HISTORY, default=current[CONF_IMPORT_HISTORY]
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="import_settings", data_schema=schema)

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick optional feed-in, grid consumption and PV statistics (issue #50).

        Prefilled from the Energy dashboard preferences until the step is
        saved once; after that the saved values (empty included) are shown.
        """

        errors: dict[str, str] = {}
        if user_input is not None:
            values = {
                key: str(user_input.get(key) or "").strip() or None
                for key in SOURCE_KEYS
            }
            errors = await async_validate_sources(self.hass, values)
            if not errors:
                return self.async_create_entry(
                    data=dict(self.config_entry.options) | values
                )
            suggested: Mapping[str, str | None] = values
        elif sources_configured(self.config_entry.options):
            suggested = configured_sources(self.config_entry)
        else:
            suggested = await async_energy_defaults(self.hass)
        schema = vol.Schema(
            {vol.Optional(key): StatisticSelector() for key in SOURCE_KEYS}
        )
        return self.async_show_form(
            step_id="sources",
            data_schema=self.add_suggested_values_to_schema(
                schema, {k: v for k, v in suggested.items() if v}
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Surplus and supplier price (ADR 0015, issue #73): the same steps for
    # both, ``kind`` is "surplus" or "supplier".

    def _save_price(self, kind: str, source: PriceSource | None) -> ConfigFlowResult:
        options = dict(self.config_entry.options)
        if source is None:
            options.pop(_PRICE_OPTION[kind], None)
        else:
            options[_PRICE_OPTION[kind]] = source.as_options()
        return self.async_create_entry(data=options)

    def _current_price(self, kind: str) -> PriceSource | None:
        return PriceSource.from_options(
            self.config_entry.options.get(_PRICE_OPTION[kind])
        )

    async def _price_price(
        self, kind: str, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how the price is provided, or none."""

        if user_input is not None:
            choice = user_input["source"]
            if choice == SourceType.NUMBER:
                return await self._price_number(kind)
            if choice == SourceType.SENSOR:
                return await self._price_sensor(kind)
            if choice == SourceType.TABLE:
                return await self._price_table(kind)
            return self._save_price(kind, None)
        current = self._current_price(kind)
        schema = vol.Schema(
            {
                vol.Required(
                    "source", default=current.source.value if current else "none"
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=["none", *(t.value for t in SourceType)],
                        translation_key="price_source",
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id=f"{kind}_price", data_schema=schema)

    async def _price_number(
        self, kind: str, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter fixed prices in ct/kWh, one ``YYYY-MM-DD: price`` line per period."""

        errors: dict[str, str] = {}
        current = self._current_price(kind)
        text = (
            format_periods(current.periods)
            if current and current.source is SourceType.NUMBER
            else ""
        )
        if user_input is not None:
            text = str(user_input.get("periods") or "")
            try:
                periods = parse_periods(text)
            except ValueError:
                errors["periods"] = "bad_periods"
            else:
                return self._save_price(
                    kind, PriceSource(SourceType.NUMBER, periods=tuple(periods))
                )
        schema = vol.Schema(
            {
                vol.Required("periods", default=text): TextSelector(
                    TextSelectorConfig(multiline=True)
                )
            }
        )
        return self.async_show_form(
            step_id=f"{kind}_number", data_schema=schema, errors=errors
        )

    async def _price_sensor(
        self, kind: str, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a price sensor with long-term statistics (EUR/kWh or ct/kWh)."""

        errors: dict[str, str] = {}
        current = self._current_price(kind)
        entity = (
            current.entity if current and current.source is SourceType.SENSOR else None
        )
        if user_input is not None:
            entity = user_input.get("entity")
            state = self.hass.states.get(entity) if entity else None
            unit = state.attributes.get("unit_of_measurement") if state else None
            if state is None or unit_factor(unit) is None:
                errors["entity"] = "bad_price_unit"
            elif state.attributes.get("state_class") is None:
                errors["entity"] = "no_price_statistics"
            else:
                return self._save_price(
                    kind, PriceSource(SourceType.SENSOR, entity=entity)
                )
        schema = vol.Schema(
            {
                vol.Required("entity"): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                )
            }
        )
        return self.async_show_form(
            step_id=f"{kind}_sensor",
            data_schema=self.add_suggested_values_to_schema(
                schema, {"entity": entity} if entity else {}
            ),
            errors=errors,
        )

    async def _price_table(
        self, kind: str, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an entity attribute with one price per month (e.g. ha-oemag)."""

        errors: dict[str, str] = {}
        current = self._current_price(kind)
        values: dict[str, Any] = {
            "attribute": DEFAULT_TABLE_ATTRIBUTE,
            "unit": UNIT_CT,
        }
        if current and current.source is SourceType.TABLE:
            values |= {
                "entity": current.entity,
                "attribute": current.attribute,
                "unit": current.unit,
            }
        elif kind == "surplus" and (found := self._find_table_entity()) is not None:
            values["entity"] = found
        if user_input is not None:
            values = dict(user_input)
            entity = values.get("entity")
            attribute = str(values.get("attribute") or DEFAULT_TABLE_ATTRIBUTE).strip()
            unit = str(values.get("unit") or UNIT_CT)
            state = self.hass.states.get(entity) if entity else None
            table = (
                month_table(state.attributes.get(attribute), unit) if state else None
            )
            if not table:
                errors["base"] = "bad_price_table"
            else:
                return self._save_price(
                    kind,
                    PriceSource(
                        SourceType.TABLE, entity=entity, attribute=attribute, unit=unit
                    ),
                )
        schema = vol.Schema(
            {
                vol.Required("entity"): EntitySelector(EntitySelectorConfig()),
                vol.Required("attribute"): TextSelector(),
                vol.Required("unit"): SelectSelector(
                    SelectSelectorConfig(
                        options=[UNIT_CT, UNIT_EUR], mode=SelectSelectorMode.LIST
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id=f"{kind}_table",
            data_schema=self.add_suggested_values_to_schema(
                schema, {k: v for k, v in values.items() if v}
            ),
            errors=errors,
        )

    async def async_step_surplus_price(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Surplus price: the price step."""

        return await self._price_price("surplus", user_input)

    async def async_step_surplus_number(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Surplus price: the number step."""

        return await self._price_number("surplus", user_input)

    async def async_step_surplus_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Surplus price: the sensor step."""

        return await self._price_sensor("surplus", user_input)

    async def async_step_surplus_table(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Surplus price: the table step."""

        return await self._price_table("surplus", user_input)

    async def async_step_supplier_price(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Supplier price: the price step."""

        return await self._price_price("supplier", user_input)

    async def async_step_supplier_number(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Supplier price: the number step."""

        return await self._price_number("supplier", user_input)

    async def async_step_supplier_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Supplier price: the sensor step."""

        return await self._price_sensor("supplier", user_input)

    async def async_step_supplier_table(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Supplier price: the table step."""

        return await self._price_table("supplier", user_input)

    def _find_table_entity(self) -> str | None:
        """Return the first entity with a valid default price table attribute."""

        for state in self.hass.states.async_all():
            raw = state.attributes.get(DEFAULT_TABLE_ATTRIBUTE)
            if raw and month_table(raw, UNIT_CT):
                return state.entity_id
        return None
