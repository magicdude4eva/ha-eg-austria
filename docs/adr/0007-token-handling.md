# 0007: Token handling and expiry warning

- Status: accepted
- Date: 2026-11-07, updated 2026-11-21 (personal tokens tested)
- Depends on: the portal's personal token API, which has no UI yet (forum 1140/33*); a renewal test of the session JWT near expiry (fallback path only)

## Context

- The portal has two kinds of credential:
  - **Session JWT:** the `portal.jwtToken` cookie set at login (emailed link only, no password). JWT with `exp` and `iat`, lifetime 31 days, carries the member's email address. Renewal is unresolved: an 8-day-old token was not reissued; forum reports conflict (1079/2, 1079/3, 1079/4, 1140/12, 1140/28). API responses send `Set-Cookie` clearing the cookie, but the token keeps working.
  - **Personal access token:** created with `POST /v1/portal/portal-session/token` `{name, expiresAt?}` while logged in; listed and revoked (`DELETE .../token/{id}`) the same way. EG Austria staff prepared it (no UI yet) and invited members to use it (1140/33*). Tested 2026-11-21: opaque string with prefix `ecapi_` (not a JWT), returned once; default lifetime 1 year; sent as `Authorization: Bearer <token>` (as a cookie it is rejected); works for the read endpoints the integration needs; token management with the token itself is refused (401), so it can't create or revoke tokens; after revoke the API answers 401 "Access token revoked"; the portal tracks `lastUsedAt`.
- The token carries no expiry the integration can read, and listing tokens needs the browser session, so the integration can't look up `expiresAt` itself.

## Decision

- **Personal token is the recommended credential;** the session JWT stays supported as a fallback.
- The user creates and revokes personal tokens outside the integration, with their browser session (portal UI once it exists; until then a documented one-off step in the README). The integration never calls `portal-session/token` create, list or revoke: they change account state or need the browser session (ADR 0002).
- The config flow detects the kind from the pasted value: `ecapi_…` → personal; three base64url segments (raw or `portal.jwtToken=…`) → session JWT. Anything else: `invalid_token_format`.
- Send a personal token as `Authorization: Bearer`, a session JWT as `Cookie: portal.jwtToken=`, with a client that has no cookie jar, so `Set-Cookie` is ignored.
- Expiry: session JWT → `exp` from the payload, decoded locally without signature check (other claims discarded). Personal token → expiry date entered by the user in the config flow, default today + 365 days (the portal default for a new token).
- Store the token and its kind and expiry in the config entry data. Never log it, never put it in diagnostics, never show it back.
- Warning before expiry: repair `token_expiring` from 7 days (session JWT) or 30 days (personal token) before the stored expiry. On HTTP 401/403 or after the stored expiry, raise `ConfigEntryAuthFailed` so Home Assistant starts reauth, where the user pastes a new token of either kind.

## Consequences

- With a personal token, users paste a token about once a year instead of monthly.
- The stored expiry of a personal token is the user's statement, not read from the portal. If it is wrong, the 401 still leads to reauth, only without the advance warning.
- A revoked personal token fails at once with 401, which leads to reauth.
- If the portal adds a UI or changes the token API, only the README step and the token-kind detection change.
- The token is stored like other integration credentials in Home Assistant's config storage, unencrypted on disk.
