"""Config flow for EG Austria (spec 02, ADR 0007, ADR 0012).

Steps of a new entry: ``user`` (token) → ``token_expiry`` (personal token
only) → ``expiring_soon`` / ``member`` / ``accept_terms`` (only when needed) →
``location`` (several locations only) → ``site`` → ``communities``.

Privacy: the token is never logged or shown back. Member display names,
addresses and community names appear only as labels in the flow; only the
community name is stored (as ``name``, the statistic display name, ADR 0012).
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
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import REPO_URL, EgAustriaClient, create_portal_session
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
    CONF_TOKEN,
    CONF_TOKEN_EXP,
    CONF_TOKEN_KIND,
    DEFAULT_DAILY_WINDOW_DAYS,
    DEFAULT_IMPORT_HISTORY,
    DEFAULT_LONG_REFETCH_MONTHS,
    DEFAULT_MAX_HOURLY_KWH,
    DOMAIN,
    PERSONAL_TOKEN_DEFAULT_DAYS,
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
from .token import ParsedToken, TokenFormatError, TokenKind, parse_token

_LOGGER = logging.getLogger(__name__)

SLUG_RE: Final = re.compile(r"[a-z][a-z0-9_]{0,19}")
DEFAULT_SITE_SLUG: Final = "home"
ENTRY_TITLE: Final = "EG Austria Home"
SESSION_NOTICE_DAYS: Final = 7
MAX_COMMUNITIES: Final = 9  # rows with translated labels in strings.json
RUN_MINUTE_RANGE: Final = (180, 359)  # ADR 0004
README_TOKEN_URL: Final = f"{REPO_URL}#token"
_EXPIRES: Final = "token_expires"

# Community type code → default slug stem and label (spec 02, ADR 0012).
_TYPE_SLUG: Final = {"CC": "beg", "RC_R": "eeg", "RC_L": "eeg"}
_TYPE_LABEL: Final = {"CC": "BEG", "RC_R": "EEG", "RC_L": "EEG"}
_DIRECTION_LABEL: Final = {
    "en": {Direction.CONSUMPTION: "Consumption", Direction.GENERATION: "Generation"},
    "de": {Direction.CONSUMPTION: "Bezug", Direction.GENERATION: "Einspeisung"},
}

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


@asynccontextmanager
async def _portal_client(
    hass: HomeAssistant, token: str, kind: TokenKind, token_exp: int | None
) -> AsyncIterator[EgAustriaClient]:
    """Yield a client on its own short-lived session, closed afterwards."""

    session = create_portal_session(hass)
    try:
        yield EgAustriaClient(session, token, kind, token_exp=token_exp)
    finally:
        await session.close()


def _is_active(mp: MeteringPoint, today: date) -> bool:
    return any(
        act.status == "Active" and (act.active_to is None or act.active_to >= today)
        for act in mp.activations
    )


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


def _community_line(n: int, c: _Community, slug: str | None = None) -> str:
    kind = _TYPE_LABEL.get(c.type, c.type)
    line = f"{n}. {c.name} ({kind}, {c.active_from.isoformat()})"
    return f"{line} → `{slug}`" if slug else line


class EgAustriaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the EG Austria config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Start with no state; nothing is persisted until the entry is made."""

        self._parsed: ParsedToken | None = None
        self._token_exp: int | None = None
        self._flow_step = "user"  # step that asked for the token
        self._session: SessionInfo | None = None
        self._member: Member | None = None
        self._mp_info: MeteringPointInfo | None = None
        self._location: Location | None = None
        self._mp_ids: list[int] = []
        self._site_slug = DEFAULT_SITE_SLUG
        self._found: list[_Community] = []
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
                        return await self._async_after_validation()
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
        return await self._async_after_validation()

    async def _async_validate_online(self) -> str | None:
        """Call the portal for the step that asked for the token."""

        if self._flow_step == "user":
            return await self._async_validate_new()
        return await self._async_validate_same_member()

    async def _async_after_validation(self) -> ConfigFlowResult:
        if self._flow_step == "user":
            return await self._async_next_new()
        if self._flow_step == "reauth_confirm":
            return self._finish_reauth()
        return await self._async_reconfigure_location()

    # ------------------------------------------------------------------
    # New entry

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the token (personal token recommended, session JWT fallback)."""

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
            description_placeholders={"readme_url": README_TOKEN_URL},
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
        """Notices, member choice and location in that order."""

        if self._notice_expiring:
            return await self.async_step_expiring_soon()
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
            return await self._async_next_new()
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
        return await self.async_step_site()

    def _mp_schema(self, default: list[str]) -> vol.Schema:
        assert self._location is not None  # noqa: S101
        lang = "de" if self.hass.config.language.startswith("de") else "en"
        labels = _DIRECTION_LABEL[lang]
        options = [
            SelectOptionDict(
                value=str(mp.id), label=f"{labels[mp.direction]} …{mp.number_tail}"
            )
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
            e.data.get(CONF_SITE_SLUG)
            for e in self._async_current_entries(include_ignore=False)
        } - {None}

    async def async_step_communities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One row per community: include and slug (frozen after setup)."""

        errors: dict[str, str] = {}
        taken: set[str] = set()
        defaults = []
        for c in self._found:
            slug = default_slug(c.type, taken)
            taken.add(slug)
            defaults.append(slug)
        if user_input is not None:
            chosen, errors = _read_rows(user_input, self._found, reserved=set())
            if not errors and not chosen:
                errors["base"] = "no_community"
            if not errors:
                return self._create_entry(chosen)
            defaults = [
                str(user_input.get(f"slug_{i + 1}", d)) for i, d in enumerate(defaults)
            ]
        schema: dict[Any, Any] = {}
        for i, slug in enumerate(defaults):
            include = user_input.get(f"include_{i + 1}", True) if user_input else True
            schema[vol.Required(f"include_{i + 1}", default=include)] = (
                BooleanSelector()
            )
            schema[vol.Required(f"slug_{i + 1}", default=slug)] = _SLUG_SELECTOR
        lines = [_community_line(i + 1, c) for i, c in enumerate(self._found)]
        return self.async_show_form(
            step_id="communities",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"communities": "\n".join(lines)},
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
        """Paste a new token of either kind for the same member."""

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
            description_placeholders={"readme_url": README_TOKEN_URL},
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

    def _token_updates(self) -> dict[str, Any]:
        assert self._parsed is not None  # noqa: S101
        return {
            CONF_TOKEN: self._parsed.token,
            CONF_TOKEN_KIND: self._parsed.kind.value,
            CONF_TOKEN_EXP: self._token_exp,
        }

    def _clear_token_expiring(self, entry: ConfigEntry) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, f"token_expiring_{entry.entry_id}")

    def _finish_reauth(self) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        self._clear_token_expiring(entry)
        return self.async_update_reload_and_abort(
            entry, data_updates=self._token_updates()
        )

    # ------------------------------------------------------------------
    # Reconfigure

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally paste a new token; then metering points and communities."""

        errors: dict[str, str] = {}
        if user_input is not None:
            raw = str(user_input.get(CONF_TOKEN) or "").strip()
            if raw:
                result = await self._async_token_submitted("reconfigure", raw)
            else:
                self._flow_step = "reconfigure"
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
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Optional(CONF_TOKEN): _TOKEN_SELECTOR}),
            errors=errors,
            description_placeholders={"readme_url": README_TOKEN_URL},
        )

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
        defaults = []
        for c in self._new:
            match = next(
                (o["slug"] for o in self._orphans if o.get("name") == c.name), None
            )
            if match is not None and match not in defaults:
                defaults.append(match)
                continue
            slug = default_slug(c.type, taken)
            taken.add(slug)
            defaults.append(slug)
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen, errors = _read_rows(user_input, self._new, reserved=kept_slugs)
            if not errors and not chosen and not self._kept:
                errors["base"] = "no_community"
            if not errors:
                return self._finish_reconfigure(chosen)
            defaults = [
                str(user_input.get(f"slug_{i + 1}", d)) for i, d in enumerate(defaults)
            ]
        schema: dict[Any, Any] = {}
        for i, slug in enumerate(defaults):
            include = user_input.get(f"include_{i + 1}", True) if user_input else True
            schema[vol.Required(f"include_{i + 1}", default=include)] = (
                BooleanSelector()
            )
            schema[vol.Required(f"slug_{i + 1}", default=slug)] = _SLUG_SELECTOR
        kept_lines = [f"- {k['name']} → `{k['slug']}`" for k in self._kept]
        orphan_lines = [f"- {o['name']} → `{o['slug']}`" for o in self._orphans]
        return self.async_show_form(
            step_id="reconfigure_communities",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "kept": "\n".join(kept_lines) or "-",
                "orphans": "\n".join(orphan_lines) or "-",
                "retired": ", ".join(f"`{s}`" for s in sorted(retired)) or "-",
                "new": "\n".join(
                    _community_line(i + 1, c) for i, c in enumerate(self._new)
                )
                or "-",
            },
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
        updates: dict[str, Any] = {
            CONF_LOCATION_ID: self._location.id,
            CONF_MP_IDS: self._mp_ids,
            CONF_COMMUNITIES: communities,
            CONF_RETIRED_SLUGS: sorted(retired),
        }
        if self._parsed is not None:
            updates |= self._token_updates()
            self._clear_token_expiring(entry)
        unique_id = entry_unique_id(entry.data[CONF_MEMBER_NUMBER], self._location.id)
        return self.async_update_reload_and_abort(
            entry, unique_id=unique_id, data_updates=updates
        )

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


def _read_rows(
    user_input: Mapping[str, Any], rows: list[_Community], *, reserved: set[str]
) -> tuple[list[tuple[_Community, str]], dict[str, str]]:
    """Read include/slug rows; slugs must match the pattern and be unique."""

    chosen: list[tuple[_Community, str]] = []
    errors: dict[str, str] = {}
    seen = set(reserved)
    for i, c in enumerate(rows, start=1):
        if not user_input.get(f"include_{i}", True):
            continue
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
    """Plausibility bound, fetch windows and history import (spec 02)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the options; the entry reloads on change."""

        if user_input is not None:
            return self.async_create_entry(
                data={
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
        return self.async_show_form(step_id="init", data_schema=schema)
