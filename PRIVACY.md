# Privacy

What the EG Austria integration reads, stores and shares. It runs entirely in
your Home Assistant; there is no server of the maintainer in between.

## What is read

Only from the EG Austria member portal (`https://portal.eg-austria.at/api`),
with your token, and only with GET requests to a fixed list of endpoints
([ADR 0002](docs/adr/0002-read-only-get-whitelist.md)):

- your portal session (which members your login can act for),
- your metering points, locations and community activations,
- the day completeness flags and the hourly analysis data (kWh),
- the announced maintenance windows and the portal version.

The only other calls are creating and revoking the integration's own access
token. They happen only in the setup wizard, after you agree, and once each
([ADR 0007](docs/adr/0007-token-handling.md)). Requests identify the
integration by name, version and repository URL only.

## What is stored

In your Home Assistant installation only:

- **Config entry:** the token, its kind and expiry, the id of a token the
  wizard created, the member number, location id, metering point ids,
  community ids, slugs and names, and the daily run time; plus the options.
  Home Assistant keeps config entries unencrypted in its config storage, like
  other integrations' credentials.
- **The entry's store:** dates and counters of past runs, held and released
  days. No energy values.
- **Statistics:** the hourly kWh values, in the recorder database.

Not stored: the session cookie used to create a token, your email address and
other token claims, member display names, addresses and metering point
numbers (shown only in the setup wizard).

## What is shared

Nothing, unless you share it. Tokens are never logged. Logged errors are
redacted. The diagnostics download removes tokens, ids, names, addresses,
metering point numbers and contains no energy values; check it before you
attach it to an issue anyway.

When you report an issue, never post tokens, cookies, member or metering
point numbers, names, addresses or real values. See
[CONTRIBUTING](CONTRIBUTING.md).

## Removal

See [Removal](README.md#removal): deleting the entry deletes the token and the
store; statistics stay until you delete them.
