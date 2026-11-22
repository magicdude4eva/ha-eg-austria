# 0008: Provisional vs settled money, Zero estimated per day

- Status: accepted (phase 2, decisions in #55 on 2026-11-22)
- Date: 2026-11-07, updated 2026-11-22 (daily Zero test passed; `billing/transactions` shape checked)
- Depends on: ADR 0009 (tariffs), ADR 0002 (`billing/transactions` in the allowlist), ADR 0015 (surplus and supplier prices)

## Context

- The portal's per-hour `earnings` and `spendings` are a service estimate that prices all community kWh at the standard tariff and ignores the Zero tariff (portal text "Zero-Option nicht berücksichtigt"; 119/87*).
- Zero is netted per day after dynamic distribution (1351/14*, 1397/4*). Hourly Zero has no meaning.
- Settled amounts arrive in `billing/transactions`, one invoice per community, direction and month, weeks to months after the month (seen: 3 to 5 weeks, up to 4 months, longer in the first year). Shape (checked 2026-11-22):
  - A JSON list, newest first. Per row: `id`, `date` (settlement date), `amount` in € (positive = paid to the member, negative = charged, `0` is a real invoice), `preview`, `description`, `document`, `energyCommunityName`, `energyCommunityShortName`, `invoiceNumber` and status fields.
  - There is **no community id**. `energyCommunityShortName` equals the community name that `metering-point` returns, so rows are matched by that name.
  - The billed month is only in the German `description`: `Einspeisevergütung <Monat Jahr>` (feed-in), `Strombezug <Monat Jahr>` (consumption), both optionally followed by `(Zero-Tarif-Korrektur folgt)`. Month names are Austrian German (`Jänner`, `Februar`, `März`, …).
  - Reversals: `Energierechnungskorrektur <Monat Jahr>` rows carry no direction; each one negates an earlier row of the same community and month exactly. A rebill follows later as normal rows.
  - Rows without a community (`Mitgliedsbeitrag <Jahr>`, `Einzahlung`) are not energy and are ignored.
  - `document.fileName` contains the member's name and is never logged or stored.
- Settled Zero kWh per month can be derived from the gap between the standard-tariff estimate and the settled amount: `Z = (provisional − settled) / (fix_rate − zero_rate)` for each direction.
- Offline test (2026-11-22, one member, ten months): the daily estimate below matched the Zero derived from the settlements within about 1 % every month.

## Decision

- Money statistics are computed by the integration from kWh and dated tariffs (ADR 0009), never taken from portal `earnings`/`spendings`.
- **Provisional (hourly):** kWh × tariff. For a community with an own-site Zero (a Zero plan in `metering-point` whose sending and receiving points are both in the site), a daily Zero estimate `Z_d = min(Σ_day generation to community, Σ_day consumption from community)` is spread over that day's hours in proportion to the hourly community kWh of each direction and priced at the Zero rates; the rest at the standard rates. Other setups get no Zero estimate (standard rates only); settlement corrects them per month.
- **Settled (monthly):** the run reads `billing/transactions` once a day. Rows are grouped per community, direction and billed month and summed; a correction row is assigned to the direction of the earlier row it negates. A correction that matches no row leaves that month unsettled (provisional values stay, diagnostics name it). When a month is settled for a community and direction, that month's provisional hours are scaled by `settled / provisional` so the month total equals the settlement. If the provisional total is 0 and the settlement is not, the month stays provisional and diagnostics flag it.
- A change in a settlement (new, corrected, rebilled) rewrites the money statistics from that month on (ADR 0003 sums), even if it lies before the usual fetch window.
- Statistics (unit EUR, `has_sum`, no mean), per community and as a site total: **feed-in income** and **community consumption cost**, both as positive amounts. Ids follow ADR 0012 (`<site>[_<community>]_feed_in_eur`, `…_consumption_eur`; final names in spec 04). The site feed-in income is meant as compensation for a feed-in source in the Energy dashboard (ADR 0011).
- Diagnostics: per community and month, provisional total, settled total, scale factor, derived and estimated Zero kWh, and the state (provisional, settled, unmatched correction).
- **Surplus income** (optional): surplus kWh (`generation_to_surplus`) × a surplus price (ADR 0015), per site. With it, the site also gets a **total feed-in compensation** = community feed-in income + surplus income, so all feed-in is priced once, community kWh at the community tariff. It is provisional until the surplus buyer's price is final; it is not rescaled by community settlements.
- **Import savings** (optional): per community and site, community kWh × (supplier price + the community's grid saving, ADR 0009/0015) − community consumption cost. Zero kWh count like other community kWh.

## Consequences

- Monthly totals match the invoices once settled; hours and days are approximations, which is all the source allows.
- Values for a month change when it settles, weeks to months later, and again after a correction. With the daily Zero estimate the change at settlement is small.
- A community rename in the portal breaks the match until the next `metering-point` refresh updates the stored name; unmatched rows show up in diagnostics.
