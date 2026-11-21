# 07: Test plan

Framework: `pytest`, `pytest-asyncio`, `pytest-homeassistant-custom-component` pinned to the targeted Home Assistant version, an in-repo session double `FakePortal` for HTTP (`tests/conftest.py`: queued responses, recorded calls, only a `get` method; S1 found `aioresponses` 0.7.9 incompatible with the aiohttp 3.14 that Home Assistant 2026.10 pins), `FakeClock` (injected `monotonic`/`sleep`/`utcnow`) or `freezegun` (via the HA fixture) for time. CI also runs `hassfest` and the store validation action. Coverage target: 90 % for `api.py`, `models.py`, `validation.py`, `statistics.py`, `coordinator.py`.

## Fixture rules

- **Synthetic only.** No captured portal response goes into the repository, not even redacted. Fixtures are produced by a generator in `tests/fixtures/generate.py` and committed as JSON (one compact line per point or entry), so diffs are reviewable. `tests/test_fixtures.py` fails when a committed file differs from the generator output; regenerate with `python3 tests/fixtures/generate.py`.
- Synthetic ids: member `9000001`, location `5001`, metering points `1001` (consumption) and `1002` (generation), communities `2001` (`Example BEG`, BEG type) and `2002` (`Example EEG`, regional EEG type). Metering point numbers `AT0000000000000000000000000001001`-style (synthetic, valid length). Email `member@example.invalid`.
- Tokens: JWTs signed with a test key generated in the test, `exp` relative to the frozen time; personal tokens built in the test as `ecapi_` + random lowercase hex. Never a real token copied from anywhere.
- Shape follows the post-update fingerprint taken in G1 (build `20d1c18`, spec 01); keys not used by phase 1 are included with null or zero values so the parser sees realistic noise.

## Generator

`generate.py` builds `analysis-data` responses for a date range and community scope from a deterministic model:

- Generation: half-sine between 07:00 and 17:00 local, peak 3 kWh/h, scaled by a per-day factor from a seeded RNG.
- Consumption: 0.4 kWh/h base plus evening bump.
- Community split per hour: `toCommunity = generation × f_c`, `fromCommunity = consumption × g_c` with fixed synthetic shares per community; residual = rest. Site response = sum over communities.
- Points newest first; `date` as `DD.MM.YY HH:MM`; `dateTimeFrom` UTC `…T23:00:00.000Z`; `dateTimeFromLocal` as local wall time with a `Z` suffix (as the portal sends it); zero values as JSON int.
- Totals computed from points; `*Complete` equal to base on complete days; community `*Faulty` true on community-incomplete days; meter `*Faulty` true with `0` values on meter-missing days.
- Single-community responses repeat the meter and residual values; only community fields differ.
- Knobs: list of community-incomplete days (community fields `0`, flag set), meter-missing days (`0` + meter flag), null meter hours; the bad fixtures are mutations of a normal day (negative value, tenfold day, all-zero day, extra unknown key, missing required key, renamed key, string instead of number, ...). Not built in S1: a "partial hour" knob (no portal case known) and the optional `--csv` output.
- Dates (all synthetic): normal week 2026-06-01..07, tail month 2026-05, autumn DST 2026-10-25, spring DST 2026-03-29, single day 2026-06-03.

Committed scenarios:

| Fixture | Content |
|---|---|
| `analysis_hour_normal_{beg,eeg}.json` | 7 days, all complete |
| `analysis_hour_tail_incomplete.json` | 31 days, last 14 days community-incomplete, last day meter-missing (`0` + flag) |
| `analysis_hour_dst_autumn.json`, `..._spring.json`, `analysis_day_dst_autumn.json` | Autumn day as the portal sends it (24 points, first local 02:00 missing, local 01:00 flagged) plus its `Day` value; spring day with 23 points |
| `analysis_empty_day.json` | Empty lists, null totals (as in 1140/20) |
| `analysis_hour_null_hours.json` | One day, two hours with null meter values |
| `analysis_hour_noise.json` | Unknown top-level and point keys, an unparsable `date` |
| `analysis_bad_*.json` | One per rule, named after it: `s1_not_object`, `s2_missing_key`, `s2_renamed_key`, `s3_string_value`, `s3_bool_flag_as_number`, `s4_naive_timestamp`, `s5_not_whole_hour`, `s6_duplicate`, `s7_out_of_range`, `p1_negative`, `p2_tenfold`, `p3_community_gt_meter`, `p4_sum_{beg,eeg}` (pair), `p5_totals_mismatch`, `p6_differs_{beg,eeg}` (pair), `h1_all_zero`. S1-S4 fail in the parser; S5 onwards parse and are for S3. No S8 file: an Hour response with more points than hours always breaks S5, S6 or S7 first, so S3 builds that case in code |
| `day_completeness_*.json` | `normal`, `tail_incomplete` (match the analysis files), `future` (null flags from a date on), `bad_duplicate`, `bad_date`, `bad_numeric` (S9 / case 12) |
| `metering_point_{single,two_locations}.json` | Discovery cases |
| `maintenance_{none,active,future}.json`, `version_{a,b}.json`, `session_{one,two}_members.json` | `maintenance_none` is an empty body |
| `error_400_stale_client.json`, `error_400_other.json`, `error_401.json` | RFC 7807-like bodies with synthetic `instance`/`traceId`; the stale-client text is invented (the real one could not be reproduced), `error_401` says "Access token revoked" |

## Optional cross-check source: portal CSV export

The portal's "Energieanalyse" export (per direction, aggregation Tag) can be used for a private cross-check on the development box: daily sums of the hourly statistics should equal the export. Not used by the integration and never committed with real values. Layout as of 2026-11 (UTF-8 with BOM, `;` separator, `.` decimal point, `Datum` as `dd.mm.yy`, `Datum Von` as `dd.mm.yyyy HH:MM` local):

Bezug (consumption):

```
Datum;Datum Von;Bezug [kWh];EG Bezug [kWh];Teilnahmefaktor [%];Von EG [kWh];Anteil von EG [%];Ausgaben;Mix Sonne [%];Mix Wind [%];Mix Wasser [%];Mix Andere [%]
01.06.26;01.06.2026 00:00;9.500;4.750;50.000;3.100;32.632;0.370;60.000;20.000;15.000;5.000
02.06.26;02.06.2026 00:00;8.200;4.100;50.000;0.000;;0.000;;;;
```

Einspeisung (feed-in):

```
Datum;Datum Von;Einspeisung [kWh];EG Einspeisung [kWh];Teilnahmefaktor [%];An EG [kWh];Anteil EG [%];Einnahmen
01.06.26;01.06.2026 00:00;20.000;10.000;50.000;6.000;30.000;0.600
02.06.26;02.06.2026 00:00;18.000;9.000;50.000;0.000;;0.000
```

Values are synthetic. The second row of each file shows the "no community data" pattern: community kWh `0.000` with an empty share. The cross-check must treat such a day as unknown, like the integration (ADR 0005), not as a mismatch. Mapping (inferred): `Bezug` = `consumption`, `EG Bezug` = `consumptionByFactor`, `Von EG` = `consumptionFromCommunity`; same pattern for feed-in. The export has no Zero column.

A fixture generator option (`--csv`) may write matching synthetic CSVs so the cross-check script itself can be unit-tested.

## Test cases

### API client (`test_api.py`)

1. Builds URLs with repeated array keys; exact query for a known call.
2. Sends `Authorization: Bearer` for a personal token and the cookie header for a session JWT, never both; ignores `Set-Cookie` (second call still sends the original token).
3. Whitelist: no public method can issue a non-GET; `_get` rejects an unlisted template.
4. Error mapping for 400 stale client, 400 other, 401, 403, 404, 429 with `Retry-After`, 500, 503, timeout, HTML body, invalid JSON.
5. One retry on timeout and 503, none on 4xx.
6. Request spacing ≥ 2 s (time patched).
7. Hour range > 31 days raises before any request.
8. Logs never contain the token, member number or ids (caplog scan).
8a. Token module (`test_token_api.py`, ADR 0007): whitelist is exactly `POST portal-session/token` and `DELETE portal-session/token/{id}`; only `config_flow.py` imports `token_api` (source scan of the package); create sends one POST with the cookie (no Bearer), JSON `{name, expiresAt}` and no retry on timeout or 503; response validation (missing or malformed `token`, bool or bad `id`, `expiresAt` missing, naive or in the past, `revokedAt` set, non-JSON, list body) → `SchemaError`/`ApiChangedError`; status mapping (401/403/404/422/500); revoke sends one DELETE to the id path, 200/204/404 succeed, bad ids raise before sending; the created token never appears in logs or `repr`. Fixtures are built in the test (`ecapi_` + random hex), never copied.

### Models (`test_models.py`)

9. Parses every normal fixture; sorts points ascending; converts ints to floats.
10. `date` string is never parsed (fixture with an unparsable `date` passes).
11. Extra keys go to `extra`; missing required key raises `SchemaError` with the path.
12. `day-completeness` booleans vs `analysis-data` numeric `*Complete` are not confused.

### Validation (`test_validation.py`)

13. One test per rule S1-S9, P1-P6, H1-H2: failing fixture fails with that id, normal fixture passes.
14. DST fixtures pass S5/S8 (autumn `Hour` with 24 points, autumn `Day`, spring with 23 points; a filled 25th autumn hour still passes, a 24th spring hour fails); P4 skips the gap hour. Filling the gap from the `Day` residual is the statistics writer's job and tested in S4 (spec 04).
15. P4-P6 (and any other P rule) warn but don't block while their `RULE_BLOCKING` entry is off; block when on.

### Statistics (`test_statistics.py`, with recorder fixture)

Recorder tests use `recorder_mock` with a module-level `mock_recorder_before_hass` override, because the shared autouse `auto_enable_custom_integrations` fixture requests `hass` first. Further cases covered: DST autumn fill and spring 23-hour day (case 14 for the writer side), base sum after a gap longer than the 400-day lookback, display-name update on rename, no import when nothing is known.

16. Ids and metadata exactly as spec 04 for a two-community entry; no statistics for a missing direction.
17. First import: sums start at 0 and are cumulative; null hours produce no row.
18. Community quantities on incomplete days produce no row; meter quantities do.
19. Re-import with changed values for an old day rewrites that day and all later sums; rows before the window untouched.
20. Known → unknown: stored rows are kept and sums stay consistent (hourly differences equal states).
21. Window ending before the newest stored hour is not possible: the rewrite runs to the newest known **or stored** hour, so stored rows after the newest known hour are kept and re-summed (spec 04).

### Coordinator (`test_coordinator.py`)

S5 tests use `FakeClient` (`tests/conftest.py`): the client's public methods, with `analysis-data` and `day-completeness` built by the fixture generator for whatever range is asked, plus knobs for incomplete and meter-missing days, errors per method, response mutation and a hook before each request. Time is frozen with `freezer` in `Europe/Vienna`; statistics go to the test recorder (`recorder_mock`). An autouse fixture sets `sqlalchemy.engine` back to WARNING: the test plugin sets it to INFO, which makes runs with thousands of statistic rows several times slower.

22. Daily window from completeness: earliest incomplete in 120 days; ≥ 62 days; capped.
23. Long run on the first daily run after the 3rd, once per month.
24. Startup catch-up when last success > 26 h; not when recent.
25. Maintenance active → skipped and rescheduled after end + 30 min; future window → normal run.
26. Version change → flagged, run continues.
27. Chunking at ≤ 31 days, month-aligned; request count for a 62-day window and two communities.
28. Error paths: auth → `ConfigEntryAuthFailed`; schema → no writes at all; plausibility → partial writes; unavailable 3 days → repair.
29. Expired `exp` → no HTTP request, reauth.
30. Concurrent triggers → one run.
30a. Communities with different activations (issue #16; BEG from 2026-01-01, EEG from 2026-04-16, dates from 2026 on because of the privacy denylist): `site_info()` keeps a merged range per community; `plan_chunks()` cuts at `begin` and `end` + 1, drops chunks without an active selected community and sets `all_communities` per chunk (false where an unselected community is active); `request_count(plan)`; an initial run over both starts is `ok` and requests the EEG only from its `begin`; site statistics are written before the EEG starts (EEG first in entry order: meter and residual from the first active community), the site community sum before the start equals the BEG value and after it BEG + EEG, community + residual = meter on both sides (P4). `FakeClient` answers a request outside the community's activation with the reduced body (no `totalConsumption`) and subtracts only active communities from the residual, so the old planning fails these tests with `api_changed`.

### Config flow (`test_config_flow.py`)

The flow uses the shared portal session and never closes it (issue #17); `test_api.py` checks that `get_portal_session()` returns the same session until Home Assistant has closed it.

31. Token parsing variants: personal token (with whitespace, quotes), JWT raw, `portal.jwtToken=...; Path=/`, quotes, whitespace; kind detection; a pasted personal token requires a future `token_expires`.
31a. Token creation (issue #18): a session cookie leads to `create_token` after the online check; `create_token_confirm` sends exactly one create with the cookie and stores only the returned token, kind `personal`, `token_exp` = `expiresAt` and `token_id` (the cookie appears nowhere in the entry); no `token_expiry` step; each create error shows `create_failed` with its error and continues with the cookie (no second create); `use_session` keeps the cookie; the 7-day notice only with the cookie kept. Reauth with a cookie creates a token the same way. A pasted `ecapi_` token still works and stores no `token_id`.
31b. Revoke (issue #18): the reconfigure menu only appears with a stored `token_id`; `revoke_token` refuses a personal token, checks the member, sends exactly one revoke with the stored id, maps errors without changing the entry, and then offers `create_token` (new token stored, or the cookie kept with `token_id` removed).
32. Errors: bad format, expired, invalid auth, cannot connect, api changed.
33. Single location auto-selected; two locations → select; metering point multi-select with full number, facility name, `sourceOfProduction` and part factors in the labels (#19); communities multi-select labelled per community with the default slug, `edit_slugs` → `community_slugs` (#20); slug validation and uniqueness.
34. Duplicate entry aborts.
35. Reauth success updates token, kind and expiry and clears `token_expiring` (session → personal switch included); wrong account error (member missing, or 403); a revoked token (401) in the reauth form shows `invalid_auth`. That a 401 during a run starts reauth (`ConfigEntryAuthFailed`) is tested with the coordinator in S5 (case 22-30 range).
36. Reconfigure adds a community (multi-select, optional slug step), existing slugs read-only and an entry made before S7b keeps its data and slugs; a changed community id is remapped to its old slug, an unmapped one is retired; the location can be remapped; a new token is checked like reauth.

### Repairs, entities, diagnostics (`test_repairs.py`, `test_sensor.py`, `test_diagnostics.py`)

S6 tests set up a real entry with the client factory patched to `FakeClient` (`conftest.async_setup_fake_entry`) and patch out the statistics write where only validation matters.

37. Each issue created and cleared on its condition: `token_expiring` at setup for both kinds, by the timer when the period starts, not again after a new token, timer cancelled on unload; auth failure → core reauth and none of the integration's issues; `api_changed` (stays during an unreachable run, cleared by a valid run) and its `stale_client` text; `implausible_data` cleared by a clean run and by `release_held` once nothing is held; `portal_unreachable` on the third day, cleared by a success; maintenance raises nothing; removing the entry deletes the issues.
38. Sensor values before and after a run (and after a failed run); diagnostic category, one service device, `portal_version` disabled by default, its value when enabled.
39. Diagnostics redaction: a session JWT carrying the email, a created token with its `token_id`, fixtures with every sensitive key, unknown keys and a maintenance title with sensitive values, an error message that slipped past the client's redaction; assert none of the synthetic sensitive strings (token and its parts, email, member number, ids, metering point numbers, names, address) appears anywhere in the output or in the logs. Also: `redact_tree()`, `schema_fingerprint()` (and the client recording it), `jwt_issued_at()`, statistics first/last hour, an entry that is not loaded.

### Integration (`test_init.py`)

40. Setup → initial import → statistics present → unload cleanly (the portal session is not closed, issue #17) → reload.
41. Services `refetch` (range, defaults, refusals, busy) and `release_held` (H1 hold, release, not held again). Also: expired token at setup → reauth; setup check (ADR 0013): portal down, 5xx, 429 or timeout → setup retry, 401/403 → reauth, non-JSON answer in an announced window → setup retry, changed API → loaded; an unreachable portal logged once at info on loss and once on recovery; unload during a run stops it without writes; translated statistic names.

## Private acceptance on the development box (not in CI, not in the repo)

After slice S7 is installed with a real token: run once, then compare monthly sums of the statistics with figures the member already holds (surplus-buyer credit notes, local inverter totals, invoice kWh). The comparison and its numbers stay in private notes.
