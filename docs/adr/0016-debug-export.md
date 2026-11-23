# 0016: Opt-in redacted debug export with values

- Status: accepted
- Date: 2026-11-23
- Amends: spec 06 "Diagnostics" (the opt-in export it reserved for phase 3)

## Context

- Diagnostics are value-free by design (spec 06). Issue #78 showed the limit: the first diagnostics said `S2`, the next ones `totalGeneration`, and we still had to ask the reporter which metering points and activations they have. Shapes say *that* a response differs, not *why*.
- Setups differ (several metering points per direction, communities joined at different dates, consumption-only sites), and maintainers can only test their own.
- Spec 06 already promised a separate, opt-in redacted export for this.

## Decision

- A user-started action `eg_austria.debug_export` (entry, 1-31 recent days, default 7) writes one JSON file `eg_austria_debug_<site>_<UTC time>.json` to the configuration folder and returns its path. It never runs by itself and changes no statistics.
- It sends only the GETs a run sends (ADR 0002 whitelist, request spacing), under the run lock (refused while a run is active): `version`, `maintenance`, `portal-session`, `metering-point`, `day-completeness` for the window, and `analysis-data` per selected community (Day and Hour, all selected metering points) plus per community and metering point (Day). That is `5 + 4 x communities` requests for two metering points.
- Each response is kept **as the portal sent it**, before parsing, through a `raw_sink` hook on the client. A parse error doesn't stop the export: the step records it and the export goes on. With every response go its request parameters, its `schema_fingerprint` and, for `analysis-data`, a `key_presence` summary per direction (point count, missing totals and day flags).
- **Energy and money values are kept**, so an export can be loaded as test fixtures. The action description, README and file `notice` say so.
- **Ids are replaced by stable synthetic values**, the same ones the test fixtures use (member `9000001`, location `5001`, metering points `1001`…, communities `2001`…, metering point numbers `AT0…0<n>`). They are replaced consistently across responses and request parameters, so the file still parses. Every other `TO_REDACT` key (names, addresses, emails, tokens, `ecNumber`, …) is removed, and every remaining string is scrubbed with `redact_text()` against the original ids.
- The startup check's `metering-point` response also fills the diagnostics `setup` section, so diagnostics right after a restart show the setup without waiting for a run.

## Consequences

- One action gives maintainers enough to reproduce a parsing or validation failure for a setup they don't have.
- A user who shares an export shares their energy values for a few days; that is their choice, and the bug report template says so.
- The export costs a few requests (about 13 for two communities); it is manual, so the portal load is negligible.
- New portal keys that carry ids would not be replaced automatically; they stay covered by `TO_REDACT` and the string scrub, and the privacy tests list the known id keys.
