# 02: Config flow

Files: `config_flow.py`, `strings.json`, `translations/{en,de}.json`.

## Steps

### `user`: token

- Field `token` (password selector). Accept the raw JWT or a cookie string; strip whitespace, a leading `portal.jwtToken=`, surrounding quotes, and anything after `;`.
- Local checks, before any request: three base64url segments; payload decodes to JSON with numeric `exp`; `exp` in the future. Errors: `invalid_token_format`, `token_expired`.
- If `exp` is less than 7 days away, continue but show a notice in the next step's description.
- Field `member_number` (text): the portal member number. **[test T-MEM]** if the member number can be derived (JWT claim or a list endpoint), prefill it and hide the field when unambiguous. The decoded email and other claims are never stored or shown.
- Validate online: `get_metering_points(member)`. Map `AuthError` → `invalid_auth`, `ApiChangedError`/`SchemaError` → `api_changed`, `PortalUnavailableError` → `cannot_connect`, otherwise `unknown`.
- Unique id: `sha256("eg_austria:" + member_number)[:16]` combined with the chosen location id hash, so the same member can add a second site in phase 3. Abort `already_configured` on duplicates.

### `site`: location and metering points

- If the member has one location, preselect it; otherwise a select of location display names.
- Multi-select of metering points of that location, labelled by direction and the last 4 characters of the metering point number. Default: all active ones. At least one required.
- `site_slug` (text, default `home`; the site's display name is "Home" (translated label), pattern `^[a-z][a-z0-9_]{0,19}$`), used in statistic ids (spec 04). Error `invalid_slug`.

### `communities`

- One row per community found in the activations of the selected metering points: checkbox (default on) and slug (default from type: `beg`, `eeg`, `eeg2`..., pattern as above, unique within the entry). Display the community name exactly as the portal returns it, and the active-from date. Names are never hard-coded or translated.
- Show a note that slugs and the site slug can't be changed later.

### Create entry

- `data`: `token`, `member_number`, `location_id`, `mp_ids`, `communities: [{id, slug, name}]` (`name` = last seen portal name, refreshed by the coordinator on each run; `slug` never changes), `site_slug`, `token_exp` (int), `run_minute` (random 180-359, minutes after midnight local, ADR 0004).
- `title`: `EG Austria <location display name>`; the user can rename.
- After creation, the coordinator starts the initial import in the background (spec 03).

## Reauth

- Triggered by `ConfigEntryAuthFailed` (401/403 or `exp` passed).
- Step `reauth_confirm`: field `token` only, same parsing. Validate with `get_metering_points` for the stored member number. The new token must belong to the same member: if the online call returns 403 or a different member set, error `wrong_account`.
- On success: update `token` and `token_exp`, clear `token_expiring`, reload the entry.

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

English and German for all steps, errors, repair issues and entity names. The token step explains how to copy the cookie from the browser's developer tools, links to the project README, and says the token typically lasts about a month.
