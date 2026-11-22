# 0002: Read-only, with a whitelist of GET endpoints

- Status: accepted
- Date: 2026-11-07, updated 2026-11-21 (token create/revoke in the config flow, ADR 0007), 2026-11-22 (phase 2 allowlist, proposed)
- Depends on: portal API shape, re-checked after the 2026-11-09 update (G1, 2026-11-21)

## Context

The member portal API is unofficial and undocumented. EG Austria staff allowed members to use it with the browser session cookie (forum 1140/4*). The same API also has state-changing endpoints: participation factors, Zero plans, payouts, terms, profile, session end, token creation. A bug or a changed URL must never change a member's contract or allocation.

## Decision

- All HTTP calls go through one client module that holds a fixed whitelist of `(method, path template)` pairs. Only `GET` is allowed. Any other method or path raises before a request is made.
- Phase 1 whitelist: `analysis-data`, `analysis-data/day-completeness`, `metering-point`, `/v1/portal/maintenance`, `/v1/version`, and `GET /v1/portal/portal-session` (added 2026-11-21: the config flow reads the member number from it; no other `portal-session/*` path). **Phase 2 adds `GET /v1/portal/member/{member}/billing/transactions`** (proposed with ADR 0008; shape checked 2026-11-22). `billing/pending-invoices` is not added: it returns one net amount per month without community or direction, which the money statistics can't use. Document downloads and every `POST`/`PUT`/`PATCH`/`DELETE` are excluded from this client.
- One exception, outside this client (ADR 0007): a separate token module with its own whitelist of exactly `POST /v1/portal/portal-session/token` and `DELETE /v1/portal/portal-session/token/{id}`. Only the config flow imports it, after the user's consent; runs never reach it.
- No login automation: the emailed-link login is never triggered by the integration.
- A unit test asserts the whitelist and that no non-GET method is reachable from the run client; another asserts the token module's two entries and that only the config flow imports it.

## Consequences

- Safe by construction against accidental writes.
- New features need an explicit whitelist change, reviewed in a PR.
- If EG Austria ships an official API, it gets its own client behind the same interface.
