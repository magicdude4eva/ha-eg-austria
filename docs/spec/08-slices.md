# 08: Slices and order

Each slice is one small, reviewable subsession ending in one PR against the public repository, with tests. Slices name the files they own so parallel slices don't touch the same files.

## Prerequisites

- **P0 Repo and tooling** (planned subsession, not a slice): repository, license, `custom_components/eg_austria` skeleton (`manifest.json`, `const.py`, empty `__init__.py`), `hacs.json`, CI with `hassfest`, store validation, `pytest`, `ruff`, pre-commit, issue templates. Copies these ADRs and specs into `docs/`.
- **G1 Post-update re-test** (main session, after 2026-11-09 18:00 CET): confirms or updates every **[verify]** item in spec 01. Also runs the analysis tests T-Q3-cache (offline checks T-Q3a-h), T-MEM. Results go into spec 01/05 as a doc-only PR in P0 or S1. **Done 2026-11-21** (build `20d1c18`): see the G1 notes in specs 01-05 and 07.

## Slices

| # | Slice | Owns | Depends on | Done when |
|---|---|---|---|---|
| S1 | Models, fixtures generator, API client | `models.py`, `api.py`, `exceptions.py`, `token.py` (kind + JWT `exp` only), `redact.py` (`redact_text()` only), `tests/fixtures/*`, `tests/conftest.py`, `test_api.py`, `test_models.py`, `test_fixtures.py` | P0, G1 | Test cases 1-12 pass; whitelist test present |
| S2 | Token parsing and config flow (user, site, communities, reauth, reconfigure, options), translations | `config_flow.py`, `token.py`, `strings.json`, `translations/*`, `test_config_flow.py`; additive: `const.py` (entry keys, option defaults), `manifest.json` (`config_flow: true`), `__init__.py` (no-op `async_setup_entry`/`async_unload_entry` until S5) | S1 | Cases 31-36 pass |
| S3 | Validation rules | `validation.py`, `test_validation.py` | S1 | Cases 13-15 pass |
| S4 | Statistics writer (ids, metadata, series building, sums, stored-row rule) | `statistics.py`, `test_statistics.py` | S1 (models only) | Cases 16-21 pass |
| S5 | Coordinator, scheduler, store, services | `coordinator.py` (with the store), `importer.py`, `__init__.py`, `services.yaml`, `test_coordinator.py`, `test_init.py`; additive: `strings.json`/`translations/*` (services, service errors, statistic labels), `tests/conftest.py` (`FakeClient`, `entry_data`, SQL log level) | S2, S3, S4 | Cases 22-30, 40-41 pass |
| S6 | Repairs, diagnostic sensors, diagnostics and redaction | `repairs.py`, `sensor.py`, `diagnostics.py`, `redact.py`, related tests; small additions to `coordinator.py` (`stale_client`, `portal_version`, `site`), `api.py` (`schema_fingerprints`), `token.py` (`jwt_issued_at`), `__init__.py` (sensor platform, repairs), strings, README "Repairs and diagnostics" (link targets) | S5 (hooks), S1 | Cases 37-39 pass |
| S7 | First run on the development box | none in the repo (private notes) | S6 | Installed, initial import done, private acceptance checks recorded, issues filed for findings |
| S7a | Fixes found in S7: activation-aware chunk planning (#16), shared portal session that is never closed (#17) | `importer.py` (`SiteInfo.activations`, `plan_chunks()`, `request_count(plan)`), `statistics.py` (`active_days`), `api.py` (`get_portal_session()`), `__init__.py`, `config_flow.py`, `coordinator.py`; `FakeClient` and the fixture generator (reduced body, residual over active communities); specs 02/03/04/07 | S6 | Case 30a passes; S7 resumes |
| S7b | Config flow from S7: create the token from the session cookie after consent, revoke step (#18); full metering-point labels (#19); communities as a labelled multi-select plus slug step (#20) | `token_api.py` (new), `config_flow.py`, `models.py` (labels only), `const.py` (`token_id`), `diagnostics.py`/`redact.py` (`token_id`), strings and translations, `test_token_api.py`, `test_config_flow.py`; ADR 0002/0007, specs 01/02/06/07 | S7a | Cases 8a, 31a, 31b, 33, 36 pass; testing on the box in S7 |
| Fix #26 | Store loaded before the sensor platform | `coordinator.py` (`async_load()`), `__init__.py`, `test_sensor.py`; spec 03 | S7b | Sensors show stored dates after a restart |
| Icon #21, #22 | Brand images shipped in the integration (`brand/`: icon at 256 and 512 px, served locally by HA's `brands` integration, accepted by the HACS brands check); README note on the "Name and assign" dialog | `custom_components/eg_austria/brand/*`, CI (`ignore: brands` removed), `README.md` (one note) | Fix #26 | Icon shows in Settings > Devices & services; HACS validation passes without ignores |
| S8 | Docs and release 0.1.0 | `README.md`, `docs/*`, `CHANGELOG.md` | S7 | Tagged release, installable by link |

Shared files, each created by S1 and extended later (S2 and S6 start after S1 is merged):

- `redact.py`: S1 creates it with the `redact_text()` helper only; S6 extends it.
- `token.py`: S1 creates it with `TokenKind` and `jwt_expiry()` (the client needs the kind and the `exp` of a session JWT); S2 adds the parsing of pasted input.
- `const.py` holds `VERSION` (S1, used in the User-Agent; keep equal to `manifest.json`); later slices may add constants.
- `tests/conftest.py` holds `load_fixture`, `FakePortal` and `FakeClock` (S1); later slices may add fixtures but not change these.

## Order and parallelism

```
P0 ─┬─ G1 ─ S1 ─┬─ S2 ─┐
    │           ├─ S3 ─┼─ S5 ─ S6 ─ S7 ─ S8
    │           └─ S4 ─┘
```

1. P0 and G1 (G1 needs the update to be over, P0 doesn't).
2. S1.
3. S2, S3, S4 in parallel (separate files).
4. S5.
5. S6.
6. S7 on the box (restart at this milestone only, with the usual post-restart checks).
7. S8.

## Phase 2 preview (not specified yet)

Billing models and transaction parser (German month text, reversals), tariff options (ADR 0009), money series with daily Zero estimate and monthly rescale (ADR 0008), compensation statistic. Starts after T-Q4 and T-Q9 in the analysis and two months of phase 1 running.
