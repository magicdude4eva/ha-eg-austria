# Architecture decision records

Short [MADR](https://adr.github.io/madr/)-style records for the `eg_austria` Home Assistant integration. One decision per file. Status values: `proposed`, `accepted`, `superseded by NNNN`, `deprecated`.

Forum references are `topic/post` on forum.eg-austria.at; `*` marks an EG Austria staff post.

| # | Decision | Status |
|---|---|---|
| [0001](0001-custom-integration.md) | Custom integration, not an add-on or REST sensors | accepted |
| [0002](0002-read-only-get-whitelist.md) | Read-only: a whitelist of GET endpoints | accepted |
| [0003](0003-external-statistics-overwrite.md) | External statistics, overwritten on re-fetch | accepted |
| [0004](0004-fetch-window.md) | Fetch window: completeness-driven, at least 2 months, periodic 13-month re-fetch, Hour aggregation, maintenance-aware | accepted |
| [0005](0005-unknown-not-zero.md) | Null or incomplete data is unknown, never zero | accepted |
| [0006](0006-fail-safe-validation.md) | Fail-safe validation and repairs | accepted |
| [0007](0007-token-handling.md) | Token handling and expiry warning; renewal unresolved | accepted (renewal open) |
| [0008](0008-provisional-settled-money.md) | Provisional vs settled money, Zero derived monthly | proposed (phase 2) |
| [0009](0009-tariffs-dated-config.md) | Tariffs as dated configuration | proposed (phase 2) |
| [0010](0010-phasing.md) | Phasing: kWh, then money, then other setups | accepted |
| [0011](0011-keep-live-energy-source.md) | Keep the live local meter as Energy dashboard source by default | accepted |
| [0012](0012-statistic-scope-and-ids.md) | Statistic scope (site, community) and stable ids | accepted |

Several records depend on the portal API as it was before the mandatory portal update of 2026-11-09. Each such record says so under "Depends on".
