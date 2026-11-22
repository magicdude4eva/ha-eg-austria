# 0009: No community tariff configuration

- Status: accepted (phase 2, decisions in #55 on 2026-11-22). Replaces the earlier proposal "Tariffs as dated configuration".
- Date: 2026-11-07, rewritten 2026-11-22

## Context

- The first proposal had members enter their community tariffs per year as options (rates, VAT or levy mode, Zero rates), with a repair for missing periods and a reminder on 1 January.
- EG Austria tariffs are fixed per calendar year and change on 1 January (990/138*). BEGs charge VAT; regional EEGs under the small-business rule charge a levy ("MAT") instead. Entering this correctly is error-prone, and members don't know next year's rates on 1 January.
- The portal API has no tariff endpoint, but `analysis-data` carries the standard-rate amount of every hour (`earnings`, `spendings`), and `billing/transactions` carries what was actually billed (ADR 0008). Together they give the standard rates directly and the Zero rates by derivation.
- Grid-fee reductions for community kWh (for example the reduced grid energy price and levy exemptions for a regional EEG) are on the grid operator's bill. They depend on the grid operator, the community type and, since 2026, the time of year and day (summer midday grid price). Neither the portal nor a supplier integration provides them.

## Decision

- **No community tariff options.** Standard rates come from the portal's hourly amounts, Zero rates are derived from settlements (ADR 0008). There is no `tariff_missing` repair and no 1 January reminder.
- **Grid-fee and levy reductions are out of scope** for now. Import savings (ADR 0015) count the supplier energy price only, and the documentation says that EEG savings are therefore understated. The open question (how members would want to enter or source these reductions) is asked in the EG Austria forum thread of the integration and tracked in #76.
- Membership fees, payouts, the levy refund and bonus points are not energy and are not part of the money statistics.
- Examples in the documentation use synthetic rates only.

## Consequences

- Nothing to enter for the communities; a new year's rates appear with the first data of the year.
- The integration depends on the portal's `earnings`/`spendings` for provisional values; settlements stay the truth per month.
- EEG members see lower savings than their real ones until grid-fee reductions are added.
