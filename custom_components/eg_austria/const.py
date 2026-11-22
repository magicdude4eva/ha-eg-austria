"""Constants for the EG Austria integration."""

from typing import Final

DOMAIN: Final = "eg_austria"
VERSION: Final = "0.1.4"  # keep equal to manifest.json "version"

# Config entry data keys (spec 02 "Create entry").
CONF_TOKEN: Final = "token"  # noqa: S105 - key name
CONF_TOKEN_KIND: Final = "token_kind"  # noqa: S105 - key name
CONF_TOKEN_EXP: Final = "token_exp"  # noqa: S105 - key name
# Id of a token the config flow created (ADR 0007); absent otherwise.
CONF_TOKEN_ID: Final = "token_id"  # noqa: S105 - key name
CONF_MEMBER_NUMBER: Final = "member_number"
CONF_LOCATION_ID: Final = "location_id"
CONF_MP_IDS: Final = "mp_ids"
CONF_COMMUNITIES: Final = "communities"  # [{id, slug, name}]
CONF_RETIRED_SLUGS: Final = "retired_slugs"  # slugs never to be reused
CONF_SITE_SLUG: Final = "site_slug"
CONF_RUN_MINUTE: Final = "run_minute"

# Options (spec 02 "Options").
CONF_MAX_HOURLY_KWH: Final = "max_hourly_kwh"
CONF_DAILY_WINDOW_DAYS: Final = "daily_window_days"
CONF_LONG_REFETCH_MONTHS: Final = "long_refetch_months"
CONF_IMPORT_HISTORY: Final = "import_history"
DEFAULT_MAX_HOURLY_KWH: Final = 50
DEFAULT_DAILY_WINDOW_DAYS: Final = 62
DEFAULT_LONG_REFETCH_MONTHS: Final = 13
DEFAULT_IMPORT_HISTORY: Final = True

# Comparison sources (options, issue #50): the user's own energy statistics.
CONF_SOURCE_FEED_IN: Final = "source_feed_in"
CONF_SOURCE_GRID_CONSUMPTION: Final = "source_grid_consumption"
CONF_SOURCE_PV: Final = "source_pv"
SOURCE_KEYS: Final = (CONF_SOURCE_FEED_IN, CONF_SOURCE_GRID_CONSUMPTION, CONF_SOURCE_PV)

# Money (options, ADR 0015): price source dicts, see prices.py.
CONF_SURPLUS_PRICE: Final = "surplus_price"

# Personal token: default stated lifetime (ADR 0007).
PERSONAL_TOKEN_DEFAULT_DAYS: Final = 365
# Name of a token the config flow creates (ADR 0007).
CREATED_TOKEN_NAME: Final = "Home Assistant"  # noqa: S105 - not a secret
