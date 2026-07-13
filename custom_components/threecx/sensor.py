"""Sensors for the 3CX integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ThreeCXSnapshot
from .const import DOMAIN
from .coordinator import ThreeCXDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class ThreeCXSensorDescription(SensorEntityDescription):
    """Describe a 3CX sensor."""

    value_fn: Callable[[ThreeCXSnapshot], int | str]


SENSORS = (
    ThreeCXSensorDescription(
        key="extensions",
        name="Extensions",
        icon="mdi:phone-classic",
        native_unit_of_measurement="extensions",
        value_fn=lambda data: data.extensions,
    ),
    ThreeCXSensorDescription(
        key="active_calls",
        name="Active calls",
        icon="mdi:phone-in-talk",
        native_unit_of_measurement="calls",
        value_fn=lambda data: data.active_calls,
    ),
    ThreeCXSensorDescription(
        key="api_mode",
        name="API mode",
        icon="mdi:api",
        value_fn=lambda data: data.api_mode,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up 3CX sensors."""
    coordinator: ThreeCXDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        ThreeCXSensor(coordinator, entry, description) for description in SENSORS
    )


class ThreeCXSensor(CoordinatorEntity[ThreeCXDataUpdateCoordinator], SensorEntity):
    """Representation of a 3CX sensor."""

    entity_description: ThreeCXSensorDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "3CX",
            "model": "Phone System",
        }

    @property
    def native_value(self) -> int | str:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)
