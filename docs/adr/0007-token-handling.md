# 0007: Token handling and expiry warning

- Status: accepted; renewal is unresolved; personal tokens pending a test
- Date: 2026-11-07, updated 2026-11-13
- Depends on: a test of the personal token API (invited by EG Austria, forum 1140/33*); a renewal test near expiry

## Context

- Auth is the `portal.jwtToken` cookie, a JWT with `exp` and `iat`. Observed lifetime: 31 days.
- Login is by emailed link only (no password), so the integration can't log in by itself.
- Renewal is unresolved: an 8-day-old token was not reissued by API or page visits; forum reports conflict (renewal for months with weekly visits, 1079/2, 1079/4; monthly renewal required, 1079/3; script users copy tokens by hand, 1140/12, 1140/28).
- API responses send `Set-Cookie` clearing the cookie, but the token keeps working.
- The portal client has `portal-session/token` create/list/revoke operations with no UI yet. EG Austria staff confirmed these are personal tokens they prepared, still without a UI, and invited members to use and test them (1140/33*). Lifetime, how such a token is sent, and the `create` request body are not known yet.
- The JWT carries the member's email address.

## Decision

- The user pastes the token in the config flow (raw value or `portal.jwtToken=...`). Store it in the config entry data. Never log it, never put it in diagnostics, never show it back.
- Decode the JWT payload locally without signature verification to read `exp` and `iat` only. Discard other claims.
- Send it as a `Cookie` header on each request with a client that has no cookie jar, so `Set-Cookie` responses are ignored.
- Expiry handling: a diagnostic timestamp sensor with `exp`; a repair issue `token_expiring` from `exp − 7 days`; on HTTP 401/403 or after `exp`, raise `ConfigEntryAuthFailed` so Home Assistant starts the reauth flow, where the user pastes a new token.
- The integration never calls `portal-session/token` create or revoke: both change account state (ADR 0002). Personal tokens are created and revoked by the user, outside the integration (portal UI once it exists, or a documented one-off step).
- Once a test confirms how personal tokens work, the config flow accepts them as an alternative to the cookie token. They use the same storage, expiry handling and reauth path; only the header form may differ. Until then the cookie token is the supported path.

## Consequences

- Users have to paste a new token about monthly unless renewal turns out to work; the warning gives a week's notice.
- If personal tokens live much longer than 31 days, monthly pasting goes away for users who switch to them; the expiry warning still applies.
- If a renewal mechanism is found (refresh near expiry, or personal tokens), this record is updated or superseded.
- The token is stored like other integration credentials in Home Assistant's config storage, unencrypted on disk.
