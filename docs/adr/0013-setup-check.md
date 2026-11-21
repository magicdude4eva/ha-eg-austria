# 0013: Test the portal before setup

- Status: accepted
- Date: 2026-11-21 (issue #36)
- Depends on: the portal's status mapping of spec 01 "Errors" (401/403, 5xx, HTML maintenance page)

## Context

Spec 03 said "setup itself never fetches": setup created the client, store and coordinator and returned, and the first request came from the background run 60-300 s after Home Assistant started. A revoked token or an unreachable portal then showed only as a run status and, for the token, as a reauth started by the run. The Home Assistant quality scale rule `test-before-setup` asks setup to check that the service can be reached and the credentials work, so that the entry ends in `setup_retry` or starts reauth at once.

## Decision

- `async_setup_entry` sends **one** GET after the existing stored-expiry check (ADR 0007): `get_metering_points(member)`. The import stays a background task (spec 03 "Startup").
- `metering-point` rather than `/v1/version`: it is member-scoped, so the portal must check the token, and it is small. Whether `/v1/version` checks the token was never verified; a probe that answers without checking it could not detect a revoked token. The endpoint is already in the whitelist (ADR 0002): no whitelist change.
- Mapping:
  - `AuthError` (401, 403 incl. `ForbiddenError`) → `ConfigEntryAuthFailed` (reauth), translation key `auth_failed`.
  - `PortalUnavailableError` (timeout, connection error, 5xx), `RateLimitedError` (429), or the 30 s setup bound passing → `ConfigEntryNotReady` (Home Assistant retries the setup with back-off), key `portal_unavailable`.
  - A non-JSON answer (`ApiChangedError` without status, as an HTML maintenance page): one more GET, `get_maintenance()`. If a window is active (± 30 min, as in a run) → `ConfigEntryNotReady`, key `portal_maintenance`. A 503 maintenance page is already `PortalUnavailableError`.
  - Any other answer (other `ApiChangedError`, `SchemaError`): setup goes on. The run raises the `api_changed` repair, which a setup retry could not show.
- Nothing is logged before raising; Home Assistant logs the setup result.

## Consequences

- One or two extra requests per setup and reload (restart, options change, reauth). A setup retry loop sends one request per retry, spaced by Home Assistant's back-off.
- Setup can wait up to 30 s for a slow portal, then retries.
- Spec 03 "Startup" no longer says setup never fetches.
