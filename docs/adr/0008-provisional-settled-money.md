# 0008: Provisional vs settled money, Zero estimated per day

- Status: accepted (phase 2, decisions in #55 on 2026-11-22)
- Date: 2026-11-07, updated 2026-11-22 (community rates from portal data; daily Zero test passed; `billing/transactions` shape checked)
- Depends on: ADR 0009 (no tariff config), ADR 0002 (`billing/transactions` in the allowlist), ADR 0015 (surplus and supplier prices)

## Context

- `analysis-data` returns, per community and hour, `earnings` (feed-in) and `spendings` (consumption) next to the kWh. Checked 2026-11-22 for a BEG and a regional EEG: `earnings / generationToCommunity` and `spendings / consumptionFromCommunity` are constant per community and year and equal the effective standard rates on the invoices (including the service fee and VAT or levy). So the portal already knows the standard rate of every hour; the integration doesn't need it as config.
- These amounts ignore the Zero tariff (portal text "Zero-Option nicht berücksichtigt"; 119/87*): Zero kWh are priced at the standard rate. Zero rates are nowhere in the API.
- Zero is netted per day after dynamic distribution (1351/14*, 1397/4*). Hourly Zero has no meaning.
- The portal API has no tariff endpoint (portal client checked 2026-11-22); the community page only links PDF tariff sheets.
- Settled amounts arrive in `billing/transactions`, one invoice per community, direction and month, weeks to months after the month (seen: 3 to 5 weeks, up to 4 months, longer in the first year). Shape (checked 2026-11-22):
  - A JSON list, newest first. Per row: `id`, `date` (settlement date), `amount` in € (positive = paid to the member, negative = charged, `0` is a real invoice), `preview`, `description`, `document`, `energyCommunityName`, `energyCommunityShortName`, `invoiceNumber` and status fields.
  - There is **no community id**. `energyCommunityShortName` equals the community name that `metering-point` returns, so rows are matched by that name.
  - The billed month is only in the German `description`: `Einspeisevergütung <Monat Jahr>` (feed-in), `Strombezug <Monat Jahr>` (consumption), both optionally followed by `(Zero-Tarif-Korrektur folgt)`. Month names are Austrian German (`Jänner`, `Februar`, `März`, …).
  - Reversals: `Energierechnungskorrektur <Monat Jahr>` rows carry no direction; each one negates an earlier row of the same community and month exactly. A rebill follows later as normal rows.
  - Rows without a community (`Mitgliedsbeitrag <Jahr>`, `Einzahlung`) are not energy and are ignored.
  - `document.fileName` contains the member's name and is never logged or stored.
- Offline test (2026-11-22, one member, ten months): the daily Zero estimate below matched the Zero derived from the settlements within about 1 % every month.

## Decision

- **Standard rates come from the portal:** the provisional amount of an hour starts from the portal's `earnings` and `spendings` for that community. An hour where the amount is null, or whose kWh are unknown (ADR 0005), gets no money row. If the fields disappear or change type, validation fails as for any other schema change (ADR 0006).
- **Daily Zero estimate:** for a community with an own-site Zero (a Zero plan in `metering-point` whose sending and receiving points are both in the site), `Z_d = min(Σ_day generation to community, Σ_day consumption from community)`, spread over that day's hours in proportion to the hourly community kWh of each direction. These kWh are repriced from the standard rate (the hour's portal amount ÷ kWh) to the Zero rate of that direction. Other setups get no Zero estimate; settlement corrects them per month.
- **Zero rates are derived, not configured:** for each settled month, community and direction, `zero_rate = (settled − (kWh − Z) × standard_rate) / Z`, with `Z` the month's estimated Zero kWh. The provisional months use the latest derived rate of that community and direction from a month with `Z ≥ 1 kWh`. Until a community has such a month, its Zero kWh stay at the standard rate.
- **Settled (monthly):** the run reads `billing/transactions` once a day. Rows are grouped per community, direction and billed month and summed; a correction row is assigned to the direction of the earlier row it negates. A correction that matches no row leaves that month unsettled (provisional values stay, diagnostics name it). When a month is settled for a community and direction, that month's provisional hours are scaled by `settled / provisional` so the month total equals the settlement. If the provisional total is 0 and the settlement is not, the month stays provisional and diagnostics flag it.
- A change in a settlement (new, corrected, rebilled) rewrites the money statistics from that month on (ADR 0003 sums), even if it lies before the usual fetch window.
- Statistics (unit EUR, `has_sum`, no mean), per community and as a site total: **feed-in income** and **community consumption cost**, both as positive amounts. Ids follow ADR 0012 (`<site>[_<community>]_feed_in_eur`, `…_consumption_eur`; final names in spec 04). The site feed-in income is meant as compensation for a feed-in source in the Energy dashboard (ADR 0011).
- **Surplus income** (optional): surplus kWh (`generation_to_surplus`) × the surplus price (ADR 0015), per site. With it, the site also gets a **total feed-in compensation** = community feed-in income + surplus income, so all feed-in is priced once, community kWh at the community rate. It is not rescaled by community settlements.
- **Import savings** (optional): per community and site, community kWh × supplier energy price (ADR 0015) − community consumption cost. Zero kWh count like other community kWh. Grid-fee and levy reductions for community kWh are not included (ADR 0009).
- Diagnostics: per community, direction and month, the state (provisional, settled, unmatched correction, provisional total 0) and the scale factor; with #89 also the derived Zero rates and which months have a Zero estimate (no kWh). Amounts are left out (diagnostics stay value-free, spec 06; amended 2026-11-23 in #74); the debug export (ADR 0016) has the raw responses when values are needed.

## Consequences

- No community tariff config: rates follow EG Austria's yearly change on their own, and new members see money as soon as their kWh arrive.
- Monthly totals match the invoices once settled; hours and days are approximations, which is all the source allows.
- Values for a month change when it settles, weeks to months later, and again after a correction. With the daily Zero estimate and a derived Zero rate the change at settlement is small.
- If EG Austria changes the Zero rate on 1 January, the first months of the year use last year's derived rate until the first settlement of the new year.
- If the portal stops sending `earnings`/`spendings`, provisional money stops (repair `api_changed`) while settled months stay right.
- A community rename in the portal breaks the settlement match until the next `metering-point` refresh updates the stored name; unmatched rows show up in diagnostics.
