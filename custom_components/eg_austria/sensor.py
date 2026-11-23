"""Diagnostic and summary sensors (spec 06 "Diagnostic entities", ADR 0014).

One service device per entry. Diagnostic sensors are
``EntityCategory.DIAGNOSTIC``. Summary sensors show shares in percent and € sums (issue #75) with no
``state_class``: the energy and money values stay in external statistics
(ADR 0003).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import CURRENCY_EURO, PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .importer import RunStatus
from .repairs import token_expiry
from .summary import MoneyDef, MoneyValue, Period, Settlement, SummaryDef, SummaryValue

if TYPE_CHECKING:
    from .coordinator import EgAustriaConfigEntry, EgAustriaCoordinator

# Device page (issue #30): a community integration, not an EG Austria product.
# No serial number or hardware version: metering point and community ids are
# personal data and stay out of the device registry.
MANUFACTURER = "magicdude4eva"
MODEL = "EG Austria member portal (unofficial)"
CONFIGURATION_URL = "https://github.com/magicdude4eva/ha-eg-austria"
GIT_COMMIT_SHORT = 7

# Read-only entities, updated by the coordinator; no parallel limit needed.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EgSensorDescription(SensorEntityDescription):
    """A diagnostic sensor and how to read its value."""

    value_fn: Callable[[EgAustriaCoordinator], datetime | date | str | None]


def _run_status(coordinator: EgAustriaCoordinator) -> str | None:
    """Return the last run's status; after a restart the stored one (issue #29)."""

    summary = coordinator.data
    status = (
        summary.status if summary is not None else coordinator.state.last_run_status
    )
    return status.value if status is not None else None


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
        key="meter_data_until",
        translation_key="meter_data_until",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda c: c.state.meter_until,
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
    entry: EgAustriaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the diagnostic sensors of an entry."""

    coordinator = entry.runtime_data
    integration = await async_get_integration(hass, DOMAIN)
    sw_version = str(integration.version) if integration.version else None
    async_add_entities(EgDiagnosticSensor(coordinator, d, sw_version) for d in SENSORS)
    async_add_entities(
        EgSummarySensor(coordinator, d, period, sw_version)
        for d in coordinator.summary_defs
        for period in Period
    )
    async_add_entities(
        EgMoneySensor(coordinator, d, period, sw_version)
        for d in coordinator.money_defs
        for period in Period
    )


def _device_info(entry: EgAustriaConfigEntry, sw_version: str | None) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=MODEL,
        sw_version=sw_version,
        configuration_url=CONFIGURATION_URL,
        entry_type=DeviceEntryType.SERVICE,
    )


class EgDiagnosticSensor(CoordinatorEntity["EgAustriaCoordinator"], SensorEntity):
    """A diagnostic value of the import (spec 06)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    entity_description: EgSensorDescription

    def __init__(
        self,
        coordinator: EgAustriaCoordinator,
        description: EgSensorDescription,
        sw_version: str | None = None,
    ) -> None:
        """Attach to the entry's service device."""

        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        # Entry id, not the entry unique id: that one changes when the
        # location is remapped (reconfigure), which would orphan entities.
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _device_info(entry, sw_version)

    @property
    def available(self) -> bool:
        """Values come from the store, so they stay available between runs."""

        return True

    @property
    def native_value(self) -> datetime | date | str | None:
        """Return the value from the coordinator."""

        return self.entity_description.value_fn(self.coordinator)


class EgSummarySensor(SensorEntity):
    """A share in percent for one period (ADR 0014).

    No ``state_class``: nothing is compiled into long-term statistics. The
    kWh it was computed from and the period are attributes. Updated by the
    coordinator's summary signal, not by run updates.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: EgAustriaCoordinator,
        definition: SummaryDef,
        period: Period,
        sw_version: str | None = None,
    ) -> None:
        """Attach to the entry's service device."""

        self.coordinator = coordinator
        self._definition = definition
        self._period = period
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{definition.key}_{period}"
        self._attr_translation_key = f"{definition.translation_key}_{period}"
        if definition.community is not None:
            self._attr_translation_placeholders = {"community": definition.community}
        self._attr_device_info = _device_info(entry, sw_version)

    async def async_added_to_hass(self) -> None:
        """Follow the summary signal."""

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.coordinator.summary_signal, self.async_write_ha_state
            )
        )

    @property
    def native_value(self) -> float | None:
        """Return the share, or None (unknown) when it can't be computed."""

        value = self._value()
        return value.value if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the period and the kWh behind the share."""

        summary = self.coordinator.summary.get(self._period)
        dates = summary.dates if summary is not None else None
        value = self._value()
        return {
            "period_start": dates.start.isoformat() if dates else None,
            "period_end": dates.end.isoformat() if dates else None,
            "numerator_kwh": value.numerator_kwh if value else None,
            "denominator_kwh": value.denominator_kwh if value else None,
        }

    def _value(self) -> SummaryValue | None:
        summary = self.coordinator.summary.get(self._period)
        if summary is None:
            return None
        return summary.values.get(self._definition.key)


class EgMoneySensor(SensorEntity):
    """A € sum for one period (issue #75, ADR 0014 pattern).

    No ``state_class``: the money statistics are the long-term record. The
    attribute ``settlement`` says whether the communities have settled the
    month (ADR 0008); values change when they do.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: EgAustriaCoordinator,
        definition: MoneyDef,
        period: Period,
        sw_version: str | None = None,
    ) -> None:
        """Attach to the entry's service device."""

        self.coordinator = coordinator
        self._definition = definition
        self._period = period
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{definition.key}_{period}"
        self._attr_translation_key = f"{definition.translation_key}_{period}"
        if definition.community is not None:
            self._attr_translation_placeholders = {"community": definition.community}
        self._attr_device_info = _device_info(entry, sw_version)

    async def async_added_to_hass(self) -> None:
        """Follow the summary signal."""

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.coordinator.summary_signal, self.async_write_ha_state
            )
        )

    @property
    def native_value(self) -> float | None:
        """Return the sum, or None (unknown) without money rows."""

        value = self._value()
        return value.value if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the period and the settlement state."""

        summary = self.coordinator.summary.get(self._period)
        dates = summary.dates if summary is not None else None
        value = self._value()
        settlement: Settlement | None = value.settlement if value else None
        return {
            "period_start": dates.start.isoformat() if dates else None,
            "period_end": dates.end.isoformat() if dates else None,
            "settlement": settlement.value if settlement else None,
        }

    def _value(self) -> MoneyValue | None:
        summary = self.coordinator.summary.get(self._period)
        if summary is None:
            return None
        return summary.money.get(self._definition.key)
