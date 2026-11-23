# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- The first import no longer stops with *Portal changed* on a site whose
  metering points have no generation data: the portal leaves out the totals of
  a direction without data points, which now count as empty (#78).

## [0.1.4.2] - 2026-11-23

Patch release: better diagnostics on top of 0.1.4.1. No configuration changes
are needed.

### Added

- Diagnostics show the setup shape (metering point directions and community
  activations as slugs, types and months; no ids or names), the last response
  shapes including `metering-point`, and the last run, all kept across a
  restart, so a failed parse can be traced from one download (#78).

## [0.1.4.1] - 2026-11-22

Patch release: one fix on top of 0.1.4. No configuration changes are needed.

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

[Unreleased]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.4.2...HEAD
[0.1.4.2]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.4.1...0.1.4.2
[0.1.4.1]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.4...0.1.4.1
[0.1.4]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.3...0.1.4
[0.1.3]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.2...0.1.3
[0.1.2]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.1...0.1.2
[0.1.1]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.0...0.1.1
[0.1.0]: https://github.com/magicdude4eva/ha-eg-austria/releases/tag/0.1.0
