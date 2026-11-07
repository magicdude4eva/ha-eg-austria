# 0008: Provisional vs settled money, Zero derived monthly

- Status: proposed (phase 2)
- Date: 2026-11-07
- Depends on: an offline test of the daily Zero estimate; the `billing/transactions` shape

## Context

- The portal's per-hour `earnings` and `spendings` are a service estimate that prices all community kWh at the standard tariff and ignores the Zero tariff (portal text "Zero-Option nicht berücksichtigt"; 119/87*).
- Zero is netted per day after dynamic distribution (1351/14*, 1397/4*). Hourly Zero has no meaning.
- Settled amounts arrive monthly per community and direction in `billing/transactions`, weeks to months late, with reversals and rebills (119/290*).
- Settled Zero kWh per month can be derived from the gap between the standard-tariff estimate and the settled amount: `Z = (provisional − settled) / (fix_rate − zero_rate)` for each direction.

## Decision

- Money statistics are computed by the integration from kWh and dated tariffs (ADR 0009), not taken from portal `earnings`/`spendings`.
- **Provisional (hourly):** kWh × tariff, with a daily Zero estimate per community: `Z_d = min(Σ_day generation to community, Σ_day consumption from community)` for an own-site Zero, spread over that day's hours in proportion to the hourly community kWh, priced at the Zero rate. Setups the estimate doesn't cover fall back to "no Zero" (pure standard rate).
- **Settled (monthly):** when a month's transaction for a community and direction is present, rescale that month's provisional hours by `settled / provisional` so the month total equals the settlement. Reversals and rebills are netted per month.
- Derived monthly Zero kWh and the rescale factor go into diagnostics as quality indicators.
- One money statistic per community and direction, plus a site total suitable as Energy dashboard compensation (ADR 0011).

## Consequences

- Monthly totals match the bank account once settled; hours and days are approximations, which is all the source allows.
- Values for a month change when it settles, up to two months later.
- If the daily estimate fails its test, use plain rescaling (no Zero in provisional values), at the cost of a larger change at settlement.
