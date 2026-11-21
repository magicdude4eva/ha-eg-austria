# 02: Config flow

Files: `config_flow.py`, `strings.json`, `translations/{en,de}.json`.

## Steps

### `user`: token

- Field `token` (password selector). Strip whitespace and surrounding quotes, then detect the kind (ADR 0007):
  - **Personal token** (recommended): starts with `ecapi_`, rest `[a-z0-9_]`. Kind `personal`.
  - **Session JWT** (fallback): raw JWT or a cookie string; also strip a leading `portal.jwtToken=` and anything after `;`. Kind `session`.
  - Anything else: `invalid_token_format`.
- Session JWT, local checks before any request: three base64url segments; payload decodes to JSON with numeric `exp`; `exp` in the future. Errors: `invalid_token_format`, `token_expired`. If `exp` is less than 7 days away, continue but show a notice in the next step's description.
- Personal token: field `token_expires` (date selector, shown only for this kind), default today + 365 days; must be in the future (`token_expired`). The integration can't read the real expiry (listing tokens needs the browser session).
- Member number: after the token checks, call `get_session()` (`GET /v1/portal/portal-session`, works with both token kinds). One entry in `members` → use its `memberId` and show no field. Several → a select labelled by `displayName` (shown in the flow only, never stored). None → error `no_member`. The JWT `sub` claim is not the member number (G1, T-MEM), so it is never used. If the chosen member has `mustAcceptPlatformTerms: true`, show a notice that the portal terms must be accepted in the browser first; continue anyway and let the online validation decide. The decoded email and other claims are never stored or shown.
- Validate online: `get_metering_points(member)`. Map `AuthError` → `invalid_auth`, `ApiChangedError`/`SchemaError` → `api_changed`, `PortalUnavailableError` → `cannot_connect`, otherwise `unknown`.
- Unique id: `sha256("eg_austria:" + member_number)[:16]` combined with the chosen location id hash, so the same member can add a second site in phase 3. Abort `already_configured` on duplicates.

### `site`: location and metering points

- If the member has one location, preselect it; otherwise a select labelled with the street and house number from `address` (shown in the flow only, never stored or logged).
- Multi-select of metering points of that location, labelled by direction and the last 4 characters of the metering point number. Default: all active ones. At least one required.
- `site_slug` (text, default `home`; the site's display name is "Home" (translated label), pattern `^[a-z][a-z0-9_]{0,19}$`), used in statistic ids (spec 04). Error `invalid_slug`.

### `communities`

- One row per community found in the activations of the selected metering points: checkbox (default on) and slug (default from type: `beg`, `eeg`, `eeg2`..., pattern as above, unique within the entry). Display the community name exactly as the portal returns it, and the active-from date. Names are never hard-coded or translated.
- Show a note that slugs and the site slug can't be changed later.

### Create entry

- `data`: `token`, `token_kind` (`personal` or `session`), `member_number`, `location_id`, `mp_ids`, `communities: [{id, slug, name}]` (`name` = last seen portal name, refreshed by the coordinator on each run; `slug` never changes), `site_slug`, `token_exp` (int: JWT `exp`, or end of the entered `token_expires` day in local time as a Unix timestamp), `run_minute` (random 180-359, minutes after midnight local, ADR 0004).
- `title`: `EG Austria Home` (translated site label; the address is not used in the title); the user can rename.
- After creation, the coordinator starts the initial import in the background (spec 03).

## Reauth

- Triggered by `ConfigEntryAuthFailed` (401/403, revoked token, or stored expiry passed).
- Step `reauth_confirm`: field `token` (either kind; `token_expires` for a personal token), same parsing. Validate with `get_session()` and `get_metering_points` for the stored member number. The new token must belong to the same member: if the stored member number is not in `members`, or the online call returns 403, error `wrong_account`.
- On success: update `token`, `token_kind` and `token_exp`, clear `token_expiring`, reload the entry.

## Reconfigure

`reconfigure` step to paste a new token before the old one expires (same checks as reauth), change the metering point selection, add a newly joined community (new slug), and remap ids if the portal changes them. Existing slugs are read-only.

## Options

| Option | Default | Use |
|---|---|---|
| `max_hourly_kwh` | 50 | Plausibility upper bound per hour and statistic (spec 05) |
| `daily_window_days` | 62 | Minimum daily window |
| `long_refetch_months` | 13 | Monthly long window |
| `import_history` | true | Run the initial import from the first activation date |

## Translations

English and German for all steps, errors, repair issues and entity names. The token step recommends a personal token (valid about a year) and links to the README section that explains how to create one; as a fallback it explains how to copy the session cookie from the browser's developer tools (valid about a month).
