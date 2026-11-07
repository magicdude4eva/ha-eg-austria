# 0011: Keep the live local meter as Energy dashboard source by default

- Status: accepted
- Date: 2026-11-07

## Context

- Many members already feed the Energy dashboard from a near-live local source (inverter, smart meter reader). Metered portal data is the billing truth but arrives days late, community shares weeks late.
- In one validated setup, a local inverter's monthly feed-in was within about 1 % of the metered value.
- Using both sources for the same flow double counts.

## Decision

- The integration never changes Energy dashboard preferences.
- Documentation recommends keeping the live local source for grid import/export and solar, and using `eg_austria` statistics for comparison cards, community shares and (phase 2) compensation.
- Members without a local source may choose the `eg_austria` statistics as grid sources; documented as an option with the lag explained.

## Consequences

- The Energy dashboard stays current; community information lives in statistics cards and, later, in compensation.
- Hourly kWh in the dashboard and the hourly compensation can come from different sources; totals are unaffected.
