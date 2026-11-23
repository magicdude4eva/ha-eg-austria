# 05: Validation rules

File: `validation.py`, pure functions over parsed models; no Home Assistant imports. Each rule has an id used in logs, diagnostics and tests.

## Stage 1: shape (per response)

Any failure → `SchemaError` → the whole run writes nothing, repair `api_changed` (ADR 0006).

| Id | Rule |
|---|---|
| S1 | Response is a JSON object (list for `day-completeness`, `metering-point`; list or empty body for `maintenance`). |
| S2 | Required keys present (spec 01; totals and day flags of a direction without points may be missing, issue #78). |
| S3 | Energy fields are number or null; flags are bool; `dateTimeFrom` is a string. |
| S4 | `dateTimeFrom` parses as ISO 8601 with offset or `Z`; converted to UTC. |
| S5 | For `Hour`: every timestamp is on a whole UTC hour. For `Day`: every timestamp is a local midnight. |
| S6 | No duplicate timestamps within a point list. |
| S7 | All timestamps lie in the requested local date range (start 00:00 to end 24:00 local). |
| S8 | Point count per list ≤ hours in the range (23/24/25 per local day around DST; the autumn day actually returns 24, spec 01). |
| S9 | `day-completeness` dates are valid, unique, within range. |

S1-S4 and the date part of S9 are raised by the parser (`SchemaError.rule`, spec 01); `schema_failure()` turns that into a result. S5-S8 run in `validate_chunk()`, the rest of S9 (unique, in range) in `validate_completeness()`. A day missing from `day-completeness` is not an S9 failure (it counts as not complete). When S5-S8 fail for a chunk, stage 2 is not run for it.

Extra unknown keys are not a failure; their names (never values) are collected in `schema_extra` for diagnostics. A change in the set of extra keys between runs is logged once at info level.

## Stage 2: plausibility (per chunk and statistic)

Failure → that statistic's values for that chunk are not written (stored rows stay), repair `implausible_data` naming the rule and statistic key. Other statistics and chunks still write.

| Id | Rule | Tolerance |
|---|---|---|
| P1 | No negative value. | 0 |
| P2 | No value > `max_hourly_kwh` (option, default 50). Catches unit or tenfold errors (1326/3*). For a `Day` response (autumn DST day) the bound is `max_hourly_kwh` × the day's hours. | — |
| P3 | `fromCommunity ≤ consumption` and `toCommunity ≤ generation` per hour, when both known. | 0.001 kWh |
| P4 | `Σ_c fromCommunity_c + inExcess ≈ consumption`, `Σ_c toCommunity_c + inExcess ≈ generation` per hour (sum over the selected communities' responses). Confirmed by G1 (T-Q3b: exact in every hour, complete and incomplete days). The residual is global, so the equality needs every community the metering points are active in; when the entry selects only some (`all_communities=False`), P4 only checks `Σ_c + inExcess ≤ meter`. | 0.002 kWh or 0.5 % |
| P5 | Point sums ≈ `total*` of the same response for the six mapped fields, where totals are not null. Confirmed by G1 (T-Q3d: exact). | 0.01 kWh or 0.1 % |
| P6 | `consumption`, `generation`, `consumptionInExcess`, `generationInExcess` are equal per hour in every per-community response of a chunk. Confirmed by G1 (T-Q3c). | 0.002 kWh |

P1 to P6 are blocking (G1 confirmed P4 to P6 on build `20d1c18`). `RULE_BLOCKING` in `validation.py` can turn one back into a warning if a portal change breaks an identity; `validate_chunk(blocking=...)` overrides it per call (tests). P4 and P6 run on the hours present in every response, so the autumn DST gap hour, which has no point, is skipped. A known value in one response and `null` in another fails P6. A meter value (`consumption`, `generation`) flagged faulty is unknown and left out of P6, and P4 takes the meter from the first response whose meter value isn't faulty (issue #78: one community reported a faulty 0 while the other had the reading); the site meter statistic is read the same way (spec 04). Tolerances pass when the difference is within the absolute or the relative bound.

Statistics blocked by a failure: P1, P2 and P5 the statistics fed by the failing field (`community` feeds the community statistic and the site sum). For identities between fields, every statistic in the identity: P3 the meter and both community statistics of that direction; P4 the whole direction (meter, residual, site sum and every community); P6 the site meter or residual statistic.

## Stage 3: history guard (per day and statistic)

Compares the new values with the stored statistics for the same day. The caller passes the stored day totals per `(statistic key, local day)`; the new totals are the sums of the known hourly values (`day_totals()`). Only days that `day-completeness` reports complete for the statistic's direction are compared (other days are provisional). The autumn DST gap hour is not in the new total; its effect is far below the H2 thresholds.

| Id | Rule | Action |
|---|---|---|
| H1 | A day that is stored with a non-zero total and is complete now arrives with every hour `0` (retroactive zeroing, topic 592). | Hold the day (stored rows stay), add to `held_days`, repair `implausible_data`. Released automatically when a later run returns non-zero values, or by the `eg_austria.release_held` service. |
| H2 | A day's total changes by more than 50 % and more than 1 kWh compared with the stored total. | Write it (corrections are normal, 171/55*), record in diagnostics `corrections` (date, statistic key, relative change only; `None` when the stored total was 0). Not a finding, no repair. |

## Outputs

`ValidationResult` per check, combined per run with `merge()`:

- `findings`: `Finding(rule, severity, message, statistic, chunk, day)`; `failures` (blocking) and `warnings`. One finding per rule, statistic and chunk, with the first hour and a count. `statistic` is the full key (`<site>_<quantity>`, `<site>_<community>_<quantity>`, spec 04) or `None` for shape rules. Messages name rules, fields, keys and UTC hours only: no energy values; ids passed as `sensitive` are redacted.
- `schema_failed` (a shape rule failed: write nothing), `ok` (nothing blocks), `is_blocked(statistic, day)`, `blocked_statistics()`.
- `schema_extra`, `held_days` (`{(statistic, day)}`; `held_by_statistic()` gives the spec 03 store layout), `corrections`.

Entry points: `schema_failure(err)`, `validate_chunk(start, end, {community_slug: AnalysisData}, site=, aggregation=, all_communities=, max_hourly_kwh=, blocking=)`, `validate_completeness(entries, start, end)`, `check_history(responses, completeness, stored, site=, released=)`. `released` holds days cleared by `release_held`; they are not held again. The coordinator turns the result into repairs (spec 06) and the run summary.
