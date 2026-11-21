# 03: Coordinator and fetch schedule

Files: `coordinator.py` (coordinator, scheduler, store), `importer.py` (window planning and the fetch → validate → write pipeline), `__init__.py` (setup, unload, services). One `EgAustriaCoordinator` per config entry. It is a `DataUpdateCoordinator` with `update_interval=None`; runs are triggered by a time listener, at startup, after a maintenance window and by a service. Its `data` holds the run summary (`RunSummary`) that the diagnostic entities read; a generic refresh never fetches.

## Run types

| Type | Trigger | Window |
|---|---|---|
| `initial` | After entry creation if `import_history`: the startup run, and every scheduled run until one succeeds (store `initial_done`) | Earliest activation `begin` (`activeFrom`) of the selected metering points in the selected communities → today |
| `daily` | `async_track_time_change` at `run_minute` (local time); and at startup if the last success is > 26 h old | See below |
| `long` | The first daily run (scheduled or catch-up) on or after the 3rd of each month, if no long or initial run succeeded this month | Today − `long_refetch_months` → today |
| `manual` | Service `eg_austria.refetch` with optional `start_date`, `end_date` (at most 400 days, end not in the future) | As given; no `start_date`: `end − daily_window_days`; no `end_date`: today |

Every window starts no earlier than the earliest activation (no requests for days before the member joined). A successful initial run also counts as this month's long run.

Only one run per entry at a time (an `asyncio.Lock`); a scheduled trigger during a run is dropped and logged at debug; the `refetch` service refuses with `run_in_progress` instead. Runs are background tasks (`entry.async_create_background_task`) and never block startup.

### Startup

After Home Assistant has started (`async_at_started`), at most one run, 60-300 s later (random): the initial import if pending, else a catch-up run if the last success is older than 26 h (it becomes a long run when one is due). No startup run if any run was attempted in the last hour, so a restart loop can't become a request storm. Setup itself never fetches.

## Daily window

```
completeness = get_day_completeness(today − 13 months + 1 day, today, all community ids, mp_ids)
first_incomplete = earliest date d in [today − 120 d, today] where
                   not (consumptionComplete and generationComplete) or d missing
                   (null flags count as false; future days are listed with null)
start = min(today − daily_window_days, first_incomplete or today)
end   = today
```

The completeness range is extended to the window start when `daily_window_days` reaches further back. 13 months back plus one day keeps it at one request (the client chunks longer ranges). The initial, long and manual runs call `day-completeness` for their window (chunked to 13 months if needed) because statistics need per-day flags (spec 04).

## Run sequence

1. `get_maintenance()`. If now ∈ [start − 30 min, end + 30 min] for any window: reschedule this run for end + 30 min (one-shot listener), finish with status `skipped_maintenance`.
2. `get_version()`. Record `gitCommit`; if it differs from the stored one, flag `portal_version_changed` in the run summary and log at info.
3. `get_metering_points(member)`: directions of the selected metering points (which statistics exist, spec 04), the earliest activation (window floor, initial window), the current community names (written back to `communities[].name`, spec 02) and whether the entry selects every community the points are active in (`all_communities` for P4, spec 05). None of the selected metering points found → `ApiChangedError` (the ids changed, as on 2026-10-22); some missing → warning, the rest is fetched.
4. Day completeness (above), checked with `validate_completeness()` (S9).
5. Split [start, end] into calendar-month chunks (each ≤ 31 days).
6. For each chunk, request `analysis-data` (Hour), one request per selected community, all selected metering points. There is no all-communities request: G1 (T-Q3c) showed that meter values and residuals (`*InExcess`) are the same in every single-community response, and that site community values are the sum of the per-community values (spec 01 "Request scope"). If the chunk contains an autumn DST day, also request `Day` for that one day, once per community (spec 04 "Autumn DST gap").
7. Parse and validate every chunk (spec 05, `validate_chunk()` with `site`, `all_communities` and the entry's `max_hourly_kwh`; `Day` responses with `aggregation=Day`). On a shape failure: stop, no writes for the whole run.
8. History guard: read the stored hourly states of the window from the recorder, sum them per Vienna local day and run `check_history()` per chunk, passing the days released by `release_held`.
9. Every (statistic, day) where `ValidationResult.is_blocked()` is true goes to S4 as `held_days`, so stored rows stay for those hours; build hourly series and write statistics (spec 04, `async_import_window()`) per statistic in one import call covering start → newest hour.
10. Store the run summary and the bookkeeping below; publish the summary to listeners (S6 turns it into repairs).

The entry unloading is checked before every request; a run stops there without writing.

Request count per run: 4 + N communities × chunks (+ N for each autumn DST day in the window). With 2 communities and a 62-day window (3 chunks): 10.

## Persistent state

`helpers.storage.Store` per entry (`eg_austria.<entry_id>`), version 1:

```
last_success: iso datetime            # end of the last ok/partial run
last_attempt: iso datetime            # start of the last run of any outcome
last_long_success: iso datetime       # long or initial run
portal_git_commit: str
consecutive_failure_days: int
last_failure_day: date                # counts a failure day once
held_days: {statistic_key: [dates]}   # days held back by H1 (spec 05)
released: {statistic_key: [dates]}    # release_held, until a run covers them
complete_until: date                  # last date of the contiguous complete run
initial_done: bool
rate_limited: bool                    # the last run got 429
```

No energy values are cached; statistics are the store of record. `held_days` inside a run's window are replaced by that run's H1 result; days outside it are kept. `complete_until` walks the completeness list of the run from its first day; if the stored value ends before that day, it is kept (the gap was not re-checked). The store is deleted with the entry (`async_remove_entry`).

## Error handling per run

| Error | Run result | Follow-up |
|---|---|---|
| `AuthError` | abort, status `auth_failed` | `ConfigEntryAuthFailed` → `entry.async_start_reauth()` |
| `ApiChangedError`, `SchemaError`, shape rule | abort, no writes, status `api_changed` | repair `api_changed` |
| `RateLimitedError` | abort, status `rate_limited` | next run (any type) with doubled request spacing, then back to normal |
| `PortalUnavailableError` | abort, status `unavailable` | `consecutive_failure_days += 1` once per calendar day; at 3, repair `portal_unreachable` |
| Plausibility or history failure | partial, status `partial` | repair `implausible_data`, affected statistic and chunk (or day) held |
| Maintenance | skipped, status `skipped_maintenance` | one-shot run at end + 30 min |

After `exp` has passed, no request is made: setup raises `ConfigEntryAuthFailed`, and a run that finds the expiry passed starts reauth without a request. Error texts in the summary are redacted (`redact_text()` with member, location, metering point and community ids).

## Unload

Cancel listeners, let a running import finish its current request (it stops before the next one, without writing; max 60 s, then the task is cancelled), save the store, then close the session.

## Hooks for S6

- `coordinator.data`: `RunSummary` of the last finished run: `run_type`, `status` (the `last_run_status` values of spec 06), `started`, `finished`, `duration`, `window`, `chunks`, `requests`, `portal_git_commit`, `portal_version_changed`, `maintenance`, `validation` (`ValidationResult`: rule ids, statistic keys, dates, `schema_extra`, `corrections`), `written_rows` and `kept_stored_hours` per statistic key, `blocked` (held days per key), redacted `error`, `stale_client` (S6: the `api_changed` came from a `StaleClientError`), `rescheduled_for`.
- S6 additions for diagnostics: `coordinator.portal_version` (last `PortalVersion`), `coordinator.site` (last `SiteInfo`: directions, missing metering points), `client.schema_fingerprints`.
- `RunSummary.issues` (`IssueHints`): per repair `api_changed`, `implausible_data`, `portal_unreachable`: True = create, False = delete, None = this run says nothing (for example an unreachable portal says nothing about `api_changed`).
- `coordinator.state`: `StoreData` (above; `as_dict()` for diagnostics). Sensors: `last_import` = `state.last_success`, `data_complete_until` = `state.complete_until`, `portal_version` = `state.portal_git_commit`.
- `coordinator.async_add_listener()`: called after every run and after `release_held` (S6 re-evaluates `implausible_data` then).
- Reauth is started by the coordinator; `token_expiring` (spec 06) needs only `entry.data["token_exp"]`.
- `entry.runtime_data` is the coordinator.

## Services

Registered in `async_setup` (`services.yaml`, translations in `strings.json`):

- `eg_austria.refetch` (`config_entry_id`, `start_date?`, `end_date?`): manual run in the background. Refused with a translated `ServiceValidationError` for an unknown or unloaded entry, `start > end`, an end in the future or more than 400 days; `HomeAssistantError` `run_in_progress` while a run is active.
- `eg_austria.release_held` (`config_entry_id`, `statistic_key?`, `date?`): moves matching `held_days` to `released`; the next run covering those days doesn't hold them again (`check_history(released=...)`) and then drops them from `released`. No run is started.

## Statistic display names

The site label ("Home") and the quantity labels are translation strings in `strings.json` under `selector.statistic_name.options` (`site`, `site_<quantity>`, `community_<quantity>`). The integration has no other translation category hassfest accepts for free-form labels. They are read in the configured language at setup and passed to `build_statistic_defs()`. A language change renames the statistics at the next import; ids never change.
