# 01: API client

Module `api.py` (async, `aiohttp` via Home Assistant's shared client session factory) plus `models.py`. No Home Assistant imports in `models.py`, so it can be tested alone.

## Base and request policy

- Base URL: `https://portal.eg-austria.at/api` (constant, not configurable).
- Auth by token kind (ADR 0007): personal token → header `Authorization: Bearer <token>`; session JWT → header `Cookie: portal.jwtToken=<token>`. The client takes `(token, kind)`; there is no fallback from one form to the other. Use a dedicated `ClientSession` with `cookie_jar=DummyCookieJar()` so `Set-Cookie` responses (which clear the cookie) are ignored.
- Headers: `Accept: application/json`, `User-Agent: ha-eg-austria/<version> (+<repo url>)`. No personal data in headers.
- Timeout: 60 s total per request. Requests strictly sequential; minimum 2 s between request starts.
- Retries inside one run: none for 4xx; one retry after 30 s for timeouts, connection errors and 502/503/504. Further retries are the scheduler's job.
- Redirects are not followed (`allow_redirects=False`); a 3xx is an `ApiChangedError`.
- Stored expiry: before every request the client compares now with the stored token expiry (`exp` of a session JWT, read locally; the entered date for a personal token) and raises `TokenExpiredError` (an `AuthError`) without sending anything.
- Spacing: `min_interval` (default 2 s) is a public attribute, so the coordinator can double it after a 429 (spec 03).
- Whitelist (ADR 0002): the client exposes one method per endpoint; an internal `_get(path_template, params)` checks the template against a frozen set and the method is always `GET`. There is no generic request method.
- Logging: URL path with ids replaced by `{member}`, `{mp}`; never headers, never token; response bodies only at debug level and only through the redaction helper (spec 06).

## Array parameters

Arrays are sent as repeated keys: `energyCommunityIds=1&energyCommunityIds=2&mpIds=11&mpIds=12` (as used by members after 2026-10-22, 1140/29-30; confirmed by G1 on build `20d1c18`, and a single-community request filters the community fields as expected). Both ids are the internal integer ids from `metering-point` (`energyCommunityId`, metering point `id`), not market-partner or metering point numbers. Omitting both means "all communities, all metering points" (1140/24*); the client always sends them explicitly so results don't change when the member joins another community.

## Endpoints (phase 1)

| Method | Path | Params | Returns |
|---|---|---|---|
| `get_metering_points(member)` | `/v1/portal/member/{member}/metering-point` | none | `MeteringPointInfo` |
| `get_analysis(member, aggregation, start, end, community_ids, mp_ids)` | `/v1/portal/member/{member}/analysis-data` | `aggregation`, `startDate`, `endDate`, `energyCommunityIds`, `mpIds` | `AnalysisData` |
| `get_day_completeness(member, start, end, community_ids, mp_ids)` | `/v1/portal/member/{member}/analysis-data/day-completeness` | `startDate`, `endDate`, `energyCommunityIds`, `mpIds` | `list[DayCompleteness]` |
| `get_maintenance()` | `/v1/portal/maintenance` | none | `list[Maintenance]` |
| `get_version()` | `/v1/version` | none | `PortalVersion` |
| `get_session()` | `/v1/portal/portal-session` | none | `SessionInfo` (config flow only, spec 02) |
| `get_transactions(member)` | `/v1/portal/member/{member}/billing/transactions` | none | `list[Transaction]` (phase 2, ADR 0008, issue #74) |

- `aggregation`: enum `Month, Week, Day, Hour, FifteenMinutes`; phase 1 uses `Hour`, plus `Day` for the autumn DST day (spec 04). An unknown value returns 400 `Validation failed (enum string is expected)`.
- `startDate`, `endDate`: `YYYY-MM-DD`, Austrian local dates; **both inclusive** (G1: `startDate = endDate` returns that one local day; 31 days return 31 × 24 hours, first point at local 00:00 of `startDate`, last at local 23:00 of `endDate`). The client enforces `end − start ≤ 30 days` for `Hour` and `FifteenMinutes` and raises `ValueError` before any request (a caller bug, not a portal error). `community_ids` and `mp_ids` must be non-empty lists of ints; the member number must be digits.
- `{member}`: member number, discovered through `get_session()` (spec 02).

## Response models

All numeric energy fields are `float | None` (JSON ints accepted and converted; bools and strings rejected). Unknown keys are kept in `model.extra` for diagnostics, not an error; `AnalysisData.extra_keys()` lists their names (never values) for `schema_extra`. Missing required keys raise `SchemaError` with the key path. Exception (issue #78): the portal leaves out the totals, and may leave out the day flags, of a direction whose point list is empty (an empty day, a site without generation); there a missing total is null and a missing flag false. With points in that direction a missing key is still S2, because that is the reduced body for a day outside a community's activation (issue #16). `SchemaError` also carries the spec 05 rule id (`S1` wrong container, `S2` missing key, `S3` wrong type, `S4` bad timestamp, `S9` bad completeness date), so the parser covers S1-S4 and the date part of S9; S5-S8 and the rest of S9 run on the parsed models in `validation.py`. Sensitive fields (names, addresses, number tails) are excluded from `repr`.

### `AnalysisData`

Top level (required unless noted):

| Key | Type | Use in phase 1 |
|---|---|---|
| `consumptionDataPoints` | list of `ConsumptionPoint` | yes |
| `generationDataPoints` | list of `GenerationPoint` | yes |
| `totalConsumption`, `totalGeneration`, `totalConsumptionFromCommunity`, `totalGenerationToCommunity`, `totalConsumptionInExcess`, `totalGenerationInExcess` | number or null; may be missing for a direction without data points (= null, issue #78) | validation (sum check) |
| `consumptionFaulty`, `consumptionFromCommunityFaulty`, `consumptionInExcessFaulty`, `generationFaulty`, `generationToCommunityFaulty`, `generationInExcessFaulty` | bool | diagnostics |
| other `total*`, `*ByFactor*`, `*Complete`, `*Percent*`, `*PartFactor*`, `*EnergyMix*`, `totalEarnings*`, `totalSpendings*` | any | ignored, optional |

Both point lists parse into one direction-neutral `EnergyPoint` (`direction`, `start` = `dateTimeFrom` in UTC, `value`, `community`, `excess`, `faulty`, `community_faulty`, optional `value_complete`, `community_complete`, `excess_complete`, `by_factor`, `by_factor_complete`, `by_factor_faulty`, `local_wall_time` = raw `dateTimeFromLocal`), so validation and statistics handle both directions with one code path. Response-level fields go to `AnalysisTotals` (the six totals) and `AnalysisFlags` (the six flags).

Consumption point (required): `dateTimeFrom` (ISO 8601 UTC string), `consumption`, `consumptionFromCommunity`, `consumptionInExcess`, `consumptionFaulty`, `consumptionFromCommunityFaulty`. Optional, parsed for diagnostics and tests: `consumptionComplete`, `consumptionFromCommunityComplete`, `consumptionInExcessComplete`, `consumptionByFactor`, `consumptionByFactorComplete`, `consumptionByFactorFaulty`, `dateTimeFromLocal`. Ignored: `date` (locale string, never parse), percent, part factor, energy mix, `spendings*`.

Generation point (required): `dateTimeFrom`, `generation`, `generationToCommunity`, `generationInExcess`, `generationFaulty`, `generationToCommunityFaulty`. Optional as above with `generation*` names. Ignored: `date`, percent, part factor, `earnings*`.

Notes (G1, build `20d1c18`, 2026-11-21):
- Key set unchanged from the pre-update baseline; no field was renamed or removed.
- Energy values come as JSON int when zero or whole and float otherwise; totals likewise. Percent and part-factor fields are number or null; `energyMix` is an object (`sun`, `water`, `wind`, `other`) or null.
- `dateTimeFrom` is `YYYY-MM-DDTHH:MM:SS.sssZ` (UTC). `dateTimeFromLocal` is the **local wall time with a misleading `Z` suffix** (for example `…T00:00:00.000Z` for local midnight); never parse it as UTC, never use it for keys. `date` is now `DD.MM.YY HH:MM` (it was a German month text before); still never parsed.
- `Day` points carry `dateTimeFrom` = local midnight in UTC (`…T23:00:00.000Z` in winter, `…T22:00:00.000Z` in summer).
- `*Complete` in `analysis-data` are **numbers**, unlike the booleans of the same name in `day-completeness`. On complete days every `xComplete` equals `x`.
- **Missing meter data is `0` with `*Faulty: true`, not `null`.** A day with no meter values yet returned `0` for every base field of every hour, with `consumptionFaulty`/`generationFaulty` true. Days with meter values but no community split have `*FromCommunityFaulty`/`*ToCommunityFaulty` true and community fields `0`. The parser keeps the flags per point; spec 04 decides what is unknown.
- Points arrive newest first; the parser sorts by `dateTimeFrom` ascending.
- `total*` equal the point sums of the same response exactly (rounding below 0.001 kWh).
- An empty day returns empty lists and null totals (1140/20); that is valid, not an error.
- **Autumn DST:** on the day clocks go back (25 local hours), `Hour` returns 24 points and `FifteenMinutes` 96: the first local 02:00-03:00 hour (00:00-01:00 UTC) is missing, and the hour before it (local 01:00) is flagged `*Faulty`. The `Day` value includes the missing hour. Spring DST (23 local hours) returns 23 correct points. Spec 04 handles the gap.

### Request scope (G1 finding)

In a request for one community (`energyCommunityIds=<c>`), `consumption`, `generation`, `consumptionInExcess` and `generationInExcess` are the same as in a request for all communities: the residual is global, not per community. Only `*FromCommunity`/`*ToCommunity` (and `*ByFactor`) are per community, and the values for all communities are the sum of the per-community values. So one request per community gives every phase-1 quantity; the all-communities request is not needed (spec 03, 04).

### `DayCompleteness`

`{date: "YYYY-MM-DD", consumptionComplete: bool | null, generationComplete: bool | null}`. One entry per day in the range, both ends inclusive, ascending by date. Days in the future are listed with `null` flags. Parse `null` as "not complete". A day missing from the list (not seen in G1) is also treated as not complete. The maximum range is untested (34 days worked); the client chunks long ranges to 13 months and treats a 400 as `ApiChangedError`.

### `MeteringPointInfo`

The response is a list of locations (G1 fingerprint; synthetic example):

```json
[
  {
    "locationId": "5001",
    "memberId": "9000001",
    "address": {"street": "Example street", "houseNumber": "1", "postalCode": "9999", "city": "Example", "stairsFloorDoor": null, "createdAt": "…", "updatedAt": "…"},
    "substationName": {"id": 3001, "name": "Example substation", "ecNumber": "…", "substation": "…", "meteringPointRange": "…", "consumerCount": 1, "producerCount": 1, "prosumerCount": 1},
    "memberMeteringpoints": [
      {
        "id": 1001,
        "number": "AT0000000000000000000000000001001",
        "name": "Example consumption",
        "consumptionOrGeneration": "Consumption",
        "locationId": "5001",
        "networkOperator": "…", "ecNumber": "…", "commercialUse": false,
        "installationNumber": null, "nameAddition": null, "isManualPartFactor": false,
        "sourceOfProduction": null, "statedPeakFeedIn": null, "statedYearlyConsumption": 3000,
        "desiredPartFactorParticipatingCommunityIds": [2001],
        "activations": [
          {
            "energyCommunityId": 2001,
            "energyCommunityName": "Example BEG",
            "energyCommunityType": "CC",
            "status": "Active",
            "begin": "2026-01-01", "end": null, "desiredBegin": null,
            "activePartFactor": 100, "confirmedPartFactor": 100, "pendingPartFactor": 100,
            "isManualPartFactor": false, "anforderungMsgDate": "…"
          }
        ],
        "sendingZeroPlans": [],
        "receivingZeroPlans": [{"id": 7001, "status": "Active", "begin": "2026-01-01", "end": null, "isAutoGenerated": true, "fromMeteringpointNumber": "…", "fromName": "…", "toMeteringpointNumber": "…", "toName": "…"}]
      }
    ]
  }
]
```

The adapter maps it into the phase-1 structure in one function, so a shape change touches one place:

```
locations[]:
  id                    <- locationId (string)
  label                 <- address street + houseNumber (config-flow select only; never logged, redacted in diagnostics)
  meteringPoints[]:     <- memberMeteringpoints[]
    id                  <- id (int, used as mpIds)
    number              <- number (full metering point number; config-flow labels only, never logged, redacted in diagnostics)
    number_tail         <- last 4 characters of number
    name                <- name (facility name, optional; config-flow labels only)
    sourceOfProduction  <- sourceOfProduction (optional, generation points; config-flow labels only)
    direction           <- consumptionOrGeneration: "Consumption" | "Generation" (anything else: SchemaError)
    activations[]:
      energyCommunityId <- energyCommunityId (int, used as energyCommunityIds)
      communityName     <- energyCommunityName
      communityType     <- energyCommunityType: "CC" (citizen energy community, BEG), "RC_R" (regional renewable energy community, EEG); other codes kept as they are
      activeFrom        <- begin (local date)
      activeTo          <- end (local date or null)
      status            <- status ("Active" seen)
      activePartFactor  <- activePartFactor (number or null; config-flow labels only)
```

Zero plans and the other participation factors go into `extra` only (snapshot values, never configuration); `activePartFactor` is also parsed for the metering point labels (issue #19) and never stored. `address`, `number`, `name`, `*Name` and `memberId` are sensitive and redacted (spec 06).

### `Transaction` (issue #74)

`parse_transactions()`: a JSON list (else S1); per row `amount` a number (S2 if missing, S3 if null or not a number), `date` an ISO date or timestamp (the first 10 characters are the local settlement date), `description` a string, `energyCommunityShortName` a string or null. Only these are kept (`Transaction(date, amount, description, community_name)`); `document` (its `fileName` holds the member's name), `invoiceNumber` and the status fields are dropped and never logged. Grouping and matching live in `settlement.py` (spec 04 "Settlement").

### `SessionInfo`

`GET /v1/portal/portal-session` → `{email: str, members: [{memberId: str, displayName: str, mustAcceptPlatformTerms: bool}]}`. `memberId` is the `{member}` path value (an int is accepted and converted to text). The model drops `email` at parse time and keeps `memberId`, `mustAcceptPlatformTerms` and `displayName`; `displayName` is only the label of the config-flow select when there are several members (spec 02), excluded from `repr`, never stored, logged or put into diagnostics. The JWT `sub` claim is **not** the member number; never derive it from the token.

### `Maintenance`

With no announced window the endpoint returns **200 with an empty body** (no content type), not `[]`; the client treats an empty body as an empty list. Entry field names `{id, start (UTC), end (UTC), title?}` are still **[verify]** (no window was announced at G1). Only start and end are used; until verified the parser also accepts `startDate`/`startTime`/`from` and `endDate`/`endTime`/`to`. The client remembers the last fetched list; `maintenance_active()` uses it to classify a non-JSON body (table below).

### `PortalVersion`

`{gitCommit: str, buildTime: str}` (confirmed; `gitCommit` is the full 40-character hash).

## Errors

Exceptions in `exceptions.py`, all subclasses of `EgAustriaError`:

| Condition | Exception | Effect (spec 03, 06) |
|---|---|---|
| 401 (including "Access token revoked"), or 403 whose `detail`/`message` mentions token, auth, JWT, session or login | `AuthError` | `ConfigEntryAuthFailed` → reauth |
| Stored expiry passed (checked before sending) | `TokenExpiredError` (subclass of `AuthError`) | same, no request sent |
| 403 other | `ForbiddenError` (subclass of `AuthError`) | treated as `AuthError` in phase 1; `wrong_account` in reauth (spec 02) |
| 400 whose `detail`/`message` contains "aktualisiert" (stale client, 1140/29) | `StaleClientError` (subclass of `ApiChangedError`) | repair `api_changed`, no writes |
| 400 other, 404, 405, 422, other 4xx, 3xx | `ApiChangedError` (with status and redacted message) | same |
| 429 | `RateLimitedError` (with `Retry-After` if any) | stop run, retry next schedule, double the spacing for the next run |
| 5xx, timeout, connection error | `PortalUnavailableError` (`retryable` set for timeout, connection error, 502-504) | retry next schedule; repair after 3 days |
| JSON decode error, empty body (except `maintenance`), wrong content type (HTML maintenance page) | `PortalUnavailableError` if a maintenance window is active, else `ApiChangedError` | as above |
| Model parse failure | `SchemaError` (with `rule` and `path`) | repair `api_changed`, no writes |
| Path template not in the whitelist (programming error) | `NotWhitelistedError` | nothing sent |

Error bodies are RFC 7807-like with content type `application/problem+json`: `type` (`about:blank`), `title` (German, e.g. `Ungültige Anfrage`), `status`, `statusCode`, `error` (e.g. `Bad Request`), `detail`, `message`, `instance`, `traceId` (G1). `instance` is the full request path and query, so it holds the member number and the community and metering point ids; the client never reads `instance` or `traceId` and puts only `detail`/`message`, passed through `redact_text()` with the member number and ids of the session, into exception messages and debug logs. The stale-client body (1140/29) could not be reproduced in G1 and stays as described.

## Token module (config flow only, ADR 0007)

Module `token_api.py`, class `PortalTokenClient(session, cookie)`. Only `config_flow.py` imports it (a test checks this); the coordinator and services can't reach it. It takes a session JWT only (sent as `Cookie: portal.jwtToken=`, same headers, timeout and no-redirect policy as above) and has its own frozen whitelist of exactly two entries:

| Method | Path | Body | Success |
|---|---|---|---|
| `create_token(name, expires_at)` | `POST /v1/portal/portal-session/token` | JSON `{"name": "Home Assistant", "expiresAt": "<UTC ISO 8601 with ms and Z>"}` | 200/201 with the token object |
| `revoke_token(token_id)` | `DELETE /v1/portal/portal-session/token/{id}` | none | 200/204 (body ignored); 404 counts as already gone |

- One request per call, **no retry** (a timeout may have created the token anyway), no spacing needed.
- `{id}` must match `^[A-Za-z0-9_-]{1,64}$` (ids seen are small integers) or the call raises `ValueError` before sending.
- Create response validation (any failure → `SchemaError`, or `ApiChangedError` for a non-JSON body): a JSON object; `token` a string matching `^ecapi_[a-z0-9_]+$`; `id` an int (not a bool) or a string matching the id pattern, kept as text; `expiresAt` an ISO 8601 timestamp with time zone, in the future; `revokedAt`, if present, null. Other keys (`name`, `lastUsedAt`, `createdAt`) are ignored. The result is `CreatedToken(id, token, expires_at)` with `token` excluded from `repr`.
- Status mapping as in "Errors" below (401/403 → `AuthError`, 5xx/timeout → `PortalUnavailableError`, other 4xx → `ApiChangedError`). Error bodies go through `redact_text()` (which also removes `ecapi_` tokens); the success body is never logged.

## Not in phase 1

`billing/*` other than `billing/transactions`, `energy-flow`, `average-day`, `part-factor-changes`, listing tokens (`GET portal-session/token`) and every other `portal-session/*` path except `portal-session` itself and the two token calls above, any document download.
