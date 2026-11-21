# 03: Coordinator and fetch schedule

Files: `coordinator.py`, `importer.py`. One `EgAustriaCoordinator` per config entry. It is a `DataUpdateCoordinator` with `update_interval=None`; runs are triggered by a time listener, at startup and by a service. Its `data` holds the run summary that the diagnostic entities read.

## Run types

| Type | Trigger | Window |
|---|---|---|
| `initial` | After entry creation if `import_history` | Earliest activation `begin` (`activeFrom`) of selected metering points → today |
| `daily` | `async_track_time_change` at `run_minute`; and at startup if the last success is > 26 h old | See below |
| `long` | The first daily run on or after the 3rd of each month, if no long run succeeded this month | Today − `long_refetch_months` → today |
| `manual` | Service `eg_austria.refetch` with optional `start_date`, `end_date` (max 400 days) | As given |

Only one run per entry at a time (an `asyncio.Lock`); a trigger during a run is dropped and logged at debug. Runs are background tasks and never block startup.

## Daily window

```
completeness = get_day_completeness(today − 13 months, today, all community ids, mp_ids)
first_incomplete = earliest date d in [today − 120 d, today] where
                   not (consumptionComplete and generationComplete) or d missing
                   (null flags count as false; future days are listed with null)
start = min(today − daily_window_days, first_incomplete or today)
end   = today
```

The initial and long runs also call `day-completeness` for their window (chunked to 13 months if needed) because statistics need per-day flags (spec 04).

## Run sequence

1. `get_maintenance()`. If now ∈ [start − 30 min, end + 30 min] for any window: reschedule this run for end + 30 min (one-shot listener), finish with status `skipped_maintenance`.
2. `get_version()`. Record `gitCommit`; if it differs from the stored one, flag `portal_version_changed` in the run summary.
3. Day completeness (above).
4. Split [start, end] into chunks of ≤ 31 days aligned to calendar months where possible.
5. For each chunk, request `analysis-data` (Hour), one request per selected community, all selected metering points. There is no all-communities request: G1 (T-Q3c) showed that meter values and residuals (`*InExcess`) are the same in every single-community response, and that site community values are the sum of the per-community values (spec 01 "Request scope"). If the chunk contains an autumn DST day, also request `Day` for that one day, once per community (spec 04 "Autumn DST gap").
6. Parse and validate every chunk (spec 05). On a shape failure: stop, no writes for the whole run.
7. Build hourly series per statistic (spec 04), apply completeness rules.
8. Write statistics (spec 04) per statistic in one import call covering start → newest hour.
9. Store the run summary; clear repairs that this run resolved.

Request count per daily run: N communities × chunks + 3 (+ N once a year for the DST day). With 2 communities and a 62-day window: about 9.

## Persistent state

`helpers.storage.Store` per entry (`eg_austria.<entry_id>`), version 1:

```
last_success: iso datetime
last_long_success: iso datetime
portal_git_commit: str
consecutive_failure_days: int
held_days: {statistic_key: [dates]}   # days held back by validation (spec 05)
complete_until: date                  # last date of the contiguous complete run
```

No energy values are cached; statistics are the store of record.

## Error handling per run

| Error | Run result | Follow-up |
|---|---|---|
| `AuthError` | abort | raise `ConfigEntryAuthFailed` (reauth) |
| `ApiChangedError`, `SchemaError` | abort, no writes | repair `api_changed` |
| `RateLimitedError` | abort | next daily run; spacing doubled for that run |
| `PortalUnavailableError` | abort | `consecutive_failure_days += 1` once per calendar day; at 3, repair `portal_unreachable` |
| Plausibility failure | partial | repair `implausible_data`, affected statistic and chunk skipped |

After `exp` has passed, no request is made; the coordinator raises `ConfigEntryAuthFailed` directly.

## Unload

Cancel listeners, wait for a running import to finish its current request (max 60 s), then close the session.
