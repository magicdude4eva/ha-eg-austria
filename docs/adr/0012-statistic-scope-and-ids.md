# 0012: Statistic scope and stable ids

- Status: accepted
- Date: 2026-11-07
- Depends on: whether per-community requests add up to the combined request (offline test planned)

## Context

`analysis-data` filters by community ids and metering point ids and returns separate consumption and generation point lists, summed over the selected metering points. A member can be in several communities. Statistic ids are permanent; changing them orphans history. Portal ids (member, community, metering point) are personal and have changed form before (2026-10-22).

## Decision

- **One config entry = one member and one site** (the selected metering points of one location).
- Site statistics: `consumption`, `generation`, `consumption_from_community`, `generation_to_community` (all communities), `consumption_from_supplier`, `generation_to_surplus`.
- Per-community statistics: `consumption_from_community`, `generation_to_community`.
- **Ids are slugs, generated once at setup and stored.** Format `eg_austria:<site>_<quantity>` and `eg_austria:<site>_<community>_<quantity>`. Defaults: `home` for the site, the community type for communities (`beg`, `eeg`, then `eeg2`, ...); the user may edit them in the config flow, then they are frozen. No portal ids and no portal names in statistic ids.
- **Display names follow the portal and are never hard-coded.** The site total is shown as "Home"; communities use the name the portal returns in `metering-point`, read at runtime and refreshed on each run. When the portal renames a community, the statistic's display name is updated in its metadata; the id and history stay.
- The mapping from slugs to portal ids lives in the config entry and can be updated if portal ids change, without touching statistics.

## Consequences

- Statistic ids are readable, stable and contain nothing personal; a portal rename doesn't break history.
- Per-metering-point statistics are deferred to phase 3 and will use new ids; phase 1 ids stay.
- If the additivity test fails, the combined request stays the source for site totals.
