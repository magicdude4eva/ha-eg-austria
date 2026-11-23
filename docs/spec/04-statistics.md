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

Scope: `site` = the whole location; `c` = only community c. Every value comes from the per-community responses (spec 03): `site` meter and residual fields (`consumption`, `generation`, `*InExcess`) from the first community's response (they are identical in all of them, rule P6), `site` community fields as the sum over the selected communities, `c` fields from community c's response. Only communities active on the day count (issue #16, spec 03 step 6): meter and residual come from the first active community in entry order (for the meter value: the first active one whose hour isn't flagged faulty, issue #78), the sum covers the active ones, and `c` has no values before its activation `begin` or after its `end`.

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

Not mapped: `*Complete`, `*ByFactor`, percent, part factor, energy mix.

### Money (phase 2, ADR 0008, 0015, issue #72)

| Quantity key | Label | Scope | Point list | Field / source | Completeness side | Held with |
|---|---|---|---|---|---|---|
| `feed_in_eur` | Feed-in income from communities | site (sum) | generation | `earnings` | generation | `generation_to_community` |
| `consumption_eur` | Cost of consumption from communities | site (sum) | consumption | `spendings` | consumption | `consumption_from_community` |
| `<c>_feed_in_eur` | Feed-in income | c | generation | `earnings` | generation | `<c>_generation_to_community` |
| `<c>_consumption_eur` | Cost of consumption | c | consumption | `spendings` | consumption | `<c>_consumption_from_community` |
| `surplus_eur` | Surplus income | site | — | `generation_to_surplus` × surplus price | (from its kWh) | `generation_to_surplus` |
| `feed_in_total_eur` | Feed-in compensation | site | — | `feed_in_eur` + `surplus_eur` | (from both) | both kWh keys |
| `import_savings_eur` | Import savings | site | — | `consumption_from_community` × supplier price − `consumption_eur` | (from both) | `consumption_from_community` |
| `<c>_import_savings_eur` | Import savings | c | — | `<c>_consumption_from_community` × supplier price − `<c>_consumption_eur` | (from both) | `<c>_consumption_from_community` |

- Metadata: unit `€` (`CURRENCY_EURO`), `unit_class` none, `has_sum` true, no mean. Ids and names follow the kWh rules above.
- `earnings`/`spendings` are the portal's standard-rate amounts (ADR 0008); they follow the hourly rules 1-6 like community kWh (gated by the side's completeness, DST residual from `Day`). Site values are the sum over the active communities, unknown if any is unknown.
- Held days: a money statistic is not written on a day its kWh statistic(s) are held (`StatisticDef.gate_keys`).
- `surplus_eur` and `feed_in_total_eur` exist only when a surplus price is set (option `surplus_price`, spec 02). Per hour: `surplus_eur = round(kWh × price, 6)` where both are known; `feed_in_total_eur = round(feed_in_eur + surplus_eur, 6)` where both are known. `import_savings_eur` (site and per community, issue #73) exists only when a supplier price is set (option `supplier_price`): per hour `round(kWh × price − cost, 6)` where kWh, price and cost are known; the cost is the settled / Zero-corrected `consumption_eur` (#74, #89), so savings follow the invoices. Unpriced hours raise `supplier_price_missing` like `price_missing`. Price sources (`prices.py`, for both prices): `number` (dated periods in ct/kWh, per Austrian local date), `sensor` (hourly `mean` from long-term statistics, unit `EUR/kWh` or `ct/kWh` from the entity), `table` (entity attribute `{"YYYY-MM": price}`, unit from the option).
- Hours with surplus kWh but no price are listed per run (`WriteResult.unpriced`). The first overdue local date raises `price_missing` (spec 06); for a `table` source the current and the previous month are not overdue yet. Hours before the first hour the source has a price for in the window are not reported (`first_unpriced(covered_from=…)`); a source with no price at all in the window still raises the repair.
- **Settlement (issue #74, `settlement.py`):** before writing, the hourly amounts of every settled community, direction and month in the window are multiplied by `settled / provisional month total` (`month_factors()`, `apply_settlements()`), so a settled month's community statistic equals its invoice; site sums and `feed_in_total_eur` follow from the scaled community values, kWh are never touched. Feed-in rows count as they are, consumption rows (`Strombezug`, charged = negative) as a positive cost. `Energierechnungskorrektur` rows take the direction of the earlier row of the same community and month whose amount they negate exactly; none (or both directions) → that community and month stay unsettled (`unmatched_correction`). A provisional total of 0 with a non-zero settlement stays provisional (`zero_provisional`); 0 and 0 is settled with factor 1. Rows without a community, of unselected communities and with other descriptions are ignored (counted). The state and factor per community, direction and month go to the run summary (`RunSummary.settlement`, diagnostics); no amounts.
- **Zero (issue #89, `zero.py`), before the settlement step:** for a community on a day with an own-site Zero plan (`SiteInfo.zero_ranges`: a plan in `metering-point` whose sending and receiving metering points are both selected), `Z_d = min(Σ_day generation to community, Σ_day consumption from community)`; a day with an unknown community hour has no estimate. Each hour's Zero kWh are its community kWh × `r_d = Z_d / Σ_day community kWh` of that direction. **Derived rates:** for a settled month with `Σ Z_d ≥ 1 kWh`, `zero_rate = (settled − Σ money × (1 − r_d)) / Σ Z_d` per community and direction, computed from the portal amounts before any repricing; a negative result is dropped. The latest month's rate per community and direction is kept in the store (`StoreData.zero_rates`). **Repricing:** `money' = money × (1 − r_d) + kWh × r_d × zero_rate`, with the month's own derived rate for a settled month (its settlement factor is then about 1) and the latest stored rate otherwise; without a rate, Zero kWh stay at the standard rate. kWh are never touched. Other setups (Zero to another site, no plan) get no estimate; their settlement corrects them per month.

## Hourly series

For each statistic and each hour `h` (UTC start, from `dateTimeFrom`) in the window:

1. `v = field value` of the point at `h` (no point → unknown, except the autumn DST gap below).
2. `v is None` → unknown.
3. If the statistic has a completeness side and `day-completeness` for the local date of `h` says that side is incomplete, `null` or the day is missing → unknown.
4. If the statistic has no completeness side (meter values) and that side of the day is not complete: the point's meter flag (`consumptionFaulty` or `generationFaulty`) true → unknown. The portal sends missing meter data as `0` with that flag set, not as `null` (G1). On complete days the flag is ignored: there it was only seen on the hour before the autumn DST gap, with a correct value.
5. If validation held this statistic's day back (spec 05) → unknown.
6. Otherwise `state = v`.

Details fixed in S4:

- Local dates (window, `day-completeness`, `Day`) are Austrian local time (`Europe/Vienna`), independent of the Home Assistant time zone.
- `site` community values are the sum over **all** selected communities; if any community has no point or a `null` value for the hour, the site value is unknown (never a partial sum).
- Rule 5 uses `held_days` keyed by statistic key (`<site>_<quantity>`, as in spec 03).

### Autumn DST gap

On the day clocks go back, `Hour` has no point for the first local 02:00 hour (00:00 UTC), while the `Day` value includes it (spec 01). Once that day is complete for the statistic's side, the importer requests `Day` for that one date (per community) and sets the 00:00 UTC hour to `Day − sum of the day's 24 hourly values` for each mapped field, if that residual is ≥ 0 (else unknown, logged once). Before the day is complete the hour stays unknown. The residual uses the raw hourly values (the meter flag is ignored, as on any complete day), is rounded to 6 decimals, and is unknown if any of the 24 hours or the `Day` value is `null` or missing. A residual of exactly 0 is a value. The gap hour is found from the time zone (the first of the two local 02:00 hours), not hard-coded. Decided 2026-11-21 (G1 review) over leaving the hour unknown, so the day's sum matches the portal's `Day` value.

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

- `base` and `stored_state` lookup: one `statistics_during_period(hass, window_start − 400 days, None, {all statistic ids of the entry}, "hour", None, {"state", "sum"})` per run in the recorder executor; `base` is the sum of the last row before `window_start`, `stored_state` maps the hours from `window_start` on to stored states. A statistic with no row in the 400-day lookback (first import, or a longer gap) gets a second query over all of its history before `window_start`, so a long gap can't reset the sum to 0; only with no earlier row at all is `base` 0. A helper (`async_load_stored`) wraps this so a later version can use a cheaper query.
- `newest_hour` = the latest hour from `window_start` on that is known in this run **or already stored**. Including stored rows means a run whose newest hours went back to unknown still rewrites (and keeps) them, so no stored row after the rewritten range can keep a stale sum (test plan case 21 holds by construction, no assert needed).
- **Stored rows win over unknown.** The import API can't delete rows. If an hour in the window has a stored row and is unknown in this run (a value went back to `null`, or a day flipped back to incomplete), the run keeps the stored `state` for that hour and includes it in the new sums. Otherwise the stale row's `sum` would sit between rewritten rows and break hourly differences. Such hours are counted in diagnostics (`kept_stored_hours`). To read them, the run loads stored `state` rows for the window with the same `statistics_during_period` call (types `{"state", "sum"}`).
- Rows before `window_start` are never touched.
- One import call per statistic per run; none when the statistic has neither a known nor a stored hour from `window_start` on (no empty statistic is created, and its metadata name is refreshed on the next run with data). The recorder queues the import; the run doesn't wait for commit.

## Interface (for S5)

```python
defs = build_statistic_defs(site_slug, [CommunityRef(slug, name), ...], directions,
                            site_label=..., labels={"site.<quantity>": ..., "community.<quantity>": ...})
results = await async_import_window(hass, defs, hourly, completeness, first_day, last_day,
                                    day_values=day_values, held_days=held_days)
```

- `communities` in the entry's order; `directions` = directions of the selected metering points.
- `hourly`: community slug → that community's `Hour` responses (one per chunk); `day_values`: community slug → `Day` responses for autumn DST days; `completeness`: the run's `day-completeness` list; `first_day`/`last_day`: the window's local dates, both inclusive.
- `held_days`: statistic key → local dates that must not be written. Build it from `ValidationResult.is_blocked(key, day)` for every key and window day, not only from `held_by_statistic()`, so blocking findings (per day or per chunk) are covered too; keys match spec 05's (`<site>_<quantity>`, `<site>_<community>_<quantity>`).
- Display labels default to English in code; S5 passes the translated "Home" and quantity labels (translation strings live with S5, `strings.json` is not touched by S4).
- Result per statistic key: rows written, `kept_stored_hours`, unknown hour count with first/last hour, `newest_hour`, filled DST hours and dates with a negative residual, for the run summary and diagnostics.
- `build_series()` (pure) and `async_write_statistics()` are public too, for tests and for a later dry run.

## Statistic removal

Removing the config entry does not delete statistics (users keep history). The README explains how to delete them from Developer tools → Statistics.
