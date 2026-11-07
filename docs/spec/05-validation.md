# 05: Validation rules

File: `validation.py`, pure functions over parsed models; no Home Assistant imports. Each rule has an id used in logs, diagnostics and tests.

## Stage 1: shape (per response)

Any failure → `SchemaError` → the whole run writes nothing, repair `api_changed` (ADR 0006).

| Id | Rule |
|---|---|
| S1 | Response is a JSON object (list for `day-completeness`, `maintenance`). |
| S2 | Required keys present (spec 01). |
| S3 | Energy fields are number or null; flags are bool; `dateTimeFrom` is a string. |
| S4 | `dateTimeFrom` parses as ISO 8601 with offset or `Z`; converted to UTC. |
| S5 | For `Hour`: every timestamp is on a whole UTC hour. |
| S6 | No duplicate timestamps within a point list. |
| S7 | All timestamps lie in the requested local date range (start 00:00 to end 24:00 local). |
| S8 | Point count per list ≤ hours in the range (23/24/25 per local day around DST). |
| S9 | `day-completeness` dates are valid, unique, within range. |

Extra unknown keys are not a failure; their names (never values) are collected in `schema_extra` for diagnostics. A change in the set of extra keys between runs is logged once at info level.

## Stage 2: plausibility (per chunk and statistic)

Failure → that statistic's values for that chunk are not written (stored rows stay), repair `implausible_data` naming the rule and statistic key. Other statistics and chunks still write.

| Id | Rule | Tolerance |
|---|---|---|
| P1 | No negative value. | 0 |
| P2 | No value > `max_hourly_kwh` (option, default 50). Catches unit or tenfold errors (1326/3*). | — |
| P3 | `fromCommunity ≤ consumption` and `toCommunity ≤ generation` per hour, when both known. | 0.001 kWh |
| P4 | `fromCommunity + inExcess ≈ consumption`, `toCommunity + inExcess ≈ generation` per hour on complete days. **[test T-Q3b]**; enabled only after the test confirms the identity, otherwise diagnostic only. | 0.002 kWh or 0.5 % |
| P5 | Point sums ≈ `total*` of the same response for the six mapped fields, where totals are not null. **[test T-Q3d]** | 0.01 kWh or 0.1 % |
| P6 | Site community values ≈ sum of per-community values per hour. **[test T-Q3c]**; diagnostic until confirmed. | 0.002 kWh |

P1 to P3 are always on. P4 to P6 start as warnings in diagnostics and become blocking once the tests in the analysis confirm the identities; a constant per rule controls this.

## Stage 3: history guard (per day and statistic)

Compares the new values with the stored statistics for the same day.

| Id | Rule | Action |
|---|---|---|
| H1 | A day that is stored with a non-zero total and is complete now arrives with every hour `0` (retroactive zeroing, topic 592). | Hold the day (stored rows stay), add to `held_days`, repair `implausible_data`. Released automatically when a later run returns non-zero values, or by the `eg_austria.release_held` service. |
| H2 | A day's total changes by more than 50 % and more than 1 kWh compared with the stored total. | Write it (corrections are normal, 171/55*), record in diagnostics `corrections` (date, statistic key, relative change only). |

## Outputs

`ValidationResult` per run: lists of `(rule_id, statistic_key, chunk/day, message)` for failures and warnings, `schema_extra`, `held_days`, `corrections`. The coordinator turns it into repairs (spec 06) and the run summary.
