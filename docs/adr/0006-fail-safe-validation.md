# 0006: Fail-safe validation and repairs

- Status: accepted
- Date: 2026-11-07
- Depends on: field names and response shape before the 2026-11-09 update

## Context

The API changed in April 2026 (old endpoint dropped, 1140/19) and on 2026-10-22 (parameter rename and different ids, 1140/29-30); the backend rebuild continues (1140/31*). A stale client got HTTP 400 "Das Mitgliederportal wurde aktualisiert" (1140/29). Data errors have included negative feed-in (1373/2*) and a tenfold error (1326/3*). Writing bad data into statistics is worse than writing nothing, because statistics are kept for ever and users rarely notice.

## Decision

- Parse every response into typed models with strict checks on required keys and types (spec 05). Unknown extra keys are allowed and reported in diagnostics.
- Validate per run in two stages: **shape** (keys, types, timestamps) and **plausibility** (non-negative, hourly upper bound, identities between fields, point sums vs totals, no retroactive all-zero days).
- **Shape failure:** write nothing for the whole run, raise a repair issue `api_changed`, keep retrying daily. Clear the issue after a run validates.
- **Plausibility failure:** write nothing for the affected statistic and chunk, raise `implausible_data`, write the rest.
- Auth failures start reauth (ADR 0007). Network and server errors retry on the next schedule; after 3 consecutive failed days raise `portal_unreachable`.
- Never fall back to guessed values or old field names.

## Consequences

- An API change stops imports visibly within a day instead of corrupting history.
- Some legitimate but odd data may be held back until thresholds are adjusted; thresholds are options with safe defaults.
- Users need a way to report the change: the repair text links to the issue tracker and asks for a diagnostics download (redacted, spec 06).
