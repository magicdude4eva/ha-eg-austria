"""Diagnostic sensors (spec 06 "Diagnostic entities").

One service device per entry; every sensor is ``EntityCategory.DIAGNOSTIC``.
No energy sensors: the data lives in external statistics (ADR 0003).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .importer import RunStatus
from .repairs import token_expiry

if TYPE_CHECKING:
    from .coordinator import EgAustriaCoordinator

MANUFACTURER = "EG Austria (unofficial)"
GIT_COMMIT_SHORT = 7

# Read-only entities, updated by the coordinator; no parallel limit needed.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EgSensorDescription(SensorEntityDescription):
    """A diagnostic sensor and how to read its value."""

    value_fn: Callable[[EgAustriaCoordinator], datetime | date | str | None]


def _run_status(coordinator: EgAustriaCoordinator) -> str | None:
    summary = coordinator.data
    return summary.status.value if summary is not None else None


def _portal_version(coordinator: EgAustriaCoordinator) -> str | None:
    commit = coordinator.state.portal_git_commit
    return commit[:GIT_COMMIT_SHORT] if commit else None


SENSORS: tuple[EgSensorDescription, ...] = (
    EgSensorDescription(
        key="token_expires",
        translation_key="token_expires",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: token_expiry(c.config_entry),
    ),
    EgSensorDescription(
        key="data_complete_until",
        translation_key="data_complete_until",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda c: c.state.complete_until,
    ),
    EgSensorDescription(
        key="last_import",
        translation_key="last_import",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.state.last_success,
    ),
    EgSensorDescription(
        key="last_run_status",
        translation_key="last_run_status",
        device_class=SensorDeviceClass.ENUM,
        options=[s.value for s in RunStatus],
        value_fn=_run_status,
    ),
    EgSensorDescription(
        key="portal_version",
        translation_key="portal_version",
        entity_registry_enabled_default=False,
        value_fn=_portal_version,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the diagnostic sensors of an entry."""

    coordinator: EgAustriaCoordinator = entry.runtime_data
    async_add_entities(EgDiagnosticSensor(coordinator, d) for d in SENSORS)


class EgDiagnosticSensor(CoordinatorEntity["EgAustriaCoordinator"], SensorEntity):
    """A diagnostic value of the import (spec 06)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    entity_description: EgSensorDescription

    def __init__(
        self, coordinator: EgAustriaCoordinator, description: EgSensorDescription
    ) -> None:
        """Attach to the entry's service device."""

        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        # Entry id, not the entry unique id: that one changes when the
        # location is remapped (reconfigure), which would orphan entities.
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Values come from the store, so they stay available between runs."""

        return True

    @property
    def native_value(self) -> datetime | date | str | None:
        """Return the value from the coordinator."""

        return self.entity_description.value_fn(self.coordinator)
