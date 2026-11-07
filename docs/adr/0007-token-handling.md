# 0007: Token handling and expiry warning

- Status: accepted; renewal is unresolved
- Date: 2026-11-07
- Depends on: EG Austria's answer about personal API tokens (forum 1140/32); a renewal test near expiry

## Context

- Auth is the `portal.jwtToken` cookie, a JWT with `exp` and `iat`. Observed lifetime: 31 days.
- Login is by emailed link only (no password), so the integration can't log in by itself.
- Renewal is unresolved: an 8-day-old token was not reissued by API or page visits; forum reports conflict (renewal for months with weekly visits, 1079/2, 1079/4; monthly renewal required, 1079/3; script users copy tokens by hand, 1140/12, 1140/28).
- API responses send `Set-Cookie` clearing the cookie, but the token keeps working.
- The portal client has `portal-session/token` create/list/revoke operations with no UI yet; their purpose is unconfirmed.
- The JWT carries the member's email address.

## Decision

- The user pastes the token in the config flow (raw value or `portal.jwtToken=...`). Store it in the config entry data. Never log it, never put it in diagnostics, never show it back.
- Decode the JWT payload locally without signature verification to read `exp` and `iat` only. Discard other claims.
- Send it as a `Cookie` header on each request with a client that has no cookie jar, so `Set-Cookie` responses are ignored.
- Expiry handling: a diagnostic timestamp sensor with `exp`; a repair issue `token_expiring` from `exp − 7 days`; on HTTP 401/403 or after `exp`, raise `ConfigEntryAuthFailed` so Home Assistant starts the reauth flow, where the user pastes a new token.
- Do not call `portal-session/token` create or revoke. Revisit if EG Austria confirms personal API tokens; they would plug into the same reauth path.

## Consequences

- Users have to paste a new token about monthly unless renewal turns out to work; the warning gives a week's notice.
- If a renewal mechanism is found (refresh near expiry, or personal tokens), this record is superseded.
- The token is stored like other integration credentials in Home Assistant's config storage, unencrypted on disk.
