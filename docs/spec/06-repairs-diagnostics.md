# 06: Repairs, entities and diagnostics

Files: `repairs.py` (issue helpers), `sensor.py`, `diagnostics.py`, `redact.py`.

## Repair issues

Created with `issue_registry.async_create_issue(hass, DOMAIN, f"{issue}_{entry_id}", ...)`; deleted when the condition clears. All have translation keys in en and de and a `learn_more_url` to the README section.

| Issue | Severity | Created when | Cleared when | Fixable |
|---|---|---|---|---|
| `token_expiring` | warning | now ≥ `exp − 7 days` | new token stored | no (text says how to paste a new token with the entry's Reconfigure) |
| reauth (core flow) | — | `AuthError` or `exp` passed → `ConfigEntryAuthFailed` | reauth completed | yes (core) |
| `api_changed` | error | `ApiChangedError` or `SchemaError` | next run passes shape validation | no; text asks for a diagnostics download and an issue report |
| `implausible_data` | warning | a plausibility or history rule blocked a write | next run with no blocking result for that statistic, or `release_held` | no |
| `portal_unreachable` | warning | 3 consecutive calendar days without a successful run | next successful run | no |

Placeholders in issue texts contain only the entry title, rule id, statistic key and dates.

## Diagnostic entities

One device per entry (`DeviceEntryType.SERVICE`, name = entry title, manufacturer "EG Austria (unofficial)"). All sensors `EntityCategory.DIAGNOSTIC`, `has_entity_name`, unique id `<entry unique id>_<key>`.

| Key | Type | Value |
|---|---|---|
| `token_expires` | timestamp | `exp` |
| `data_complete_until` | date | last date of the contiguous run of days where both completeness flags are true, starting from the first activation |
| `last_import` | timestamp | end of last successful run |
| `last_run_status` | enum | `ok`, `partial`, `skipped_maintenance`, `auth_failed`, `api_changed`, `unavailable`, `rate_limited` |
| `portal_version` | text, disabled by default | short `gitCommit` |

No energy sensors (ADR 0003).

## Services

| Service | Fields | Effect |
|---|---|---|
| `eg_austria.refetch` | `config_entry_id`, `start_date?`, `end_date?` | Manual run (spec 03) |
| `eg_austria.release_held` | `config_entry_id`, `statistic_key?`, `date?` | Clears held days so the next run writes them |

## Diagnostics

`async_get_config_entry_diagnostics` returns:

- `entry`: options, `site_slug`, community slugs, counts of metering points by direction; `data` passed through `async_redact_data` with `TO_REDACT`.
- `token`: `exp`, `iat`, days left. Nothing else from the JWT.
- `store`: persistent state from spec 03 (dates and counters only).
- `last_run`: type, window, chunk count, request count, durations, status, validation result (rule ids, statistic keys, dates), `schema_extra` key names, `kept_stored_hours`, `corrections` (relative changes only).
- `portal`: `gitCommit`, `buildTime`, maintenance windows.
- `schema_fingerprint`: for the last `analysis-data` and `day-completeness` responses, key → type name, recursively, first list element only. No values.
- `statistics`: statistic ids with first and last stored hour. No sums or states.

### Redaction

`TO_REDACT = {"token", "member_number", "location_id", "mp_ids", "id", "energyCommunityId", "communityId", "meteringPointId", "meteringPointNumber", "email", "name", "firstName", "lastName", "address", "street", "zip", "city", "iban", "bic", "instance", "traceId"}`.

Rules beyond key-based redaction:

- A `redact_text()` helper replaces in any string: JWT-like tokens (`eyJ[\w-]+\.[\w-]+\.[\w-]+`), Austrian metering point numbers (`AT\d{6}[0-9A-Z]{25}`, 33 characters), email addresses, and the configured member number, location and metering point ids. Applied to every logged error message and every string in diagnostics.
- Community and location display names are replaced by their slugs.
- No energy or money values in diagnostics in phase 1. If a user needs to share data for debugging, the README describes a separate, opt-in redacted export (phase 3, not specified here).

A test (spec 07) feeds a fixture containing every sensitive field and asserts that none of the synthetic sensitive strings appear in the diagnostics output or the logs.
