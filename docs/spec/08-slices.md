# 08: Slices and order

Each slice is one small, reviewable subsession ending in one PR against the public repository, with tests. Slices name the files they own so parallel slices don't touch the same files.

## Prerequisites

- **P0 Repo and tooling** (planned subsession, not a slice): repository, license, `custom_components/eg_austria` skeleton (`manifest.json`, `const.py`, empty `__init__.py`), `hacs.json`, CI with `hassfest`, store validation, `pytest`, `ruff`, pre-commit, issue templates. Copies these ADRs and specs into `docs/`.
- **G1 Post-update re-test** (main session, after 2026-11-09 18:00 CET): confirms or updates every **[verify]** item in spec 01. Also runs the analysis tests T-Q3-cache (offline checks T-Q3a-h), T-MEM. Results go into spec 01/05 as a doc-only PR in P0 or S1.

## Slices

| # | Slice | Owns | Depends on | Done when |
|---|---|---|---|---|
| S1 | Models, fixtures generator, API client | `models.py`, `api.py`, `exceptions.py`, `tests/fixtures/*`, `test_api.py`, `test_models.py` | P0, G1 | Test cases 1-12 pass; whitelist test present |
| S2 | Token parsing and config flow (user, site, communities, reauth, reconfigure, options), translations | `config_flow.py`, `token.py`, `strings.json`, `translations/*`, `test_config_flow.py` | S1 | Cases 31-36 pass |
| S3 | Validation rules | `validation.py`, `test_validation.py` | S1 | Cases 13-15 pass |
| S4 | Statistics writer (ids, metadata, series building, sums, stored-row rule) | `statistics.py`, `test_statistics.py` | S1 (models only) | Cases 16-21 pass |
| S5 | Coordinator, scheduler, store, services | `coordinator.py`, `importer.py`, `__init__.py`, `services.yaml`, `test_coordinator.py`, `test_init.py` | S2, S3, S4 | Cases 22-30, 40-41 pass |
| S6 | Repairs, diagnostic sensors, diagnostics and redaction | `repairs.py`, `sensor.py`, `diagnostics.py`, `redact.py`, related tests | S5 (hooks), S1 | Cases 37-39 pass |
| S7 | First run on the development box | none in the repo (private notes) | S6 | Installed, initial import done, private acceptance checks recorded, issues filed for findings |
| S8 | Docs and release 0.1.0 | `README.md`, `docs/*`, `CHANGELOG.md` | S7 | Tagged release, installable by link |

`redact.py` is needed by S1 for log redaction: S1 creates it with the `redact_text()` helper only; S6 extends it. That is the one shared file; S6 starts after S1 is merged.

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
