# EG Austria for Home Assistant (unofficial)

A **read-only**, community-built Home Assistant custom integration that imports
hourly energy-community data (kWh) from the EG Austria member portal into
Home Assistant long-term statistics.

> **Not affiliated with, endorsed by or supported by EG Austria.** It uses the
> portal's undocumented web API, which may change or break at any time. It only
> reads data (HTTP GET); it never changes anything in your portal account.

> [!TIP]
> **Joining EG Austria?** If this integration is useful to you, you can sign up
> with my referral link: <https://eg-austria.at/?ref=L79Y5W9G> (code
> `L79Y5W9G`). It supports the maintainer and costs you nothing extra. This is a
> personal referral, not a sponsorship by EG Austria.
>
> 🇦🇹 *Neu bei EG Austria? Mit dem Empfehlungscode `L79Y5W9G` unterstützt du
> dieses Projekt.*

## Status

Early development, **not usable yet**. Work follows the slices in
[`docs/spec/08-slices.md`](docs/spec/08-slices.md); decisions are recorded in
[`docs/adr`](docs/adr/README.md), specifications in [`docs/spec`](docs/spec/README.md).

Planned for phase 1:

- hourly external statistics per site and per energy community (consumption,
  generation, from/to community, residual), overwritten when the portal corrects data;
- incomplete or missing data stays unknown, never zero;
- repairs for expiring tokens, API changes, held-back data and an unreachable
  portal (announced maintenance only postpones the run);
- diagnostic sensors and redacted diagnostics.

## Installation

Not yet. Once 0.1.0 is released it can be added to the Home Assistant
Marketplace (formerly HACS) as a custom repository by link.

After you add the integration, Home Assistant opens a **Name and assign**
dialog for the "EG Austria" service device. An area makes no sense for this
service: just choose **Skip and finish**. Home Assistant offers this dialog for
every device a new entry creates, so the integration can't turn it off.

## Repairs and diagnostics

The integration raises a repair in **Settings > System > Repairs** when it
needs you. Each one clears itself when the cause is gone.

### Token expiry

Raised 30 days before the expiry date you entered for a personal access token,
or 7 days before a session token expires. Create a new token and paste it with
**Reconfigure** on the integration page. If the portal rejects the token
earlier, Home Assistant asks you to re-authenticate instead.

### Portal changed

The portal answered in a format the integration does not know, or said the
client is out of date. Nothing is written until a run passes the checks
again, so your statistics stay correct. Please download the diagnostics of the
entry or its device (three-dot menu > Download diagnostics; tokens, ids, names
and values are removed) and attach them to an
[issue](https://github.com/magicdude4eva/ha-eg-austria/issues).

### Held data

Some values failed a plausibility check (for example a negative or a
ten-fold value) and were not written; everything else was. The next run checks
those days again. If the portal shows the values as correct, the action
`eg_austria.release_held` lets the next run write them.

### Portal unreachable

No run succeeded on three calendar days in a row. Imports resume by
themselves; the portal keeps the data, so nothing is lost while it is down.

### Diagnostic sensors

Token expiry, data complete until, last import, last run status and (disabled
by default) the portal version.

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

This project is written with the help of AI tools, and it is **not "AI slop"**:

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
