# 0004: Fetch window and schedule

- Status: accepted
- Date: 2026-11-07
- Depends on: `day-completeness` and `analysis-data` parameters before the 2026-11-09 update; request size limits measured once

## Context

- The latest days are empty or partial; the community split arrives later than meter values (1175/33*). The portal's `day-completeness` endpoint reports per day whether consumption and generation data are complete.
- Grid operators have deadlines (15 days to the VEZ, 119/296*; a 2-month deadline was also reported, 1104/4), but corrections arrive months later.
- Settlement takes about two months (119/123*).
- Measured once: Hour aggregation for 31 days ≈ 1 MB in 6 s; FifteenMinutes for 31 days timed out at 60 s. Hourly data is what statistics need.
- Members have run daily fetches without hitting rate limits, but there is no documented limit.
- The portal announces maintenance through an API endpoint; data and billing can be restricted during system changes.

## Decision

- **Aggregation:** `Hour` only. Requests in chunks of at most 31 days, sequential, at least 2 s apart, 60 s timeout.
- **Daily run** at a fixed time per installation (random minute between 03:00 and 06:00 local, chosen at setup), plus a catch-up run at startup if the last successful run is more than 26 h old.
- **Daily window:** from `min(today − 62 days, earliest incomplete day)` to today, where the earliest incomplete day is looked for in the last 120 days only, so one day that never completes can't make every daily run long. The completeness call covers 13 months (for diagnostics); the analysis-data calls cover the window. Older incomplete days are picked up by the long re-fetch.
- **Long re-fetch:** once a month (first daily run after the 3rd), the window is the last 13 months, regardless of completeness.
- **Initial import:** from the earliest metering-point activation date (from `metering-point`) to today, chunked, in the background after setup.
- **Maintenance-aware:** before a run, read `/v1/portal/maintenance`. If now is within an announced window (plus 30 min margin either side), skip and schedule the run 30 min after its end.
- **Version-aware:** read `/v1/version` each run; a changed build is recorded in diagnostics and logged at info level. It doesn't stop the run; validation decides (ADR 0006).

## Consequences

- About 4-10 requests per day in normal operation, about 15-30 on the monthly long run, more once at setup.
- Data for a day appears one to several days late; community shares up to weeks late. That is inherent to the source.
- Chunk size and margins are constants that can be tuned without a design change.
