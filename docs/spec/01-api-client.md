# 01: API client

Module `api.py` (async, `aiohttp` via Home Assistant's shared client session factory) plus `models.py`. No Home Assistant imports in `models.py`, so it can be tested alone.

## Base and request policy

- Base URL: `https://portal.eg-austria.at/api` (constant, not configurable).
- Auth: header `Cookie: portal.jwtToken=<token>`. Use a dedicated `ClientSession` with `cookie_jar=DummyCookieJar()` so `Set-Cookie` responses (which clear the cookie) are ignored (ADR 0007).
- Headers: `Accept: application/json`, `User-Agent: ha-eg-austria/<version> (+<repo url>)`. No personal data in headers.
- Timeout: 60 s total per request. Requests strictly sequential; minimum 2 s between request starts.
- Retries inside one run: none for 4xx; one retry after 30 s for timeouts, connection errors and 502/503/504. Further retries are the scheduler's job.
- Whitelist (ADR 0002): the client exposes one method per endpoint; an internal `_get(path_template, params)` checks the template against a frozen set and the method is always `GET`. There is no generic request method.
- Logging: URL path with ids replaced by `{member}`, `{mp}`; never headers, never token; response bodies only at debug level and only through the redaction helper (spec 06).

## Array parameters

Arrays are sent as repeated keys: `energyCommunityIds=1&energyCommunityIds=2&mpIds=11&mpIds=12` (as used by members after 2026-10-22, 1140/29-30). **[verify]** after the update. Omitting both means "all communities, all metering points" (1140/24*); the client always sends them explicitly so results don't change when the member joins another community.

## Endpoints (phase 1)

| Method | Path | Params | Returns |
|---|---|---|---|
| `get_metering_points(member)` | `/v1/portal/member/{member}/metering-point` | none | `MeteringPointInfo` |
| `get_analysis(member, aggregation, start, end, community_ids, mp_ids)` | `/v1/portal/member/{member}/analysis-data` | `aggregation`, `startDate`, `endDate`, `energyCommunityIds`, `mpIds` | `AnalysisData` |
| `get_day_completeness(member, start, end, community_ids, mp_ids)` | `/v1/portal/member/{member}/analysis-data/day-completeness` | `startDate`, `endDate`, `energyCommunityIds`, `mpIds` | `list[DayCompleteness]` |
| `get_maintenance()` | `/v1/portal/maintenance` | none | `list[Maintenance]` |
| `get_version()` | `/v1/version` | none | `PortalVersion` |

- `aggregation`: enum `Month, Week, Day, Hour, FifteenMinutes`; phase 1 uses only `Hour` (and `Day` in tests).
- `startDate`, `endDate`: `YYYY-MM-DD`, Austrian local dates (the portal states its days are Austrian local days); end inclusive **[verify]** (analysis T-Q3h). The client enforces `end − start ≤ 30 days` for `Hour`.
- `{member}`: member number. How it is discovered is open (spec 02).

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

Notes:
- `*Complete` in `analysis-data` are **numbers** (value over complete data only), unlike the booleans of the same name in `day-completeness` (analysis Q3).
- Points arrive newest first; the parser sorts by `dateTimeFrom` ascending.
- An empty day returns empty lists and null totals (1140/20); that is valid, not an error.

### `DayCompleteness`

`{date: "YYYY-MM-DD", consumptionComplete: bool, generationComplete: bool}`; one entry per day in range **[verify]** whether days without data are listed or omitted (treat omitted as incomplete).

### `MeteringPointInfo` **[verify, shape to be fingerprinted after the update]**

Phase 1 needs only:

```
locations[]:
  id, name (display only)
  meteringPoints[]:
    id                 # internal id used as mpIds
    direction          # consumption | generation (map from portal codes)
    activations[]:
      energyCommunityId   # internal id used as energyCommunityIds
      communityName, communityType  # e.g. type code for BEG vs regional EEG
      activeFrom, activeTo?
```

The model maps whatever the portal returns into this structure in one adapter function, so a shape change touches one place. Participation factors and Zero plans are parsed into `extra` only (snapshot values, never configuration).

### `Maintenance`

`{id, start (UTC), end (UTC), title?}` **[verify]** field names. Only start and end are used.

### `PortalVersion`

`{gitCommit: str, buildTime: str}`.

## Errors

Exceptions in `exceptions.py`, all subclasses of `EgAustriaError`:

| Condition | Exception | Effect (spec 03, 06) |
|---|---|---|
| 401, or 403 with an auth-type body | `AuthError` | `ConfigEntryAuthFailed` → reauth |
| 403 other | `ForbiddenError` | treated as `AuthError` in phase 1 |
| 400 whose `detail`/`message` contains "aktualisiert" (stale client, 1140/29) | `ApiChangedError` | repair `api_changed`, no writes |
| 400 other, 404, 405, 422 | `ApiChangedError` (with status and redacted message) | same |
| 429 | `RateLimitedError` (with `Retry-After` if any) | stop run, retry next schedule, double the spacing for the next run |
| 500-504, timeout, connection error | `PortalUnavailableError` | retry next schedule; repair after 3 days |
| JSON decode error, wrong content type (HTML maintenance page) | `PortalUnavailableError` if a maintenance window is active, else `ApiChangedError` | as above |
| Model parse failure | `SchemaError` | repair `api_changed`, no writes |

Error bodies are RFC 7807-like (`type, title, status, detail, instance, traceId, message`); `instance` contains the member number and is redacted before logging.

## Not in phase 1

`billing/*`, `energy-flow`, `average-day`, `part-factor-changes`, `portal-session/*`, any document download.
