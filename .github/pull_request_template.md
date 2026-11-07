## What and why

<!-- Slice id (S1..S8) if any, and a short description. -->

## Checks

- [ ] Tests added or updated; `pytest` and `ruff` pass locally
- [ ] Privacy: no tokens, ids, names, addresses, emails or real kWh/EUR values; fixtures are synthetic (`scripts/privacy_guard.py --all` is clean)
- [ ] Read-only: no new non-GET request (ADR 0002)
