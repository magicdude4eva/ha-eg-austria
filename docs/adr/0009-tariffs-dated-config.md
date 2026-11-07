# 0009: Tariffs as dated configuration

- Status: proposed (phase 2)
- Date: 2026-11-07

## Context

- EG Austria community tariffs are fixed per calendar year and change on 1 January (990/138*). Members have different tariffs (Fix, Zero, others; different communities; producers, consumers, full feed-in).
- BEGs charge VAT on fees; regional EEGs under the small-business rule charge a levy ("MAT") instead, which may be partly refunded the following year (1231/4*, 562/12*).
- Grid-fee reductions for community energy appear on the grid operator's bill, not EG Austria's.
- Hard-coded rates would be wrong for most users and for every new year.

## Decision

- Tariffs are options, entered per community as a list of periods `{valid_from, valid_to?, feed_in_energy, consumption_energy, service_fee, fee_tax_mode: vat|levy|none, fee_tax_percent, zero_energy, zero_fee}` in ct/kWh net.
- A period without `valid_to` stays valid until the next one. No defaults are shipped; the money statistics stay off until a tariff covers the period.
- Hours outside every period get no money row (unknown, ADR 0005), and a repair `tariff_missing` names the community and date.
- Example in documentation uses synthetic rates only.

## Consequences

- Users enter tariffs once a year; the repair reminds them on 1 January.
- Settled money (ADR 0008) corrects small tariff mistakes per month, but provisional values are only as good as the config.
