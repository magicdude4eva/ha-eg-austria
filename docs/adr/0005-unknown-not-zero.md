# 0005: Null or incomplete data is unknown, never zero

- Status: accepted
- Date: 2026-11-07

## Context

- `null` in the portal data means no value was received for at least one quarter-hour (1158/5*). A real outage arrives as `0` (1158/6).
- A community value of zero or null for a whole day can mean the grid operator failed to deliver (171/55*); after a deadline operators can only send `0` values (119/296*); already delivered data has been zeroed retroactively (topic 592).
- The portal's own CSV export shows the same pattern: on a day before a community activation, a member's export had consumption and generation values, community columns `0.000` and an empty share column. There was no community data that day, but the export shows a zero. "No community data" and "real zero" look the same in the values; only completeness tells them apart.
- Writing zero for unknown hours would make the Energy dashboard show a real-looking but wrong value and, worse, would not be corrected unless the hour is re-fetched.

## Decision

- A `null` value produces **no new row** for that statistic and hour. If a row for that hour is already stored, it is kept as it is (the import API can't delete rows, and removing it from the sums would break them).
- Community-dependent quantities (to/from community, residual import/export) are written for a day only when `day-completeness` reports that side complete. Meter quantities (consumption, generation) are written per hour when non-null.
- Cumulative sums skip unknown hours. When the hour later becomes known, the next run rewrites the window and the sums (ADR 0003).
- Diagnostics report the count and date range of unknown hours per statistic; a diagnostic sensor shows the date up to which data is complete.

## Consequences

- Recent periods show less than the truth until data arrives, never wrong extra values.
- A day that never becomes complete (operator failure) stays missing for community quantities. Its meter values are still written.
- If a completed day later turns all-zero where it had values before, ADR 0006 holds it back instead of writing it.
