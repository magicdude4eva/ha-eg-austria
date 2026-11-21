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

## AI attribution

This project is written with the help of AI (Claude, by Anthropic), and it is
**not "AI slop"**:

- **Human-led:** a human maintainer sets the design, reviews every change and
  decides what gets merged. Architecture decisions and specifications are written
  down first ([`docs/adr`](docs/adr/README.md), [`docs/spec`](docs/spec/README.md));
  the code follows them in small, reviewed pull requests.
- **Tested:** every change runs through automated tests with synthetic data,
  linting, Home Assistant's `hassfest`, store validation and a privacy guard in CI.
  Each release also runs on the maintainer's own Home Assistant installation
  against a real portal account first.
- **Actively maintained:** it is built and used for the maintainer's own setup
  (one site with generation and consumption, a citizen energy community (BEG), a
  regional renewable energy community (EEG) and the Zero tariff option), so it is
  tested against that setup first. Other setups are supported as far as people
  with those setups help.
- **Community:** feedback, bug reports, fixes and enhancements from other users
  and contributors are welcome; see [CONTRIBUTING](CONTRIBUTING.md).

Commits and pull requests made with AI help end with:
"🤖 Generated / Supported with ❤️ Claudia, the better AI companion".
