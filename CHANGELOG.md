# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Options: optional comparison sources (grid feed-in, grid consumption, PV production), prefilled from the Energy dashboard settings, which stay unchanged (#50). The options now open with a menu: *Import settings* or *Comparison sources*.

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

[Unreleased]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.1...HEAD
[0.1.1]: https://github.com/magicdude4eva/ha-eg-austria/compare/0.1.0...0.1.1
[0.1.0]: https://github.com/magicdude4eva/ha-eg-austria/releases/tag/0.1.0
