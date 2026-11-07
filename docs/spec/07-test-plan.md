# 07: Test plan

Framework: `pytest`, `pytest-asyncio`, `pytest-homeassistant-custom-component` pinned to the targeted Home Assistant version, `aioresponses` for HTTP, `freezegun` (via the HA fixture) for time. CI also runs `hassfest` and the store validation action. Coverage target: 90 % for `api.py`, `models.py`, `validation.py`, `statistics.py`, `coordinator.py`.

## Fixture rules

- **Synthetic only.** No captured portal response goes into the repository, not even redacted. Fixtures are produced by a generator in `tests/fixtures/generate.py` and committed as JSON, so diffs are reviewable.
- Synthetic ids: member `9000001`, location `5001`, metering points `1001` (consumption) and `1002` (generation), communities `2001` (`Example BEG`, BEG type) and `2002` (`Example EEG`, regional EEG type). Metering point numbers `AT0000000000000000000000000001001`-style (synthetic, valid length). Email `member@example.invalid`.
- Tokens: JWTs signed with a test key generated in the test, `exp` relative to the frozen time. Never a real token shape copied from anywhere.
- Shape follows the post-update fingerprint **[verify]**; keys not used by phase 1 are included with null or zero values so the parser sees realistic noise.

## Generator

`generate.py` builds `analysis-data` responses for a date range and community scope from a deterministic model:

- Generation: half-sine between 07:00 and 17:00 local, peak 3 kWh/h, scaled by a per-day factor from a seeded RNG.
- Consumption: 0.4 kWh/h base plus evening bump.
- Community split per hour: `toCommunity = generation × f_c`, `fromCommunity = consumption × g_c` with fixed synthetic shares per community; residual = rest. Site response = sum over communities.
- Points newest first; `date` as a German locale string; `dateTimeFrom` UTC `Z`; `dateTimeFromLocal` with offset.
- Totals computed from points; `*Complete` equal to base on complete days, lower on incomplete days; `*Faulty` true on incomplete days.
- Knobs: list of incomplete days (community fields null), null meter hours, a partial hour, negative value, tenfold day, all-zero day, extra unknown key, missing required key, renamed key, string instead of number.

Committed scenarios:

| Fixture | Content |
|---|---|
| `analysis_hour_normal_{site,beg,eeg}.json` | 7 days, all complete |
| `analysis_hour_tail_incomplete.json` | 31 days, last 14 days community-incomplete, last 2 days empty |
| `analysis_hour_dst_autumn.json`, `..._spring.json` | Days with 25 and 23 local hours |
| `analysis_empty_day.json` | Empty lists, null totals (as in 1140/20) |
| `analysis_bad_*.json` | One per shape and plausibility rule |
| `day_completeness_*.json` | Matching completeness lists |
| `metering_point_{single,two_locations}.json` | Discovery cases |
| `maintenance_{none,active,future}.json`, `version_{a,b}.json` | |
| `error_400_stale_client.json`, `error_400_other.json`, `error_401.json` | RFC 7807-like bodies with synthetic `instance`/`traceId` |

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
2. Sends the cookie header; ignores `Set-Cookie` (second call still sends the original token).
3. Whitelist: no public method can issue a non-GET; `_get` rejects an unlisted template.
4. Error mapping for 400 stale client, 400 other, 401, 403, 404, 429 with `Retry-After`, 500, 503, timeout, HTML body, invalid JSON.
5. One retry on timeout and 503, none on 4xx.
6. Request spacing ≥ 2 s (time patched).
7. Hour range > 31 days raises before any request.
8. Logs never contain the token, member number or ids (caplog scan).

### Models (`test_models.py`)

9. Parses every normal fixture; sorts points ascending; converts ints to floats.
10. `date` string is never parsed (fixture with an unparsable `date` passes).
11. Extra keys go to `extra`; missing required key raises `SchemaError` with the path.
12. `day-completeness` booleans vs `analysis-data` numeric `*Complete` are not confused.

### Validation (`test_validation.py`)

13. One test per rule S1-S9, P1-P6, H1-H2: failing fixture fails with that id, normal fixture passes.
14. DST fixtures pass S5/S8.
15. P4-P6 warn but don't block while their constant is off; block when on.

### Statistics (`test_statistics.py`, with recorder fixture)

16. Ids and metadata exactly as spec 04 for a two-community entry; no statistics for a missing direction.
17. First import: sums start at 0 and are cumulative; null hours produce no row.
18. Community quantities on incomplete days produce no row; meter quantities do.
19. Re-import with changed values for an old day rewrites that day and all later sums; rows before the window untouched.
20. Known → unknown: stored rows are kept and sums stay consistent (hourly differences equal states).
21. Window ending before the newest stored hour is not possible (assert in importer).

### Coordinator (`test_coordinator.py`)

22. Daily window from completeness: earliest incomplete in 120 days; ≥ 62 days; capped.
23. Long run on the first daily run after the 3rd, once per month.
24. Startup catch-up when last success > 26 h; not when recent.
25. Maintenance active → skipped and rescheduled after end + 30 min; future window → normal run.
26. Version change → flagged, run continues.
27. Chunking at ≤ 31 days, month-aligned; request count for a 62-day window and two communities.
28. Error paths: auth → `ConfigEntryAuthFailed`; schema → no writes at all; plausibility → partial writes; unavailable 3 days → repair.
29. Expired `exp` → no HTTP request, reauth.
30. Concurrent triggers → one run.

### Config flow (`test_config_flow.py`)

31. Token parsing variants (raw, `portal.jwtToken=...; Path=/`, quotes, whitespace).
32. Errors: bad format, expired, invalid auth, cannot connect, api changed.
33. Single location auto-selected; two locations → select; metering point multi-select; slug validation and uniqueness.
34. Duplicate entry aborts.
35. Reauth success updates token and clears `token_expiring`; wrong account error.
36. Reconfigure adds a community, existing slugs read-only.

### Repairs, entities, diagnostics (`test_repairs.py`, `test_sensor.py`, `test_diagnostics.py`)

37. Each issue created and cleared on its condition.
38. Sensor values after a run; `portal_version` disabled by default.
39. Diagnostics redaction: fixture with every sensitive key and pattern; assert none of the synthetic sensitive strings (token, email, member number, ids, metering point numbers, names) appears anywhere in the output.

### Integration (`test_init.py`)

40. Setup → initial import → statistics present → unload cleanly → reload.
41. Services `refetch` and `release_held`.

## Private acceptance on the development box (not in CI, not in the repo)

After slice S7 is installed with a real token: run once, then compare monthly sums of the statistics with figures the member already holds (surplus-buyer credit notes, local inverter totals, invoice kWh). The comparison and its numbers stay in private notes.
