# EG Austria for Home Assistant (unofficial)

A **read-only**, community-built Home Assistant custom integration that imports
hourly energy-community data (kWh) from the EG Austria member portal into
Home Assistant long-term statistics.

> **Not affiliated with, endorsed by or supported by EG Austria.** It uses the
> portal's undocumented web API, which may change or break at any time. It only
> reads data (HTTP GET); it never changes anything in your portal account.

## Status

Early development, **not usable yet**. Work follows the slices in
[`docs/spec/08-slices.md`](docs/spec/08-slices.md); decisions are recorded in
[`docs/adr`](docs/adr/README.md), specifications in [`docs/spec`](docs/spec/README.md).

Planned for phase 1:

- hourly external statistics per site and per energy community (consumption,
  generation, from/to community, residual), overwritten when the portal corrects data;
- incomplete or missing data stays unknown, never zero;
- repairs for expiring tokens, portal maintenance and API changes;
- redacted diagnostics.

## Installation

Not yet. Once 0.1.0 is released it can be added to the Home Assistant
Marketplace (formerly HACS) as a custom repository by link.

## Privacy

Your portal token is stored in the Home Assistant config entry only and is
never logged. When reporting issues, never post tokens, member or metering point
numbers, names, addresses or real values. See [CONTRIBUTING](CONTRIBUTING.md).

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements_dev.txt
pre-commit install
pytest
ruff check . && ruff format --check .
python3 scripts/privacy_guard.py --all
```

## License

[MIT](LICENSE)
