# Phase 1 specifications

Phase 1 of the `eg_austria` integration: hourly kWh statistics from the EG Austria member portal, read-only. Decisions are in [`../adr`](../adr/README.md); these specs say how to build them.

| Spec | Covers |
|---|---|
| [01 API client](01-api-client.md) | Endpoints, parameters, response models, errors, request policy |
| [02 Config flow](02-config-flow.md) | Token entry, discovery, options, reauth |
| [03 Coordinator and schedule](03-coordinator-schedule.md) | Run types, windows, chunking, maintenance, state |
| [04 Statistics](04-statistics.md) | Ids, units, metadata, field mapping, sum computation |
| [05 Validation](05-validation.md) | Shape and plausibility rules and what each failure does |
| [06 Repairs, entities, diagnostics](06-repairs-diagnostics.md) | Repair issues, diagnostic entities, redaction |
| [07 Test plan](07-test-plan.md) | Synthetic fixtures and test cases |
| [08 Slices](08-slices.md) | Work split into small independent pieces, with order |

## Conventions

- **Pre-update:** every endpoint, parameter and field name here is as observed before the mandatory portal update of 2026-11-09. Items marked **[verify]** must be confirmed against the post-update API before slice S1 is merged. Items marked **[test]** are open questions with a planned check.
- Times: the API's `dateTimeFrom` is UTC; days and dates in request parameters are Austrian local days (Europe/Vienna; the portal states this for its day views). Whether `endDate` is inclusive is still **[verify]**.
- Energy in kWh. Money is phase 2 and not specified here.
- All examples use synthetic ids (`1001`, `2001`, ...), synthetic names (`Example BEG`) and synthetic values.

## Glossary

| Term | Meaning |
|---|---|
| BEG | Bürgerenergiegemeinschaft, citizen energy community, can span grid areas |
| EEG | Erneuerbare-Energie-Gemeinschaft, renewable energy community, regional or local |
| Member | The portal account holder; one member number |
| Location | A site with one or more metering points |
| Metering point | Zählpunkt; direction consumption or generation |
| Participation factor | Share of a metering point assigned to a community; changes up to daily |
| Zero | Tariff option gifting community energy to own or linked metering points, netted per day |
| Residual | Energy not covered by communities: bought from the supplier, or sold to the surplus buyer |
| VEZ | Cross-grid energy allocator that computes community shares for grid operators |
