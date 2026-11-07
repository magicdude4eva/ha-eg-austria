# 0003: External statistics, overwritten on re-fetch

- Status: accepted
- Date: 2026-11-07

## Context

Data per hour arrives late and changes later (grid operator corrections months afterwards: 171/55*, 152/160*, 1373/2*). Home Assistant has two kinds of long-term statistics: entity statistics, compiled by the recorder from sensor states, and external statistics (`source:statistic_id`), imported by an integration for any past hour. Entity-bound imported statistics conflict with the recorder's own compilation for the same entity.

## Decision

- Write **external statistics** with source `eg_austria` via the recorder's import API (`async_add_external_statistics`), hourly rows with `state` (the hour's kWh) and `sum` (cumulative kWh). Metadata: unit kWh, energy unit class, `has_sum` true, no mean.
- Each run rewrites every hour in its window. Re-imported rows with the same start replace the old ones.
- Because `sum` is cumulative, a run computes sums from the last stored sum before the window start and rewrites all rows from window start to the newest hour. The window always runs to the newest available hour, so later rows never keep stale sums.
- No sensor entities for the energy values. Diagnostic entities only (spec 06).

## Consequences

- Late and corrected data lands in the right hour; daily, monthly and yearly views are right once data is final.
- Statistics are visible in statistics cards and selectable as Energy dashboard sources, but not as entity states, automations or history graphs.
- A window rewrite of 13 months is about 9,500 rows per statistic; acceptable once a month (ADR 0004).
- Statistic ids must stay stable for ever (ADR 0012); renaming orphans history.
