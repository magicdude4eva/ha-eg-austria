# 06: Repairs, entities and diagnostics

Files: `repairs.py` (issue helpers), `sensor.py`, `diagnostics.py`, `redact.py`.

## Repair issues

Created with `issue_registry.async_create_issue(hass, DOMAIN, f"{issue}_{entry_id}", ...)`; deleted when the condition clears. All have translation keys in en and de and a `learn_more_url` to the README section ("Repairs and diagnostics": `#token-expiry`, `#portal-changed`, `#held-data`, `#portal-unreachable`). `repairs.EntryRepairs` follows the coordinator (spec 03 "Hooks for S6"): each run's `IssueHints` create (True) or delete (False) an issue, `None` leaves it. Removing the entry deletes all its issues.

| Issue | Severity | Created when | Cleared when | Fixable |
|---|---|---|---|---|
| `token_expiring` | warning | now ≥ `token_exp − 7 days` (session JWT) or `− 30 days` (personal token); checked at setup, after every run and by a timer at the start of the period | new token stored (config flow, S2) | no (text says how to paste a new token with the entry's Reconfigure) |
| reauth (core flow) | — | `AuthError` or `exp` passed → `ConfigEntryAuthFailed` | reauth completed | yes (core) |
| `api_changed` | error | `ApiChangedError` or `SchemaError`; translation key `stale_client` (same issue id) when the cause is a `StaleClientError` ("portal updated" 400): text also suggests updating the integration | next run passes shape validation | no; text asks for a diagnostics download and an issue report |
| `implausible_data` | warning | a plausibility or history rule blocked a write | next run with no blocking result, or `release_held` once no held days remain | no |
| `portal_unreachable` | warning | 3 consecutive calendar days without a successful run | next successful run | no |

Placeholders in issue texts contain only the entry title, rule id, statistic key and dates.

## Diagnostic entities

One device per entry (`DeviceEntryType.SERVICE`, identifier and name from the entry, manufacturer "EG Austria (unofficial)"). All sensors `EntityCategory.DIAGNOSTIC`, `has_entity_name`, unique id `<entry_id>_<key>` (the entry id, not the entry unique id: that one changes when reconfigure remaps the location, which would orphan the entities). The values come from the entry and the store, so the sensors stay available between runs.

| Key | Type | Value |
|---|---|---|
| `token_expires` | timestamp | `token_exp` |
| `data_complete_until` | date | last date of the contiguous run of days where both completeness flags are true, starting from the first activation |
| `last_import` | timestamp | end of last successful run |
| `last_run_status` | enum | `ok`, `partial`, `skipped_maintenance`, `auth_failed`, `api_changed`, `unavailable`, `rate_limited`; unknown after a restart until the first run |
| `portal_version` | text, disabled by default | short `gitCommit` |

No energy sensors (ADR 0003).

## Services

| Service | Fields | Effect |
|---|---|---|
| `eg_austria.refetch` | `config_entry_id`, `start_date?`, `end_date?` | Manual run (spec 03) |
| `eg_austria.release_held` | `config_entry_id`, `statistic_key?`, `date?` | Clears held days so the next run writes them |

Implemented in S5; details (defaults, refusals, `released`) in spec 03 "Services". The run summary and `IssueHints` that S6 turns into issues and sensor values are in spec 03 "Hooks for S6".

## Diagnostics

`async_get_config_entry_diagnostics` returns the sections below, each passed through `redact.redact_tree()` (the section names are not redacted, although one is called `token`). It works for an entry that is not loaded (entry, token and statistics only).

- `entry`: entry version, options, `site_slug`, community slugs, retired slugs, number of metering points, the directions and the number of missing metering points seen in the last run; `data` redacted by `TO_REDACT`. The entry title is left out (users may rename it).
- `token`: kind, stored expiry, days left; for a session JWT also `iat`. Nothing else from the token.
- `store`: persistent state from spec 03 (dates and counters only).
- `last_run`: type, window, chunk count, request count, duration, status, `stale_client`, redacted error, issue hints, validation result (rule id, severity, statistic key, chunk, day per finding; finding messages are left out because they may contain values), `schema_extra` key names, `written_rows` and `kept_stored_hours` (counts), `blocked` days, `corrections` (relative changes only).
- `portal`: `gitCommit`, `buildTime` (kept by the coordinator from the last run), maintenance windows (start and end only; the portal's title text is left out).
- `schema_fingerprint`: for the last `analysis-data` and `day-completeness` responses, key → type name, recursively, first list element only. No values. The client records it when the response arrives (`EgAustriaClient.schema_fingerprints`, `redact.schema_fingerprint()`), so it is empty until the first run after a restart.
- `statistics`: statistic ids with first and last stored hour (one `min`/`max` query over the recorder's statistics table). No sums or states.

### Redaction

`TO_REDACT = {"token", "member_number", "location_id", "mp_ids", "id", "energyCommunityId", "communityId", "meteringPointId", "meteringPointNumber", "email", "name", "firstName", "lastName", "address", "street", "zip", "city", "iban", "bic", "instance", "traceId", "memberId", "locationId", "number", "houseNumber", "postalCode", "stairsFloorDoor", "displayName", "energyCommunityName", "fromName", "toName", "fromMeteringpointNumber", "toMeteringpointNumber", "ecNumber", "substationName"}` (the `metering-point` and `portal-session` keys from G1).

Rules beyond key-based redaction:

- `redact_tree()` replaces the value under any `TO_REDACT` key (at any depth, any type) and runs `redact_text()` on every other string, dict keys included. Numbers are kept (ids only appear under keys). ISO dates and timestamps are exempt from the id replacement, so a short id such as a location `12` cannot mangle `2026-12-01`; the patterns still apply.
- A `redact_text()` helper replaces in any string: JWT-like tokens (`eyJ[\w-]+\.[\w-]+\.[\w-]+`), personal tokens (`ecapi_[A-Za-z0-9_]+`), Austrian metering point numbers (`AT\d{6}[0-9A-Z]{25}`, 33 characters), email addresses, and the configured member number, location and metering point ids. Applied to every logged error message and every string in diagnostics.
- Community and location display names are redacted by key; the slugs stand in for them.
- No energy or money values in diagnostics in phase 1. If a user needs to share data for debugging, the README describes a separate, opt-in redacted export (phase 3, not specified here).

A test (spec 07) feeds a fixture containing every sensitive field and asserts that none of the synthetic sensitive strings appear in the diagnostics output or the logs.
