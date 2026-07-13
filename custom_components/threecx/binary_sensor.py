"""Binary sensors for the 3CX integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ThreeCXDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the 3CX connection sensor."""
    coordinator: ThreeCXDataUpdateCoordinator = entry.runtime_data
    async_add_entities([ThreeCXConnectionBinarySensor(coordinator, entry)])


class ThreeCXConnectionBinarySensor(
    CoordinatorEntity[ThreeCXDataUpdateCoordinator], BinarySensorEntity
):
    """Show whether the 3CX web service is reachable."""

    entity_description = BinarySensorEntityDescription(
        key="connected",
        name="Connected",
        icon="mdi:server-network",
    )
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connected"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "3CX",
            "model": "Phone System",
        }

    @property
    def is_on(self) -> bool:
        """Return true when the PBX is reachable."""
        return self.coordinator.data.connected
