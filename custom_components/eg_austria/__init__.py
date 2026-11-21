"""EG Austria energy-community data for Home Assistant (unofficial, read-only).

S2 adds the config flow only; setup does nothing yet. The coordinator,
scheduler and services follow in slice S5 (see docs/spec/08-slices.md).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an entry (placeholder until S5 adds the coordinator)."""

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an entry."""

    return True
