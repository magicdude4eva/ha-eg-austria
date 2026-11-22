# 02: Config flow

Files: `config_flow.py`, `token.py` (parsing), `strings.json`, `translations/{en,de}.json`.

## Steps

### `user`: session cookie (or token)

- Field `token` (password selector). The description is a short how-to: log in to https://portal.eg-austria.at, open the browser's developer tools (Application/Storage → Cookies → portal.eg-austria.at), copy the value of `portal.jwtToken`. It also says that a personal token (`ecapi_…`) can be pasted instead. Strip whitespace and surrounding quotes, then detect the kind (ADR 0007):
  - **Session JWT** (the normal input): raw JWT or a cookie string; also strip a leading `portal.jwtToken=` and anything after `;`. Kind `session`.
  - **Personal token**: starts with `ecapi_`, rest `[a-z0-9_]`. Kind `personal`.
  - Anything else: `invalid_token_format`.
- Parsing lives in `token.parse_token()` (cookie prefix found anywhere in the value, so `other=1; portal.jwtToken=…` works too; whitespace and quotes are stripped again after the cookie is cut).
- Session JWT, local checks before any request: three base64url segments; payload decodes to JSON with numeric `exp`; `exp` in the future. Errors: `invalid_token_format`, `token_expired`.
- Pasted personal token: step `token_expiry` with field `token_expires` (date selector), default today + 365 days; the end of that day must be in the future (`token_expired`). No request is sent before this step. The integration can't read the real expiry (listing tokens needs the browser session).
- Member number: after the token checks, call `get_session()` (`GET /v1/portal/portal-session`, works with both token kinds). One entry in `members` → use its `memberId` and show no field. Several → step `member`, a select labelled by `displayName` (shown in the flow only, never stored). None → error `no_member`. The JWT `sub` claim is not the member number (G1, T-MEM), so it is never used. If the chosen member has `mustAcceptPlatformTerms: true`: when the online validation succeeds, show the notice step `accept_terms` and continue; when it fails with an `AuthError`, show error `terms_not_accepted` instead of `invalid_auth`. The decoded email and other claims are never stored or shown.
- Validate online: `get_metering_points(member)` (with one member in the same step as `get_session()`; with several after the `member` step, errors shown there). Map `TokenExpiredError` → `token_expired`, `AuthError` → `invalid_auth`, `ApiChangedError`/`SchemaError` → `api_changed`, `PortalUnavailableError`/`RateLimitedError` → `cannot_connect`, otherwise `unknown` (logged with the exception type only). Each step that calls the portal uses a client on the shared session from `get_portal_session()` (one per Home Assistant instance, no cookie jar); the flow never closes it, Home Assistant owns it (issue #17).
- Unique id: `sha256("eg_austria:" + member)[:16] + "_" + sha256("eg_austria:" + member + ":" + location_id)[:16]` (`entry_unique_id()`), so the same member can add a second site in phase 3. Set after the location is known; abort `already_configured` on duplicates.

### `create_token`: consent and token creation (session cookie only, issue #18)

- After a session JWT passed the online validation, menu step `create_token`: for a new entry only after `member`, `accept_terms` and `location` and the duplicate check (`already_configured`), so a duplicate never creates a token; in `reauth_confirm` and `reconfigure_token` right after the member check; after a successful `revoke_token`. The description says that the integration creates a **read-only** access token named "Home Assistant", valid one year, that EG Austria sends an email about it, and that the cookie itself is not stored. Options:
  - `create_token_confirm`: exactly one `PortalTokenClient.create_token("Home Assistant", now + 365 days)` with the pasted cookie (spec 01 "Token module"). On success the flow continues with the returned token as kind `personal`, `token_exp` = `expiresAt`, `token_id` = `id`; the cookie is dropped and the `expiring_soon` notice doesn't apply. No `token_expiry` step.
  - `use_session`: continue with the session cookie as the stored token (the fallback of ADR 0007).
- On any create error (HTTP error, timeout, response that fails validation): form step `create_failed` without fields, showing the error (`create_failed_auth`, `create_failed_connect`, `create_failed_response`) and saying that setup continues with the session cookie (valid about a month; a token can be created later via Reconfigure). Submit continues with the cookie. The POST is never repeated in the same flow.
- With the cookie kept: if `exp` is less than 7 days away, show the notice step `expiring_soon` (form without fields, placeholder `days`) before the next step (new entry only).
- The next step: `expiring_soon` (cookie kept, less than 7 days) or `site` for a new entry, finish for reauth and revoke, `reconfigure_location`/`reconfigure_site` for reconfigure.

### `location` and `site`: location and metering points

- If the member has one location, use it and skip the step; otherwise step `location`, a select labelled with the street and house number from `address` (shown in the flow only, never stored or logged). The metering points depend on the location, so this is its own step. No location → abort `no_location`; a location without metering points → abort `no_metering_points`.
- Step `site`: multi-select of metering points of that location (list mode). Each option label (issue #19): direction (Consumption/Generation, German Bezug/Einspeisung by the HA language), the **full** metering point number, the facility `name` and, for generation points, `sourceOfProduction` in brackets, then `activePartFactor` per community activation (`<community name> <factor> %`). Missing values are left out. The full number and names appear only in this local form; logs, diagnostics and the entry title stay redacted. Default: all active ones (an activation with status `Active` and no end before today); if none is active, all. At least one required (`no_metering_point`).
- `site_slug` (text, default `home`; the site's display name is "Home" (translated label), pattern `^[a-z][a-z0-9_]{0,19}$`), used in statistic ids (spec 04). Error `invalid_slug`. Statistic ids are global, so a site slug already used by another entry of the integration is refused (`slug_in_use`).
- The selected metering points must be in at least one community (`no_community`); more than 9 communities abort `too_many_communities` (strings have labels for rows 1-9).

### `communities` and `community_slugs` (issue #20)

- Step `communities`: field `communities`, a multi-select (list mode) with one option per community found in the activations of the selected metering points, in portal order. HA field labels can't carry runtime text, but select option labels can, so each option is labelled `<name as the portal returns it> (BEG/EEG, member since <earliest active-from date>) → <slug>`. Names are never hard-coded or translated. Default: all selected. At least one (`no_community`). No list in the description.
- Field `edit_slugs` (checkbox, default off). Default slugs from the type: `CC` → `beg`, `RC_R`/`RC_L` → `eeg`, then `eeg2`..., other codes `ec`. Off → the entry is created with the default slugs. On → step `community_slugs`.
- Step `community_slugs`: fields `slug_<n>` for the selected communities in order (n = 1-9; strings label them "Slug 1"..), defaults as above. The description maps each number to its community (`<n>: <name> (BEG/EEG)`). Pattern `^[a-z][a-z0-9_]{0,19}$`, unique within the entry: `invalid_slug`, `duplicate_slug`.
- Both steps note that slugs and the site slug can't be changed later.

### Create entry

- `data`: `token`, `token_kind` (`personal` or `session`), `token_id` (only for a token the flow created; never for a cookie or a pasted token), `member_number`, `location_id`, `mp_ids`, `communities: [{id, slug, name}]` (`name` = last seen portal name, refreshed by the coordinator on each run; `slug` never changes), `retired_slugs` (list, empty at setup; see Reconfigure), `site_slug`, `token_exp` (int: JWT `exp`, `expiresAt` of a created token, or 23:59:59 local time of the entered `token_expires` day as a Unix timestamp), `run_minute` (random 180-359, minutes after midnight local, ADR 0004).
- `options`: the defaults from the table below.
- `title`: `EG Austria Home` (fixed text: entry titles are not translated by HA; the address is not used in the title); the user can rename.
- Keys and option defaults are constants in `const.py`.
- After creation, the coordinator starts the initial import in the background (spec 03).

## Reauth

- Triggered by `ConfigEntryAuthFailed` (401/403, revoked token, or stored expiry passed).
- Step `reauth_confirm`: field `token` (session cookie, or a personal token; then step `token_expiry` for a pasted personal token), same parsing and how-to as `user`. A session cookie then goes through `create_token` after the member check (the same path as `user`). Validate with `get_session()` and `get_metering_points` for the stored member number. The new token must belong to the same member: if the stored member number is not in `members`, or the online call raises `ForbiddenError` (403), error `wrong_account`. Other errors as in `user`.
- On success: update `token`, `token_kind`, `token_exp` and `token_id` (removed unless a token was created), delete the repair issue `token_expiring_<entry_id>`, reload the entry (abort `reauth_successful`).

## Reconfigure

- Step `reconfigure`: when the entry holds a token the flow created (`token_id`), a menu with `reconfigure_token` (change token and settings) and `revoke_token`; otherwise straight to `reconfigure_token`.
- Step `reconfigure_token`: optional field `token`. A new value gets the same checks as reauth (`create_token` for a session cookie, `token_expiry` for a pasted personal token); empty keeps the stored token, which is then used for the same checks. The description says to revoke a token created by the flow first, because a replaced token stays valid until it expires.
- Step `revoke_token` (issue #18): field `token`, a fresh session cookie (a personal token can't manage tokens: `session_cookie_required`). Local checks as in `user`, then `get_session()` and `get_metering_points` for the stored member (`wrong_account` as in reauth), then exactly one `PortalTokenClient.revoke_token(<stored token_id>)`. Errors: `AuthError` → `invalid_auth`, others mapped as in `user`; nothing changes in the entry. On success (404 included) the stored token no longer works, so the flow goes to `create_token` with the same cookie: create a new token or keep the cookie. Then the token fields are updated, `token_expiring_<entry_id>` deleted, the entry reloaded (abort `token_revoked`).
- If the stored `location_id` is no longer returned, step `reconfigure_location` maps the site to a current location (unique id updated; abort `already_configured` if another entry has it).
- Step `reconfigure_site`: metering point multi-select with the labels of `site` (default: the stored ones still present). The site slug is shown in the description only (read-only).
- Communities are then split: **kept** (stored id still present; slug fixed, name refreshed), **orphans** (stored id gone) and **new**. If there is nothing new and no orphan, the entry is saved directly; otherwise step `reconfigure_communities` lists kept, orphaned and retired slugs in the description and shows the new communities as a multi-select labelled like `communities` (with the default slug), plus `edit_slugs`; with `edit_slugs` on, step `reconfigure_community_slugs` has `slug_<n>` fields for the selected new communities (description maps numbers to names). A kept slug can't be reused (`duplicate_slug`). Entering an orphaned or retired slug **remaps** it to the new community (portal id changed), so its statistics continue; the default slug is the orphan's slug when the names match, otherwise a new type default. Orphans not remapped move to `retired_slugs` and are never offered as defaults again.
- On save: `location_id`, `mp_ids`, `communities`, `retired_slugs`, and the token fields (`token_id` as in reauth) if a new token was given (then `token_expiring_<entry_id>` is deleted); reload (abort `reconfigure_successful`).

## Options

| Option | Default | Use |
|---|---|---|
| `max_hourly_kwh` | 50 | Plausibility upper bound per hour and statistic (spec 05) |
| `daily_window_days` | 62 | Minimum daily window |
| `long_refetch_months` | 13 | Monthly long window |
| `import_history` | true | Run the initial import from the first activation date |

Number selectors (box): `max_hourly_kwh` 1-1000 in steps of 0.5, `daily_window_days` 31-400, `long_refetch_months` 1-36. The options flow reloads the entry on change (`OptionsFlowWithReload`).

The options flow opens with a menu (`init`): `import_settings` (the four options above) and `sources` (below). Each step saves only its own keys and keeps the others.

### Comparison sources (issue #50)

| Option | Default | Use |
|---|---|---|
| `source_feed_in` | Energy prefs: first grid source's `stat_energy_to` | The user's live return-to-grid statistic |
| `source_grid_consumption` | Energy prefs: first grid source's `stat_energy_from` | The user's live grid-import statistic |
| `source_pv` | Energy prefs: first solar source's `stat_energy_from` | Total PV production |

- `StatisticSelector` per field, all optional; any brand, entity or external statistic. Stored as the statistic id or `None`; once the step was saved, the stored values (empty included) are shown instead of the defaults.
- Defaults are read from the Energy manager's preferences and never written (ADR 0011); the older `flow_from`/`flow_to` lists are read too. Unreadable preferences give empty fields.
- Validation on submit (`sources.async_validate_sources`, recorder `async_list_statistic_ids`): `has_sum`, a unit of `EnergyConverter.VALID_UNITS`, not an `eg_austria:` statistic, no statistic in two fields. Errors per field: `source_not_statistic`, `source_not_energy`, `source_own_statistic`, `source_duplicate`.
- No statistic ids change. Consumers: summary sensors (#51), the generated dashboard (#52), live supplier statistics (#53).

## Translations

English and German for all config-flow steps, errors, aborts and options (S2). Repair issues and entity names are added by S6 with those features. `translations/en.json` is identical to `strings.json`; URLs are passed as placeholders (`readme_url`). The token steps explain how to copy the session cookie from the browser's developer tools and link to the README section `#token` (written in S8: the cookie, the token email, how to revoke). Existing entries keep their data, slugs and statistic ids; S7b changes no stored key except the optional `token_id`.
