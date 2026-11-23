# 0015: Surplus and supplier prices: number, price sensor or monthly price table

- Status: accepted (phase 2, decisions in #55 on 2026-11-22)
- Date: 2026-11-22
- Depends on: ADR 0008 (money statistics), ADR 0009 (no community tariff config)

## Context

- Community kWh are priced from portal data (ADR 0008). Two prices come from outside EG Austria:
  - the **surplus price**: what the surplus buyer pays for feed-in the communities didn't take. For many members that is ÖMAG, whose market price is set per month and published after the month ends;
  - the **supplier price**: the energy price a member would have paid the supplier for the kWh the communities delivered. It is a fixed price per contract period for most members, and a monthly index or hourly spot price for some.
- Community data lands about two weeks late, so the price of a past hour must still be readable then. Recorder states are often purged sooner (default 10 days); long-term statistics and entity attributes are not.
- Existing integrations already provide these prices: a price sensor for dynamic tariffs, or, for ÖMAG, the [ha-oemag](https://github.com/nn-homeassistant/ha-oemag) integration's price table sensor, whose attribute `preise` maps `YYYY-MM` to ct/kWh for every published month.

## Decision

- Both prices are optional options of the entry. Each is set by one **price source**:
  1. **Number:** a list of dated periods in ct/kWh incl. VAT (a period stays valid until the next one). For fixed contracts.
  2. **Price sensor:** an entity with unit `EUR/kWh` or `ct/kWh` and long-term statistics (`state_class: measurement`); the hourly mean is the price of that hour. For hourly or daily dynamic prices.
  3. **Monthly price table:** an entity plus the name of its attribute that maps `YYYY-MM` to a price, with the unit (`ct/kWh` or `EUR/kWh`) chosen in the options. For ha-oemag: the price table sensor, attribute `preise`, unit `ct/kWh`. The month's price applies to every hour of that month. Months not in the table yet are unknown.
- The options flow checks units and, for a table, that the attribute holds month keys and numbers.
- The **supplier price is the energy price only**, without grid fees and fixed monthly charges (ADR 0009). The documentation says so with synthetic examples.
- Hours without a price get no surplus or savings row (unknown, ADR 0005) and a repair `price_missing` names the price and the first uncovered date; the repair is not raised for the current and the previous month of a monthly table, whose price is expected to come later. Nor is it raised for hours before the source's first price (for example a history import older than a price table that only keeps the running year): those hours stay without a row, since no price can be added there (amended 2026-11-23). Community money (ADR 0008) doesn't depend on these prices.
- The statistics they feed: site **surplus income**, site **total feed-in compensation** (community feed-in income + surplus income), and per community and site **import savings** (ADR 0008). Surplus and savings rows are rewritten with the window, so a price published or corrected later lands in the statistics on the next run.

## Consequences

- Fixed, monthly and spot prices all work, and an existing ÖMAG or supplier integration is reused without copying its prices.
- The total feed-in compensation can replace an "all feed-in at the surplus price" compensation in the Energy dashboard once it has been compared over two settled months (ADR 0010).
- Surplus income for the current month stays empty until the monthly price is published.
- A price sensor is only as good as its hourly means: a price that changes inside the hour is averaged.
