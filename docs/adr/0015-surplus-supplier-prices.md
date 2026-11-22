# 0015: Surplus and supplier prices from a number, an entity or a statistic pair

- Status: accepted (phase 2, decisions in #55 on 2026-11-22)
- Date: 2026-11-22
- Depends on: ADR 0008 (money statistics), ADR 0009 (tariffs, `grid_saving`)

## Context

- Community kWh are priced by the community tariff (ADR 0009). Two more prices are needed for a full picture, both outside EG Austria:
  - the **surplus price**: what the surplus buyer (for example ÖMAG or the supplier) pays for feed-in the communities didn't take;
  - the **supplier price**: the energy price a member would have paid the supplier for the kWh the communities delivered.
- Prices differ between members: fixed per contract period, a monthly index, or hourly spot prices. Some are only known after the month (the ÖMAG market price is published for a month after it ends).
- Community data lands about two weeks late, so the price of a past hour must still be readable then. Recorder states are often purged sooner (default 10 days); long-term statistics are kept.
- A Home Assistant integration for the surplus buyer or supplier may already provide a price sensor, or a compensation/cost statistic next to its energy statistic (the Energy dashboard pair). Its cost statistic can be corrected after the month when the final price is known.
- Grid fees stay on the grid operator's bill for community kWh too, apart from a reduction for some community types (ADR 0009 `grid_saving`). An all-in supplier price would overstate the savings.

## Decision

- Both prices are optional options of the entry. Each one is set by one of three **price sources**:
  1. **Number:** a list of dated periods in ct/kWh incl. VAT, like the tariffs (a period stays valid until the next one).
  2. **Entity:** a price sensor (unit `EUR/kWh` or `ct/kWh`) with long-term statistics (`state_class: measurement`); the hourly mean is the price of that hour.
  3. **Statistic pair:** a cost or compensation statistic in EUR and its energy statistic in kWh; the price of an hour is `Δcost / Δenergy` for that hour, or the month's ratio when the hour's energy is below 0.01 kWh. Late corrections of the cost statistic are picked up when the window is rewritten.
- The options flow checks units like the comparison sources (#50) and can prefill the surplus source from the Energy dashboard's export pair (compensation statistic and feed-in), read-only.
- The **supplier price is the energy price only**, without grid fees and fixed monthly charges; the grid-fee part of the savings comes from the community's `grid_saving` (ADR 0009). The documentation says so with synthetic examples.
- Hours without a price get no surplus or savings row (unknown, ADR 0005) and a repair `price_missing` names the price and the first uncovered date. Community money (ADR 0008) doesn't depend on these prices.
- The statistics they feed: site **surplus income**, site **total feed-in compensation** (community feed-in income + surplus income), and per community and site **import savings** (ADR 0008).

## Consequences

- Fixed, indexed and spot prices all work, and an existing ÖMAG or supplier integration can be reused without copying its prices.
- The total feed-in compensation can replace an "all feed-in at the surplus price" compensation in the Energy dashboard once it has been compared over two settled months (ADR 0010).
- An entity source is only as good as its hourly means: a price that changes inside the hour, or is only known after the month, is better given as a statistic pair or a number.
