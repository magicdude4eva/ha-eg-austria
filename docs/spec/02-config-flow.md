# 02: Config flow

Files: `config_flow.py`, `token.py` (parsing), `strings.json`, `translations/{en,de}.json`.

## Steps

### `user`: token

- Field `token` (password selector). Strip whitespace and surrounding quotes, then detect the kind (ADR 0007):
  - **Personal token** (recommended): starts with `ecapi_`, rest `[a-z0-9_]`. Kind `personal`.
  - **Session JWT** (fallback): raw JWT or a cookie string; also strip a leading `portal.jwtToken=` and anything after `;`. Kind `session`.
  - Anything else: `invalid_token_format`.
- Parsing lives in `token.parse_token()` (cookie prefix found anywhere in the value, so `other=1; portal.jwtToken=…` works too; whitespace and quotes are stripped again after the cookie is cut).
- Session JWT, local checks before any request: three base64url segments; payload decodes to JSON with numeric `exp`; `exp` in the future. Errors: `invalid_token_format`, `token_expired`. If `exp` is less than 7 days away, continue but show the notice step `expiring_soon` (form without fields, placeholder `days`) before the next step.
- Personal token: step `token_expiry` with field `token_expires` (date selector; HA forms can't show a field for one kind only, so it is its own step), default today + 365 days; the end of that day must be in the future (`token_expired`). No request is sent before this step. The integration can't read the real expiry (listing tokens needs the browser session).
- Member number: after the token checks, call `get_session()` (`GET /v1/portal/portal-session`, works with both token kinds). One entry in `members` → use its `memberId` and show no field. Several → step `member`, a select labelled by `displayName` (shown in the flow only, never stored). None → error `no_member`. The JWT `sub` claim is not the member number (G1, T-MEM), so it is never used. If the chosen member has `mustAcceptPlatformTerms: true`: when the online validation succeeds, show the notice step `accept_terms` and continue; when it fails with an `AuthError`, show error `terms_not_accepted` instead of `invalid_auth`. The decoded email and other claims are never stored or shown.
- Validate online: `get_metering_points(member)` (with one member in the same step as `get_session()`; with several after the `member` step, errors shown there). Map `TokenExpiredError` → `token_expired`, `AuthError` → `invalid_auth`, `ApiChangedError`/`SchemaError` → `api_changed`, `PortalUnavailableError`/`RateLimitedError` → `cannot_connect`, otherwise `unknown` (logged with the exception type only). Each step that calls the portal uses its own client session from `create_portal_session()` and closes it afterwards.
- Unique id: `sha256("eg_austria:" + member)[:16] + "_" + sha256("eg_austria:" + member + ":" + location_id)[:16]` (`entry_unique_id()`), so the same member can add a second site in phase 3. Set after the location is known; abort `already_configured` on duplicates.

### `location` and `site`: location and metering points

- If the member has one location, use it and skip the step; otherwise step `location`, a select labelled with the street and house number from `address` (shown in the flow only, never stored or logged). The metering points depend on the location, so this is its own step. No location → abort `no_location`; a location without metering points → abort `no_metering_points`.
- Step `site`: multi-select of metering points of that location, labelled by direction (Consumption/Generation, German Bezug/Einspeisung by the HA language) and the last 4 characters of the metering point number. Default: all active ones (an activation with status `Active` and no end before today); if none is active, all. At least one required (`no_metering_point`).
- `site_slug` (text, default `home`; the site's display name is "Home" (translated label), pattern `^[a-z][a-z0-9_]{0,19}$`), used in statistic ids (spec 04). Error `invalid_slug`. Statistic ids are global, so a site slug already used by another entry of the integration is refused (`slug_in_use`).
- The selected metering points must be in at least one community (`no_community`); more than 9 communities abort `too_many_communities` (strings have labels for rows 1-9).

### `communities`

- One row per community found in the activations of the selected metering points: fields `include_<n>` (checkbox, default on) and `slug_<n>` (default from type: `CC` → `beg`, `RC_R`/`RC_L` → `eeg`, then `eeg2`..., other codes `ec`; pattern as above, unique within the entry: `invalid_slug`, `duplicate_slug`). HA field labels can't carry runtime text, so the rows are numbered and the description (placeholder `communities`) lists `n. <name as the portal returns it> (BEG/EEG, earliest active-from date)`. Names are never hard-coded or translated. At least one community included (`no_community`).
- Show a note that slugs and the site slug can't be changed later.

### Create entry

- `data`: `token`, `token_kind` (`personal` or `session`), `member_number`, `location_id`, `mp_ids`, `communities: [{id, slug, name}]` (`name` = last seen portal name, refreshed by the coordinator on each run; `slug` never changes), `retired_slugs` (list, empty at setup; see Reconfigure), `site_slug`, `token_exp` (int: JWT `exp`, or 23:59:59 local time of the entered `token_expires` day as a Unix timestamp), `run_minute` (random 180-359, minutes after midnight local, ADR 0004).
- `options`: the defaults from the table below.
- `title`: `EG Austria Home` (fixed text: entry titles are not translated by HA; the address is not used in the title); the user can rename.
- Keys and option defaults are constants in `const.py`.
- After creation, the coordinator starts the initial import in the background (spec 03).

## Reauth

- Triggered by `ConfigEntryAuthFailed` (401/403, revoked token, or stored expiry passed).
- Step `reauth_confirm`: field `token` (either kind; then step `token_expiry` for a personal token), same parsing. Validate with `get_session()` and `get_metering_points` for the stored member number. The new token must belong to the same member: if the stored member number is not in `members`, or the online call raises `ForbiddenError` (403), error `wrong_account`. Other errors as in `user`.
- On success: update `token`, `token_kind` and `token_exp`, delete the repair issue `token_expiring_<entry_id>`, reload the entry (abort `reauth_successful`).

## Reconfigure

- Step `reconfigure`: optional field `token`. A new token gets the same checks as reauth (and `token_expiry` for a personal token); empty keeps the stored token, which is then used for the same checks.
- If the stored `location_id` is no longer returned, step `reconfigure_location` maps the site to a current location (unique id updated; abort `already_configured` if another entry has it).
- Step `reconfigure_site`: metering point multi-select (default: the stored ones still present). The site slug is shown in the description only (read-only).
- Communities are then split: **kept** (stored id still present; slug fixed, name refreshed), **orphans** (stored id gone) and **new**. If there is nothing new and no orphan, the entry is saved directly; otherwise step `reconfigure_communities` lists kept, orphaned and retired slugs in the description and shows `include_<n>`/`slug_<n>` rows for the new communities only. A kept slug can't be reused (`duplicate_slug`). Entering an orphaned or retired slug **remaps** it to the new community (portal id changed), so its statistics continue; the default slug is the orphan's slug when the names match, otherwise a new type default. Orphans not remapped move to `retired_slugs` and are never offered as defaults again.
- On save: `location_id`, `mp_ids`, `communities`, `retired_slugs`, and the token fields if a new token was given (then `token_expiring_<entry_id>` is deleted); reload (abort `reconfigure_successful`).

## Options

| Option | Default | Use |
|---|---|---|
| `max_hourly_kwh` | 50 | Plausibility upper bound per hour and statistic (spec 05) |
| `daily_window_days` | 62 | Minimum daily window |
| `long_refetch_months` | 13 | Monthly long window |
| `import_history` | true | Run the initial import from the first activation date |

Number selectors (box): `max_hourly_kwh` 1-1000 in steps of 0.5, `daily_window_days` 31-400, `long_refetch_months` 1-36. The options flow reloads the entry on change (`OptionsFlowWithReload`).

## Translations

English and German for all config-flow steps, errors, aborts and options (S2). Repair issues and entity names are added by S6 with those features. `translations/en.json` is identical to `strings.json`; URLs are passed as placeholders (`readme_url`). The token step recommends a personal token (valid about a year) and links to the README section `#token` that explains how to create one (README section written in S8); as a fallback it explains how to copy the session cookie from the browser's developer tools (valid about a month).
