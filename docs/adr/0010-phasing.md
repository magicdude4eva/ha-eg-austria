# 0010: Phasing

- Status: accepted
- Date: 2026-11-07
- Depends on: EG Austria's view on publishing (forum 1140/32); store rules at submission time

## Context

The first implementation is validated against one setup (one location with a generation and a consumption metering point, member of a BEG and a regional EEG, own-site Zero). Other members have other setups (several locations, full feed-in, Zero to other locations, new models such as GEA, P2P and EVA: 1397/4*, 1426/3*). Money needs tariff config and settlement parsing, which is riskier than kWh.

## Decision

1. **Phase 1, kWh:** config flow, discovery, hourly external kWh statistics per site and community, validation, repairs, diagnostics. Public repository, installable by link, labelled unofficial.
2. **Phase 2, money:** dated tariffs, provisional and settled money, compensation statistic for the Energy dashboard. Meant to replace an "all feed-in at surplus price" compensation (for example a market-price integration) once proven over at least two settled months.
3. **Phase 3, other setups:** several sites, per-metering-point statistics, Zero to other metering points, new community models; with testers from the community.

Submission to the default store list comes after phase 1 is stable and the store's rules for unofficial APIs are checked.

## Consequences

- Phase 1 is useful alone (metered kWh and community shares) and low risk.
- Phase 2 can be dropped or changed without touching phase 1 statistics.
- Phase 3 may need new statistic ids; phase 1 ids must stay valid (ADR 0012).
