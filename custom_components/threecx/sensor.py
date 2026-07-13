"""Sensors for the 3CX integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ThreeCXExtension, ThreeCXSnapshot
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
    """Set up 3CX sensors and dynamically discover extensions."""
    coordinator: ThreeCXDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        ThreeCXSensor(coordinator, entry, description) for description in SENSORS
    )

    known_extension_ids: set[str] = set()

    @callback
    def async_add_new_extensions() -> None:
        new_records = [
            record
            for record in coordinator.data.extension_records
            if record.extension_id not in known_extension_ids
        ]
        if not new_records:
            return
        known_extension_ids.update(record.extension_id for record in new_records)
        async_add_entities(
            ThreeCXExtensionSensor(coordinator, entry, record.extension_id)
            for record in new_records
        )

    async_add_new_extensions()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_extensions))


class ThreeCXSensor(CoordinatorEntity[ThreeCXDataUpdateCoordinator], SensorEntity):
    """Representation of a 3CX summary sensor."""

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
            "model": "Phone System V20",
        }

    @property
    def native_value(self) -> int | str:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)


class ThreeCXExtensionSensor(
    CoordinatorEntity[ThreeCXDataUpdateCoordinator], SensorEntity
):
    """Representation of one 3CX V20 extension."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:deskphone"

    def __init__(
        self,
        coordinator: ThreeCXDataUpdateCoordinator,
        entry: ConfigEntry,
        extension_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._extension_id = extension_id
        record = self._record
        self._attr_unique_id = f"{entry.entry_id}_extension_{extension_id}"
        self._attr_name = "Extension"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_extension_{extension_id}")},
            "via_device": (DOMAIN, entry.entry_id),
            "name": record.name if record else f"3CX extension {extension_id}",
            "manufacturer": "3CX",
            "model": "V20 Extension",
        }

    @property
    def _record(self) -> ThreeCXExtension | None:
        return next(
            (
                record
                for record in self.coordinator.data.extension_records
                if record.extension_id == self._extension_id
            ),
            None,
        )

    @property
    def available(self) -> bool:
        """Return whether this extension still exists in 3CX."""
        return super().available and self._record is not None

    @property
    def native_value(self) -> str | None:
        """Use the extension number as the entity state."""
        record = self._record
        return record.number if record else None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose stable 3CX identity and name fields."""
        record = self._record
        if record is None:
            return {"3cx_id": self._extension_id}
        return {
            "3cx_id": record.extension_id,
            "number": record.number,
            "first_name": record.first_name,
            "last_name": record.last_name,
            "display_name": record.name,
        }
