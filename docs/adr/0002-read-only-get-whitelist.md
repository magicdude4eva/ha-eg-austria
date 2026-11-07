# 0002: Read-only, with a whitelist of GET endpoints

- Status: accepted
- Date: 2026-11-07
- Depends on: portal API shape before the 2026-11-09 update

## Context

The member portal API is unofficial and undocumented. EG Austria staff allowed members to use it with the browser session cookie (forum 1140/4*). The same API also has state-changing endpoints: participation factors, Zero plans, payouts, terms, profile, session end, token creation. A bug or a changed URL must never change a member's contract or allocation.

## Decision

- All HTTP calls go through one client module that holds a fixed whitelist of `(method, path template)` pairs. Only `GET` is allowed. Any other method or path raises before a request is made.
- Phase 1 whitelist: `analysis-data`, `analysis-data/day-completeness`, `metering-point`, `/v1/portal/maintenance`, `/v1/version`. Phase 2 adds `billing/transactions` and `billing/pending-invoices`. Document downloads, token create/revoke and every `POST`/`PUT`/`PATCH`/`DELETE` are excluded.
- No login automation: the emailed-link login is never triggered by the integration.
- A unit test asserts the whitelist and that no non-GET method is reachable.

## Consequences

- Safe by construction against accidental writes.
- New features need an explicit whitelist change, reviewed in a PR.
- If EG Austria ships an official API, it gets its own client behind the same interface.
