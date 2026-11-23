# 06: Repairs, entities and diagnostics

Files: `repairs.py` (issue helpers), `sensor.py`, `diagnostics.py`, `redact.py`.

## Repair issues

Created with `issue_registry.async_create_issue(hass, DOMAIN, f"{issue}_{entry_id}", ...)`; deleted when the condition clears. All have translation keys in en and de and a `learn_more_url` to the README section ("Repairs and diagnostics": `#token-expiry`, `#portal-changed`, `#held-data`, `#portal-unreachable`, `#money`). `repairs.EntryRepairs` follows the coordinator (spec 03 "Hooks for S6"): each run's `IssueHints` create (True) or delete (False) an issue, `None` leaves it. Removing the entry deletes all its issues.

| Issue | Severity | Created when | Cleared when | Fixable |
|---|---|---|---|---|
| `token_expiring` | warning | now ≥ `token_exp − 7 days` (session JWT) or `− 30 days` (personal token; `token_exp` = `expiresAt` of a created token); checked at setup, after every run and by a timer at the start of the period | new token stored (config flow, S2) | no (text says how to paste a new token with the entry's Reconfigure) |
| reauth (core flow) | — | `AuthError` or `exp` passed → `ConfigEntryAuthFailed` | reauth completed | yes (core) |
| `api_changed` | error | `ApiChangedError` or `SchemaError`; translation key `stale_client` (same issue id) when the cause is a `StaleClientError` ("portal updated" 400): text also suggests updating the integration | next run passes shape validation | no; text asks for a diagnostics download and an issue report |
| `implausible_data` | warning | a plausibility or history rule blocked a write | next run with no blocking result, or `release_held` once no held days remain | no |
| `portal_unreachable` | warning | 3 consecutive calendar days without a successful run | next successful run | no |
| `price_missing` | warning | a run has surplus kWh on an overdue day without a surplus price (spec 04 "Money"); placeholder `date` = first such day | next run that writes and finds a price for every overdue hour (also when the price is removed) | no; text points to the options |
| `supplier_price_missing` | warning | the same for community kWh without a supplier price (import savings, issue #73) | as `price_missing` | no; text points to the options |

Placeholders in issue texts contain only the entry title, rule id, statistic key and dates.

## Diagnostic entities

One device per entry (`DeviceEntryType.SERVICE`, identifier and name from the entry, manufacturer "magicdude4eva", model "EG Austria member portal (unofficial)", `sw_version` the integration version from `manifest.json` (read at runtime), `configuration_url` the GitHub project https://github.com/magicdude4eva/ha-eg-austria (the "Visit" link; same as the manifest `documentation`); no serial number or hardware version, because metering point and community ids are personal data (issue #30)). All sensors `EntityCategory.DIAGNOSTIC`, `has_entity_name`, unique id `<entry_id>_<key>` (the entry id, not the entry unique id: that one changes when reconfigure remaps the location, which would orphan the entities). The values come from the entry and the store, so the sensors stay available between runs. They also stay available while the portal is unreachable, on purpose (issue #36, rule `entity-unavailable`): each one reports a fact that is still true without the portal (when the token expires, the last import, the stored complete-until date, the last run status, the last seen portal build), and `last_run_status` = `unavailable` together with the `portal_unreachable` repair is how an outage shows. Marking them unavailable would hide exactly the values that explain it.

| Key | Type | Value |
|---|---|---|
| `token_expires` | timestamp | `token_exp` |
| `data_complete_until` | date | last date of the contiguous run of days where both completeness flags are true, starting from the first activation |
| `meter_data_until` | date | last local day whose meter statistics (site consumption and generation, the earlier of the two) reach its last hour, from each run's newest known hour; stored as `meter_until` (issue #51) |
| `last_import` | timestamp | end of last successful run |
| `last_run_status` | enum | `ok`, `partial`, `skipped_maintenance`, `auth_failed`, `api_changed`, `unavailable`, `rate_limited`; kept in the store, so a restart shows the last finished run; unknown only before the first run |
| `portal_version` | text, disabled by default | short `gitCommit` |

No energy sensors (ADR 0003). Summary sensors with shares in percent, not diagnostic and without `state_class`, are described in ADR 0014 and `summary.py`: key `<value>_<period>` (`month_so_far`, `last_month`), unique id `<entry_id>_<value>_<period>`, attributes `period_start`, `period_end`, `numerator_kwh`, `denominator_kwh`. They are recomputed after startup and after each successful run (once the recorder queue is empty); diagnostics contain no summary values.

## Dashboard strategy (issue #52)

`dashboard.py` and `frontend/eg-austria-dashboard.js` (decided in spike #49):

- `async_setup` registers the websocket command `eg_austria/dashboard_config` and, when the frontend and http are loaded (after-dependencies), serves `frontend/` at `/eg_austria_frontend` and loads the script with `add_extra_js_url` (`?v=<version>` against stale caches).
- The script defines `ll-strategy-dashboard-eg-austria` and adds `{type: "eg-austria", strategyType: "dashboard"}` to `window.customStrategies`, so *Add dashboard* lists it. The user adds the dashboard once; the integration never creates or changes dashboards.
- `generate()` calls the websocket command, which returns per loaded entry: `entry_id`, `title`, `site`, `communities` (slug, name), `statistics` and `community_statistics` (only ids the recorder knows), `sources` (`feed_in`, `grid_consumption`, `pv`), `entities` (enabled diagnostic and summary sensors by key). One sections view per entry, core cards only; cards whose statistics, sources or sensors are missing are left out. Labels in English or German from the profile language.
- Tests: `tests/test_dashboard.py` (registration, websocket result) and `tests/js/dashboard.test.mjs` (`node --test`, CI job "Dashboard script").

## Services

| Service | Fields | Effect |
|---|---|---|
| `eg_austria.refetch` | `config_entry_id`, `start_date?`, `end_date?` | Manual run (spec 03) |
| `eg_austria.release_held` | `config_entry_id`, `statistic_key?`, `date?` | Clears held days so the next run writes them |

Implemented in S5; details (defaults, refusals, `released`) in spec 03 "Services". The run summary and `IssueHints` that S6 turns into issues and sensor values are in spec 03 "Hooks for S6".

## Diagnostics

`async_get_config_entry_diagnostics` returns the sections below, each passed through `redact.redact_tree()` (the section names are not redacted, although one is called `token`). It works for an entry that is not loaded (entry, token and statistics only). `async_get_device_diagnostics` returns the same for the entry's one service device, so "Download diagnostics" is also in the device page's menu.

- `entry`: entry version, options, `site_slug`, community slugs, retired slugs, number of metering points, the directions and the number of missing metering points seen in the last run; `data` redacted by `TO_REDACT`. The entry title is left out (users may rename it).
- `token`: kind, stored expiry, days left, `created_by_flow` (true when the entry holds a token the config flow created, ADR 0007); for a session JWT also `iat`. Nothing else from the token; the token id is never included.
- `store`: persistent state from spec 03 (dates and counters only), without the three snapshots below that have their own sections.
- `last_run`: from the current run summary, else the copy the coordinator stored after the last run (`snapshot.run_snapshot()`, issue #78), so it survives a restart. Type, window, chunk count, request count, duration, status, `stale_client`, redacted error, issue hints, validation result (rule id, severity, statistic key, chunk, day per finding; finding messages are left out because they may contain values), `schema_extra` key names, `written_rows` and `kept_stored_hours` (counts), `blocked` days, `corrections` (relative changes only).
- `setup` (issue #78): shape of the selected setup from the last run's `metering-point` response (`snapshot.setup_shape()`), stored so it survives a restart: number of locations, whether the configured location was found, metering points per direction at that location, selected points found and missing, and per selected point its direction and activations (community slug, or `other` for a community the entry doesn't select; portal community type such as `CC` / `RC_R`; status; `from` / `to` as `YYYY-MM`). No ids, names, numbers or values; points are listed by direction, not by id.
- `last_run.settlement` (issue #74): per `<slug>/<direction>/<YYYY-MM>` the settlement state (`settled`, `unmatched_correction`, `zero_provisional`) and the scale factor (4 decimals), plus counts of unknown and ignored transaction rows and the unmatched months; with an own-site Zero (issue #89) also `zero_rates` (per `<slug>/<direction>` the latest derived rate in €/kWh and its month: a tariff, not a member value) and `zero_months` (community and month with an estimated Zero; no kWh). No amounts. `store.settlement_digests` is left out of diagnostics (short hashes of amounts could be brute-forced).
- `portal`: `gitCommit`, `buildTime` (kept by the coordinator from the last run), maintenance windows (start and end only; the portal's title text is left out).
- `schema_fingerprint`: for the last `analysis-data`, `day-completeness` and `metering-point` responses, key → type name, recursively, first list element only. No values. The client records it when the response arrives, before parsing (`EgAustriaClient.schema_fingerprints`, `redact.schema_fingerprint()`), so the shape of a response that failed to parse is included. The coordinator stores it after every run (issue #78), so diagnostics after a restart show the last stored shapes until a new run replaces them.
- `statistics`: statistic ids (incl. surplus and feed-in compensation when a surplus price is set) with first and last stored hour (one `min`/`max` query over the recorder's statistics table). No sums or states.

### Debug export (ADR 0016, issue #84)

`eg_austria.debug_export` (fields `config_entry_id`, `days` 1-31, default 7; response `path`, `responses`, `failed_steps`) writes `eg_austria_debug_<site>_<UTC time>.json` to the configuration folder (`export.py`). Refused while a run holds the lock (`run_in_progress`). Requests, in order, through the normal client (whitelist, spacing): `version`, `maintenance`, `portal-session`, `metering-point`, `day-completeness` (window), then per selected community `analysis-data` Day and Hour for all selected metering points and Day per metering point. The client's `raw_sink` hands every decoded body to the export before parsing; a `SchemaError` or portal error ends only that step (`steps[].result`), an auth error stops the export.

File sections: `notice` (says values are included), `export_version`, `created`, versions, `window`, `entry` (options, slugs, synthetic ids), `setup` (`snapshot.setup_shape()`), `steps` (name, result, response indexes), `responses` (`endpoint`, `params`, `schema_fingerprint`, `raw`, and for `analysis-data` `key_presence` per direction: `points`, `missing` totals / day flags).

Redaction: ids are replaced by stable synthetic values (member `9000001`, location `5001`, metering points from `1001`, communities from `2001`, numbers `AT` + 31 digits) under `memberId`, `locationId`, `energyCommunityId`, `communityId`, `energyCommunityIds`, `desiredPartFactorParticipatingCommunityIds`, `meteringPointId`, `mpIds`, and `id` / `number` inside `memberMeteringpoints`, in bodies and request parameters alike; every other `TO_REDACT` key becomes `REDACTED`; every other string goes through `redact_text()` with the original ids. Energy and money values are kept.

The startup check (ADR 0013) also stores the `setup` shape from its `metering-point` response, so diagnostics show it before the first run.

### Redaction

`TO_REDACT = {"token", "token_id", "member_number", "location_id", "mp_ids", "id", "energyCommunityId", "communityId", "meteringPointId", "meteringPointNumber", "email", "name", "firstName", "lastName", "address", "street", "zip", "city", "iban", "bic", "instance", "traceId", "memberId", "locationId", "number", "houseNumber", "postalCode", "stairsFloorDoor", "displayName", "energyCommunityName", "fromName", "toName", "fromMeteringpointNumber", "toMeteringpointNumber", "ecNumber", "substationName"}` (the `metering-point` and `portal-session` keys from G1).

Rules beyond key-based redaction:

- `redact_tree()` replaces the value under any `TO_REDACT` key (at any depth, any type) and runs `redact_text()` on every other string, dict keys included. Numbers are kept (ids only appear under keys). ISO dates and timestamps are exempt from the id replacement, so a short id such as a location `12` cannot mangle `2026-12-01`; the patterns still apply.
- A `redact_text()` helper replaces in any string: JWT-like tokens (`eyJ[\w-]+\.[\w-]+\.[\w-]+`), personal tokens (`ecapi_[A-Za-z0-9_]+`), Austrian metering point numbers (`AT\d{6}[0-9A-Z]{25}`, 33 characters), email addresses, and the configured member number, location and metering point ids. Applied to every logged error message and every string in diagnostics.
- Community and location display names are redacted by key; the slugs stand in for them.
- No energy or money values in diagnostics in phase 1. If a user needs to share data for debugging, the README describes a separate, opt-in redacted export (phase 3, not specified here).

A test (spec 07) feeds a fixture containing every sensitive field and asserts that none of the synthetic sensitive strings appear in the diagnostics output or the logs.
