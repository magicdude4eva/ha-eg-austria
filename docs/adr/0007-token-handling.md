# 0007: Token handling and expiry warning

- Status: accepted
- Date: 2026-11-07, updated 2026-11-21 (personal tokens tested; created by the config flow, issue #18)
- Depends on: the portal's personal token API, which has no UI yet (forum 1140/33*); a renewal test of the session JWT near expiry (fallback path only)

## Context

- The portal has two kinds of credential:
  - **Session JWT:** the `portal.jwtToken` cookie set at login (emailed link only, no password). JWT with `exp` and `iat`, lifetime 31 days, carries the member's email address. Renewal is unresolved: an 8-day-old token was not reissued; forum reports conflict (1079/2, 1079/3, 1079/4, 1140/12, 1140/28). API responses send `Set-Cookie` clearing the cookie, but the token keeps working.
  - **Personal access token:** created with `POST /v1/portal/portal-session/token` `{name, expiresAt?}` while logged in; listed and revoked (`DELETE .../token/{id}`) the same way. EG Austria staff prepared it (no UI yet) and invited members to use it (1140/33*). Tested 2026-11-21: opaque string with prefix `ecapi_` (not a JWT), returned once in the create response together with `id`, `name`, `expiresAt`, `revokedAt`, `lastUsedAt`, `createdAt`; default lifetime 1 year; sent as `Authorization: Bearer <token>` (as a cookie it is rejected); works for the read endpoints the integration needs; token management with the token itself is refused (401), so it can't create or revoke tokens; after revoke the API answers 401 "Access token revoked"; the portal tracks `lastUsedAt`. Creating a token sends the member an email ("Neuer Access-Token erstellt": name, valid until, programs can read but not change data).
- The first live run (S7) showed that members can't create a personal token themselves: the portal has no token page. Only the session cookie is within reach, and it lasts about a month.
- The token itself carries no expiry the integration can read, and listing tokens needs the browser session.

## Decision

- **Personal token is the credential the integration keeps;** the session JWT stays supported as a fallback.
- **The config flow creates the personal token** from a session cookie the user pastes (user, reauth and reconfigure steps), and only after a consent step that says it creates a read-only token named "Home Assistant" and that EG Austria sends an email about it. Then exactly one `POST /v1/portal/portal-session/token` with `{name: "Home Assistant", expiresAt: now + 365 days}`, sent with the session cookie. No retry: a timeout or 5xx is not repeated, because the token may exist anyway.
- **Revoke** only on request: reconfigure has a "revoke token" step (offered when the entry holds a token the flow created). It takes a fresh session cookie (the token can't manage tokens) and sends exactly one `DELETE /v1/portal/portal-session/token/{id}` with the stored id. Afterwards the flow offers to create a new token or keep the cookie, as above.
- Create and revoke live in their own module (`token_api.py`) that only the config flow imports. Runs (coordinator, services) use the GET-only client of ADR 0002 and never call `portal-session/token*`. The integration never lists tokens.
- Store only the returned token, its `id` and `expiresAt` (as `token_exp`); the session cookie used to create or revoke is dropped. The create response is validated (token pattern, id, future `expiresAt`); a response that doesn't match, or any error, means **fallback**: the flow says so and continues with the session cookie (stored as today, with its `exp`).
- A pasted `ecapi_` token is still accepted (for tokens made another way, and for a future portal token page). For it the user enters the expiry date (default today + 365 days), because the integration can't read it.
- The config flow detects the kind from the pasted value: `ecapi_…` → personal; three base64url segments (raw or `portal.jwtToken=…`) → session JWT. Anything else: `invalid_token_format`.
- Send a personal token as `Authorization: Bearer`, a session JWT as `Cookie: portal.jwtToken=`, with a client that has no cookie jar, so `Set-Cookie` is ignored.
- Expiry: session JWT → `exp` from the payload, decoded locally without signature check (other claims discarded). Created token → `expiresAt` from the create response. Pasted personal token → the date the user entered.
- Store the token and its kind and expiry (and the id of a created token) in the config entry data. Never log the token or the cookie, never put them or the token id in diagnostics, never show them back.
- Warning before expiry: repair `token_expiring` from 7 days (session JWT) or 30 days (personal token) before the stored expiry. On HTTP 401/403 or after the stored expiry, raise `ConfigEntryAuthFailed` so Home Assistant starts reauth, where the user pastes a new session cookie (and the flow creates a new token) or a token.

## Consequences

- Users paste a session cookie about once a year instead of monthly, without needing a portal token page.
- The integration has two write calls on the account. They are limited to the config flow, need a user action plus consent each time, and are whitelisted separately (ADR 0002). A bug in a run can't reach them.
- Each created token means one email from EG Austria. A failed create that timed out may still have created a token; the user then gets the email and can revoke it once a portal token page exists (the flow doesn't know its id).
- A token replaced by reauth or reconfigure without revoking stays valid until it expires; the reconfigure text says to revoke first.
- The stored expiry of a pasted personal token is the user's statement. If it is wrong, the 401 still leads to reauth, only without the advance warning.
- A revoked personal token fails at once with 401, which leads to reauth.
- If the portal changes the token API, the create falls back to the session cookie and only `token_api.py` and its validation change.
- The token is stored like other integration credentials in Home Assistant's config storage, unencrypted on disk.
