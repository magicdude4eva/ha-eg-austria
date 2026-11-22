# 0009: Tariffs as dated configuration

- Status: accepted (phase 2, decisions in #55 on 2026-11-22)
- Date: 2026-11-07, updated 2026-11-22 (effective rates instead of a tax model)

## Context

- EG Austria community tariffs are fixed per calendar year and change on 1 January (990/138*). Members have different tariffs (Fix, Zero, others; different communities; producers, consumers, full feed-in).
- BEGs charge VAT on fees (and on consumption energy); regional EEGs under the small-business rule charge a levy ("MAT") instead, which may be partly refunded the following year (1231/4*, 562/12*). Modelling these rules per community is error-prone, and the invoice already shows the resulting price per kWh.
- Grid-fee reductions for community energy appear on the grid operator's bill, not EG Austria's. For a regional EEG they are a reduced grid energy price and the exemption from some levies per community kWh; a BEG gets none. Since 2026 the grid operators also have a cheaper summer midday grid price ("Sommer-Nieder-Arbeitspreis", 1 April to 30 September, 10:00 to 16:00) for kWh that are *not* EEG kWh, so in those hours the EEG reduction is worth less. Most community kWh fall in exactly these hours.
- Hard-coded rates would be wrong for most users and for every new year.
- Next year's rates are often decided late in the year, so a member may not know them on 1 January.

## Decision

- Tariffs are options, entered per community as a list of periods with **effective rates in ct/kWh, including fees and taxes** (what one kWh earns or costs on the invoice): `{valid_from, feed_in, consumption, zero_feed_in, zero_consumption, grid_saving, grid_saving_summer_midday}`. The Zero rates are only used for a community with an own-site Zero (ADR 0008); they default to empty, not 0. `grid_saving` (optional) is the grid-fee reduction per community kWh shown on the grid operator's bill (for example the reduced grid fee for a regional EEG); it is only used for import savings (ADR 0015), and empty means not counted. `grid_saving_summer_midday` (optional) replaces it for hours from 1 April to 30 September, 10:00 to 16:00 Austrian local time; empty means `grid_saving` applies all year. The window is fixed in code and named in the documentation; if the regulation changes, it is a code change.
- A period stays valid until the next one starts. The options flow adds, edits and removes periods; the documentation shows how to work out effective rates from an invoice, with **synthetic numbers only**.
- **No defaults are shipped.** Money statistics for a community start only once a period covers its hours.
- Hours not covered by a period get no money row (unknown, ADR 0005), and a repair `tariff_missing` names the community and the first uncovered date.
- On 1 January (and at startup in January), if a community's latest period started before the new year, a dismissible repair `tariff_review` asks the user to check the new year's rates. Until then the old rates go on; settlement (ADR 0008) corrects the months.
- Membership fees, payouts and the levy refund are not energy and are not part of the money statistics.

## Consequences

- Users enter a few numbers per community once a year, copied from an invoice.
- Settled money corrects tariff mistakes per month, but provisional values are only as good as the config.
- Members with tariffs this model can't express (for example several prices per direction) get approximate provisional values, still corrected at settlement.
