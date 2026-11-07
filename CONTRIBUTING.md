# Contributing

Thanks for helping. Please read this before opening an issue or pull request.

## The privacy rule

This is a public repository about a service that holds personal energy and
billing data. **Nothing private may ever be committed, attached or pasted**:

- no portal tokens or cookies (JWTs), not even expired ones;
- no member numbers, location, community or metering point ids, and no real
  metering point numbers (`AT…`);
- no names, addresses, emails (other than GitHub noreply or `example.*`),
  or the names of the communities you belong to;
- no real kWh or EUR values, no captured API responses (not even redacted),
  no HAR files, invoices, account statements or portal CSV exports.

Test data is **synthetic only**, produced by `tests/fixtures/generate.py`
(see [docs/spec/07-test-plan.md](docs/spec/07-test-plan.md)). Use the synthetic
ids from the test plan (member `9000001`, metering points `1001`/`1002`,
`member@example.invalid`, metering point numbers starting `AT000000…`).

### Privacy guard

`scripts/privacy_guard.py` runs as a pre-commit hook (files and commit
message) and in CI. It fails on JWT-like strings, non-synthetic metering point
numbers, non-noreply/example emails, forbidden file types, and on entries of an
optional **private denylist kept outside the repository**:

```bash
export EG_PRIVACY_DENYLIST=/path/outside/the/repo/denylist.txt
python3 scripts/privacy_guard.py --all
```

One entry per line (your member number, name, street, community names, ...);
`#` starts a comment; matching ignores case and umlaut spelling. The guard
never prints denylist entries or matching lines. In CI the list can be provided
via the repository secret `PRIVACY_DENYLIST`.

## Workflow

- Work on a branch and open a pull request against `main`.
- One slice (see `docs/spec/08-slices.md`) per pull request, with tests.
- `pytest`, `ruff check`, `ruff format --check` and the privacy guard must pass.
- Read-only: the API client may only issue GET requests to whitelisted
  endpoints (ADR 0002). Pull requests adding anything else will be declined.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements_dev.txt
pre-commit install
```
