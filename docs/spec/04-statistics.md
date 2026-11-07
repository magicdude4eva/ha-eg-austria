# 04: Statistics

File: `statistics.py`. Writes external statistics through `homeassistant.components.recorder.statistics.async_add_external_statistics`. All recorder reads run in the recorder's executor (`get_instance(hass).async_add_executor_job`).

## Ids and metadata

`statistic_id = "eg_austria:" + key`, where `key` is

- site: `<site>_<quantity>`
- community: `<site>_<community>_<quantity>`

with slugs generated once at setup and stored in the config entry (spec 02, ADR 0012). Example with synthetic slugs: `eg_austria:home_generation_to_community`, `eg_austria:home_beg_consumption_from_community`.

### Display names

- Site statistics: `Home <quantity label>`, for example "Home Generation to communities". "Home" and the quantity labels are translation strings, not portal data.
- Community statistics: `<portal community name> <quantity label>`, for example "Example BEG Consumption from community". The community name is read from `metering-point` at runtime; nothing is hard-coded.
- Each run compares the portal name with the stored `name`. On a change it updates the config entry and passes the new `name` in the metadata of the next import (the recorder updates metadata for an existing id). The statistic id never changes.

Metadata for every phase 1 statistic:

| Field | Value |
|---|---|
| `source` | `eg_austria` |
| `statistic_id` | as above |
| `name` | See "Display names" above |
| `unit_of_measurement` | `kWh` |
| `unit_class` | `energy` **[verify]** against the Home Assistant version at implementation time; omit if the API version in use doesn't have it |
| `has_sum` | `true` |
| `mean_type` | `StatisticMeanType.NONE` (`has_mean` false on older versions) |

## Mapping

Request scope: `site` = all selected communities; `c` = only community c (spec 03).

| Quantity key | Label | Scope | Point list | Field | Completeness side |
|---|---|---|---|---|---|
| `consumption` | Consumption | site | consumption | `consumption` | none (meter value) |
| `generation` | Generation | site | generation | `generation` | none (meter value) |
| `consumption_from_community` | Consumption from communities | site | consumption | `consumptionFromCommunity` | consumption |
| `generation_to_community` | Generation to communities | site | generation | `generationToCommunity` | generation |
| `consumption_from_supplier` | Consumption from supplier | site | consumption | `consumptionInExcess` | consumption |
| `generation_to_surplus` | Generation to surplus buyer | site | generation | `generationInExcess` | generation |
| `<c>_consumption_from_community` | Consumption from community | c | consumption | `consumptionFromCommunity` | consumption |
| `<c>_generation_to_community` | Generation to community | c | generation | `generationToCommunity` | generation |

Statistics for a direction with no selected metering point of that direction are not created.

Not mapped in phase 1: `*Complete`, `*ByFactor`, percent, part factor, energy mix, `earnings`, `spendings`.

## Hourly series

For each statistic and each hour `h` (UTC start, from `dateTimeFrom`) in the window:

1. `v = field value` of the point at `h` (no point → unknown).
2. `v is None` → unknown.
3. If the statistic has a completeness side and `day-completeness` for the local date of `h` says that side is incomplete or the day is missing → unknown.
4. If validation held this statistic's day back (spec 05) → unknown.
5. Otherwise `state = v`.

Unknown hours produce no new row (ADR 0005); an hour that already has a stored row keeps it (see "Stored rows win over unknown" below).

## Sum computation and write

```
base = last row of statistic_id with start < window_start   # sum, or 0 if none
rows = []
running = base
for h in hours(window_start, newest_hour):
    v = state(h) if known(h) else stored_state.get(h)   # stored rows win over unknown
    if v is not None:
        running += v
        rows.append(StatisticData(start=h, state=v, sum=running))
async_add_external_statistics(hass, metadata, rows)
```

- `base` and `stored_state` lookup: one `statistics_during_period(hass, window_start − 400 days, window_end, {statistic_id}, "hour", None, {"state", "sum"})`; `base` is the sum of the last row before `window_start` (0 if none), `stored_state` maps the window's hours to stored states. A helper wraps this so a later version can use a cheaper query.
- `newest_hour` = the latest hour with any known value for that statistic in this run.
- **Stored rows win over unknown.** The import API can't delete rows. If an hour in the window has a stored row and is unknown in this run (a value went back to `null`, or a day flipped back to incomplete), the run keeps the stored `state` for that hour and includes it in the new sums. Otherwise the stale row's `sum` would sit between rewritten rows and break hourly differences. Such hours are counted in diagnostics (`kept_stored_hours`). To read them, the run loads stored `state` rows for the window with the same `statistics_during_period` call (types `{"state", "sum"}`).
- Rows before `window_start` are never touched.
- One import call per statistic per run. The recorder queues the import; the run doesn't wait for commit.

## Statistic removal

Removing the config entry does not delete statistics (users keep history). The README explains how to delete them from Developer tools → Statistics.
