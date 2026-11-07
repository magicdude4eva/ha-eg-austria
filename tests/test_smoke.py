"""Smoke test: the skeleton is a loadable custom integration."""

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.eg_austria.const import DOMAIN

MANIFEST = Path(__file__).parents[1] / "custom_components" / DOMAIN / "manifest.json"


def test_manifest() -> None:
    """Manifest has the agreed basics."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["domain"] == DOMAIN
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["requirements"] == []
    assert "version" in manifest


async def test_integration_is_discovered(hass: HomeAssistant) -> None:
    """Home Assistant's loader finds the custom integration."""
    integration = await async_get_integration(hass, DOMAIN)
    assert integration.domain == DOMAIN
    assert not integration.is_built_in
