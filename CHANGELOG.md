# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- € in the generated dashboard (#75): every community segment of the
  *Import* and *Feed-in* donuts shows its cost or income, the surplus buyer its
  surplus income, and each donut a € line (cost and savings, income or
  feed-in compensation). The community cards show € next to fed in and drawn,
  plus savings with a supplier price. Amounts are marked *settled*, *partly
  settled* or *provisional* from the invoices the integration reads.
- € summary sensors (#75) for the month so far and the last complete month,
  like the share sensors (ADR 0014, no state class): feed-in income and cost
  of consumption (site and per community), and with the matching price surplus
  income, feed-in compensation, import savings and supplier energy cost.
  Attribute `settlement`: `settled`, `partly_settled` or `provisional`.
- Supplier energy cost (#75): new statistic `<site>_supplier_eur` with a
  supplier price, consumption from the supplier × the supplier price (energy
  part only), the import-side mirror of surplus income. Shown on the
  *Supplier* segment of the *Import* donut.
- README and the dashboard's (i) text: setting up €, the € summary sensors,
  € in the dashboard, why the € differ from the portal's Energieanalyse (it
  prices Zero kWh at the standard rate), and when to use *Feed-in
  compensation* in the Energy dashboard (after two settled months).

### Upgrade

- The settled / provisional marker needs one run after the update: until
  then every amount shows as provisional. Reload the dashboard page after
  that run.

## [0.2.0.1] - 2026-11-23

Patch release: one validation fix on top of 0.2.0. No configuration changes
are needed.

### Fixed

- One community reporting a faulty 0 for the meter while another has the
  reading no longer holds the site meter statistic for the whole month
  (P6 / P4): a faulty meter value counts as unknown, and the site meter is
  read from the first community whose hour isn't faulty (#78).

## [0.2.0] - 2026-11-23

Phase 2: money in € (#55). Community income and cost come from the portal and
your invoices; no tariffs to enter. **Requires Home Assistant 2026.11.0 or
later.**

### Upgrade

- No configuration changes are needed. The new € statistics appear with the
  next run.
- The first run after the update reads your invoices and re-fetches once from
  your earliest settled month (up to your earliest activation), so it takes
  a little longer and sends more requests than a daily run.
- Optional: set a *Surplus price* and a *Supplier price* in the options to get
  surplus income, feed-in compensation and import savings.

### Added

- Money statistics in € (#72): feed-in income and cost of consumption per
  community and for the site, from the portal's hourly amounts.
- Settlement from the portal's invoices (`billing/transactions`, #74): once a
  month is billed, its € statistics per community are scaled so the month
  equals the invoice, including the Zero tariff, reversals and rebills. A
  month whose invoice changes later is fetched again. One more read-only
  request per run.
- Zero estimate for months not billed yet (#89): with an own-site Zero plan,
  each day's Zero kWh (the smaller of feed-in to and consumption from the
  community) are priced at a Zero rate derived from the settled invoices, so
  provisional months come close to the invoice.
- Optional surplus price (fixed periods, price sensor or monthly price table
  such as the ÖMAG integration's) for *Surplus income* and *Feed-in
  compensation*, and a *price missing* repair.
- Optional supplier price (#73, same sources) for *Import savings* per
  community and for the site (community kWh at your supplier's energy price
  minus what the community charged), and a *supplier price missing* repair.
  Grid-fee and levy reductions are not included yet (#76).

### Fixed

- No *price missing* repair for hours before the price source's first price,
  such as a history import older than a price table that only keeps the
  running year (#90).
- Diagnostics list the surplus, feed-in compensation and savings statistics,
  and the debug export keeps Zero plan numbers recognisable (synthetic, the
  same as the metering points).

### Documentation

- README: My Home Assistant buttons to open the repository in the Marketplace
  and to start the setup; money, surplus and supplier price sections.

## [0.1.4.3] - 2026-11-23

Patch release: a parser fix and a debug export on top of 0.1.4.2 (from branch
`release/0.1.4.x`). No configuration changes are needed.

### Added

- Debug export (`eg_austria.debug_export`, #84): writes the portal responses
  of a few recent days to a JSON file in the configuration folder, with ids
  replaced by synthetic values and names, addresses and tokens removed, for
  issue reports. Contains energy values; opt-in and only on request.
- Diagnostics show the setup shape right after a restart, from the startup
  check (#84).

### Fixed

- The first import no longer stops with *Portal changed* on a site whose
  metering points have no generation data: the portal leaves out the totals of
  a direction without data points, which now count as empty (#78).

## [0.1.4.2] - 2026-11-23

Patch release: better diagnostics on top of 0.1.4.1 (from branch
`release/0.1.4.x`). No configuration changes are needed.

### Added

- Diagnostics show the setup shape (metering point directions and community
  activations as slugs, types and months; no ids or names), the last response
  shapes including `metering-point`, and the last run, all kept across a
  restart, so a failed parse can be traced from one download (#78).

## [0.1.4.1] - 2026-11-22

Patch release: one fix on top of 0.1.4 (from branch `release/0.1.4.x`). No
configuration changes are needed.

### Fixed

- Diagnostics keep the redacted error of the last run (for example which
  portal field was missing) across a restart (#78).

## [0.1.4] - 2026-11-22

Documentation only; no functional changes. Released for the Marketplace listing
(#38). No configuration changes are needed.

### Documentation

- README: reload the browser page if *Custom* is missing in *Add dashboard*
  after installing or updating.
- README: the install status links to the latest release instead of naming
  0.1.0.

## [0.1.3] - 2026-11-22

Reworks the *Energy community* dashboard after its first real use. Requires
Home Assistant 2026.11.0 or later. No configuration changes are needed; after
the update, reload the dashboard page in the browser once.

### Changed

- *Energy community* dashboard reworked after the first real use (#61). The
  date picker now opens on this month (it opened on today, while community
  data is about two weeks late, so totals showed `?` and the by-day charts
  were empty); the dashboard stores that default in the browser until you
  pick another preset. Under the picker, following it, two small cards of the
  integration's own replace the gauges, the four totals and the per-community
  tiles: a period card with donuts for
  import, feed-in and PV (like the portal's overview), and one compact card
  per community with kWh fed in and drawn and their share of the total. Both
  count only up to *Data complete until*. Then the feed-in and import split
  for the picked period. Charts in pairs, fixed series colours, metered vs
  your source as bars, as the two lines covered each other; the import status
  with short names, including *Token expires*.

### Added

- An (i) icon on the dashboard's period and community cards explains how the numbers are counted, like the core energy cards (#63).
- Hovering or tapping a segment of the period card's donuts (or its legend row) shows its share and kWh, the same part in the previous period with the change in %, and its best day (#65).
- The dashboard's import status is one row above the date picker: *Last run* in green, amber or red, *Token expires* as a date (amber in the last two weeks, red once expired); a click on a value opens its details (#63).
- *Add dashboard* pre-fills the title *Energy community* (German
  *Energiegemeinschaft*) and the icon `mdi:home-group` (#61).

## [0.1.2] - 2026-11-22

Adds comparison sources, summary sensors and a ready-made dashboard. Requires
Home Assistant 2026.11.0 or later. No configuration changes are needed.

### Added

- Options: optional comparison sources (grid feed-in, grid consumption, PV production), prefilled from the Energy dashboard settings, which stay unchanged (#50). The options now open with a menu: *Import settings* or *Comparison sources*.
- Summary sensors: shares in percent for the month so far and the last complete month (feed-in to and import from communities, per community, and with comparison sources the PV split and the deviation of your own meter), without long-term statistics (ADR 0014, #51).
- Diagnostic sensor *Meter data until* (#51).
- Ready-made *EG Austria energy community* dashboard: add it once under **Settings → Dashboards → Add dashboard**; it is built from core cards on every load from the entry's statistics, summary sensors and comparison sources (#52).

## [0.1.1] - 2026-11-22

Maintenance release with no change in behaviour. Requires Home Assistant
2026.11.0 or later.

### Changed

- **Strict typing:** the integration passes mypy in strict mode with Home
  Assistant core's settings, checked in CI and pre-commit (#37).
- Schemas use `probatio`, as Home Assistant itself does, and the recorder
  helpers come from `homeassistant.helpers.recorder`.
- The actions `eg_austria.refetch` and `eg_austria.release_held` moved to
  `services.py`; they work as before.

### Added

- `quality_scale.yaml`: a self-assessment against all 54 rules of the Home
  Assistant integration quality scale.

## [0.1.0] - 2026-11-22

First release. Requires Home Assistant 2026.11.0 or later.

### Added

- **Hourly energy-community statistics** from the EG Austria member portal as
  Home Assistant external statistics, per site and per energy community:
  consumption, generation, from and to the community, and the residual from
  your supplier or to your surplus buyer. Ready for the Energy dashboard and
  statistics cards. Read-only: the integration never changes anything in the
  portal.
- **Setup wizard:** paste a session cookie from the portal, then either let
  the wizard create a read-only access token that lasts a year (recommended)
  or keep the session cookie (about a month). An existing personal access
  token can be pasted instead. Metering points are listed with their full
  number, facility name, type and participation factor; communities are
  labelled by name.
- **Reauthentication and reconfigure** to paste a new cookie or token without
  removing the entry.
- **Options:** plausibility limit per hour, daily and monthly refetch windows,
  and whether to import the history since the first activation.
- **Scheduled updates:** a daily run at a random time between 03:00 and 06:00,
  a monthly run over the last 13 months for late corrections, an initial run
  for the history, and a catch-up run after a restart. Announced portal
  maintenance only postpones a run.
- **Corrections land in the right hour:** every run rewrites its whole window,
  and an hour the portal hasn't completed yet stays empty instead of being
  written as 0 kWh.
- **Checks before writing:** a changed portal format writes nothing;
  implausible values are held back while everything else is written.
- **Actions** `eg_austria.refetch` (fetch a date range again) and
  `eg_austria.release_held` (write held-back days after checking them).
- **Diagnostic sensors** on the service device: token expiry, data complete
  until, last import, last run status (kept across restarts) and portal
  version (disabled by default). They stay available while the portal is
  down.
- **Repairs** for an expiring token, a changed portal, held data and an
  unreachable portal.
- **Diagnostics download** on the device page, with tokens, ids, names and
  values redacted.
- **Device page** shows the author, model, version and a link to the portal;
  "Visit" opens the project page.
- **Setup check:** the portal is tested before the entry is set up, so a wrong
  token or an unreachable portal shows up right away, and an unreachable
  portal is logged once instead of on every run.
- **Integration icon**, and English and German translations for every field,
  error and message.
- **Documentation:** README as the integration page (setup, options, actions,
  data updates, troubleshooting, removal), a German README and a privacy
  statement ([PRIVACY.md](PRIVACY.md)).

[Unreleased]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.2.0.1...HEAD
[0.2.0.1]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.2.0...0.2.0.1
[0.2.0]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.4.3...0.2.0
[0.1.4.3]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.4.2...0.1.4.3
[0.1.4.2]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.4.1...0.1.4.2
[0.1.4.1]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.4...0.1.4.1
[0.1.4]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.3...0.1.4
[0.1.3]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.2...0.1.3
[0.1.2]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.1...0.1.2
[0.1.1]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.0...0.1.1
[0.1.0]: https://github.com/magicdude4eva/ha-eg-austria/releases/tag/0.1.0
