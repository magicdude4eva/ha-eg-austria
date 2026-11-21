# 01: API client

Module `api.py` (async, `aiohttp` via Home Assistant's shared client session factory) plus `models.py`. No Home Assistant imports in `models.py`, so it can be tested alone.

## Base and request policy

- Base URL: `https://portal.eg-austria.at/api` (constant, not configurable).
- Auth by token kind (ADR 0007): personal token → header `Authorization: Bearer <token>`; session JWT → header `Cookie: portal.jwtToken=<token>`. The client takes `(token, kind)`; there is no fallback from one form to the other. Use a dedicated `ClientSession` with `cookie_jar=DummyCookieJar()` so `Set-Cookie` responses (which clear the cookie) are ignored.
- Headers: `Accept: application/json`, `User-Agent: ha-eg-austria/<version> (+<repo url>)`. No personal data in headers.
- Timeout: 60 s total per request. Requests strictly sequential; minimum 2 s between request starts.
- Retries inside one run: none for 4xx; one retry after 30 s for timeouts, connection errors and 502/503/504. Further retries are the scheduler's job.
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

- `aggregation`: enum `Month, Week, Day, Hour, FifteenMinutes`; phase 1 uses `Hour`, plus `Day` for the autumn DST day (spec 04). An unknown value returns 400 `Validation failed (enum string is expected)`.
- `startDate`, `endDate`: `YYYY-MM-DD`, Austrian local dates; **both inclusive** (G1: `startDate = endDate` returns that one local day; 31 days return 31 × 24 hours, first point at local 00:00 of `startDate`, last at local 23:00 of `endDate`). The client enforces `end − start ≤ 30 days` for `Hour`.
- `{member}`: member number, discovered through `get_session()` (spec 02).

## Response models

All numeric energy fields are `float | None` (JSON ints accepted and converted). Unknown keys are kept in `model.extra` for diagnostics, not an error. Missing required keys raise `SchemaError` with the key path.

### `AnalysisData`

Top level (required unless noted):

| Key | Type | Use in phase 1 |
|---|---|---|
| `consumptionDataPoints` | list of `ConsumptionPoint` | yes |
| `generationDataPoints` | list of `GenerationPoint` | yes |
| `totalConsumption`, `totalGeneration`, `totalConsumptionFromCommunity`, `totalGenerationToCommunity`, `totalConsumptionInExcess`, `totalGenerationInExcess` | number or null | validation (sum check) |
| `consumptionFaulty`, `consumptionFromCommunityFaulty`, `consumptionInExcessFaulty`, `generationFaulty`, `generationToCommunityFaulty`, `generationInExcessFaulty` | bool | diagnostics |
| other `total*`, `*ByFactor*`, `*Complete`, `*Percent*`, `*PartFactor*`, `*EnergyMix*`, `totalEarnings*`, `totalSpendings*` | any | ignored, optional |

`ConsumptionPoint` (required): `dateTimeFrom` (ISO 8601 UTC string), `consumption`, `consumptionFromCommunity`, `consumptionInExcess`, `consumptionFaulty`, `consumptionFromCommunityFaulty`. Optional, parsed for diagnostics and tests: `consumptionComplete`, `consumptionFromCommunityComplete`, `consumptionInExcessComplete`, `consumptionByFactor`, `consumptionByFactorComplete`, `consumptionByFactorFaulty`, `dateTimeFromLocal`. Ignored: `date` (locale string, never parse), percent, part factor, energy mix, `spendings*`.

`GenerationPoint` (required): `dateTimeFrom`, `generation`, `generationToCommunity`, `generationInExcess`, `generationFaulty`, `generationToCommunityFaulty`. Optional as above with `generation*` names. Ignored: `date`, percent, part factor, `earnings*`.

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
    "locationId": "L-1001",
    "memberId": "1001",
    "address": {"street": "Example street", "houseNumber": "1", "postalCode": "9999", "city": "Example", "stairsFloorDoor": null, "createdAt": "…", "updatedAt": "…"},
    "substationName": {"id": 3001, "name": "Example substation", "ecNumber": "…", "substation": "…", "meteringPointRange": "…", "consumerCount": 1, "producerCount": 1, "prosumerCount": 1},
    "memberMeteringpoints": [
      {
        "id": 2001,
        "number": "AT0000000000000000000000000000001",
        "name": "Example consumption",
        "consumptionOrGeneration": "Consumption",
        "locationId": "L-1001",
        "networkOperator": "…", "ecNumber": "…", "commercialUse": false,
        "installationNumber": null, "nameAddition": null, "isManualPartFactor": false,
        "sourceOfProduction": null, "statedPeakFeedIn": null, "statedYearlyConsumption": 3000,
        "desiredPartFactorParticipatingCommunityIds": [4001],
        "activations": [
          {
            "energyCommunityId": 4001,
            "energyCommunityName": "Example BEG",
            "energyCommunityType": "CC",
            "status": "Active",
            "begin": "2026-01-01", "end": null, "desiredBegin": null,
            "activePartFactor": 100, "confirmedPartFactor": 100, "pendingPartFactor": 100,
            "isManualPartFactor": false, "anforderungMsgDate": "…"
          }
        ],
        "sendingZeroPlans": [],
        "receivingZeroPlans": [{"id": 5001, "status": "Active", "begin": "2026-01-01", "end": null, "isAutoGenerated": true, "fromMeteringpointNumber": "…", "fromName": "…", "toMeteringpointNumber": "…", "toName": "…"}]
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
    number_tail         <- last 4 characters of number (labels only)
    direction           <- consumptionOrGeneration: "Consumption" | "Generation" (anything else: SchemaError)
    activations[]:
      energyCommunityId <- energyCommunityId (int, used as energyCommunityIds)
      communityName     <- energyCommunityName
      communityType     <- energyCommunityType: "CC" (citizen energy community, BEG), "RC_R" (regional renewable energy community, EEG); other codes kept as they are
      activeFrom        <- begin (local date)
      activeTo          <- end (local date or null)
      status            <- status ("Active" seen)
```

Participation factors and Zero plans go into `extra` only (snapshot values, never configuration). `address`, `number`, `name`, `*Name` and `memberId` are sensitive and redacted (spec 06).

### `SessionInfo`

`GET /v1/portal/portal-session` → `{email: str, members: [{memberId: str, displayName: str, mustAcceptPlatformTerms: bool}]}`. `memberId` is the `{member}` path value. The model drops `email` and `displayName` at parse time and keeps `memberId` and `mustAcceptPlatformTerms` only. The JWT `sub` claim is **not** the member number; never derive it from the token.

### `Maintenance`

With no announced window the endpoint returns **200 with an empty body** (no content type), not `[]`; the client treats an empty body as an empty list. Entry field names `{id, start (UTC), end (UTC), title?}` are still **[verify]** (no window was announced at G1). Only start and end are used.

### `PortalVersion`

`{gitCommit: str, buildTime: str}` (confirmed; `gitCommit` is the full 40-character hash).

## Errors

Exceptions in `exceptions.py`, all subclasses of `EgAustriaError`:

| Condition | Exception | Effect (spec 03, 06) |
|---|---|---|
| 401 (including "Access token revoked"), or 403 with an auth-type body | `AuthError` | `ConfigEntryAuthFailed` → reauth |
| 403 other | `ForbiddenError` | treated as `AuthError` in phase 1 |
| 400 whose `detail`/`message` contains "aktualisiert" (stale client, 1140/29) | `ApiChangedError` | repair `api_changed`, no writes |
| 400 other, 404, 405, 422 | `ApiChangedError` (with status and redacted message) | same |
| 429 | `RateLimitedError` (with `Retry-After` if any) | stop run, retry next schedule, double the spacing for the next run |
| 500-504, timeout, connection error | `PortalUnavailableError` | retry next schedule; repair after 3 days |
| JSON decode error, wrong content type (HTML maintenance page) | `PortalUnavailableError` if a maintenance window is active, else `ApiChangedError` | as above |
| Model parse failure | `SchemaError` | repair `api_changed`, no writes |

Error bodies are RFC 7807-like with content type `application/problem+json`: `type` (`about:blank`), `title` (German, e.g. `Ungültige Anfrage`), `status`, `statusCode`, `error` (e.g. `Bad Request`), `detail`, `message`, `instance`, `traceId` (G1). `instance` is the full request path and query, so it holds the member number and the community and metering point ids; redact it before logging. The stale-client body (1140/29) could not be reproduced in G1 and stays as described.

## Not in phase 1

`billing/*`, `energy-flow`, `average-day`, `part-factor-changes`, `portal-session/token*` and every other `portal-session/*` path except `portal-session` itself, any document download.
