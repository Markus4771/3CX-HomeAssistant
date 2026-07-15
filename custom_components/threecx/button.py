"""Buttons for controlled 3CX queue-state comparison captures."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Set up queue comparator buttons."""
    coordinator: ThreeCXDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        [
            ThreeCXQueueCaptureButton(coordinator, entry, "logged_in"),
            ThreeCXQueueCaptureButton(coordinator, entry, "logged_out"),
        ]
    )


class ThreeCXQueueCaptureButton(
    CoordinatorEntity[ThreeCXDataUpdateCoordinator], ButtonEntity
):
    """Capture the raw queue API state for one known login condition."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, label: str) -> None:
        super().__init__(coordinator)
        self._label = label
        self._attr_unique_id = f"{entry.entry_id}_queue_compare_{label}"
        self._attr_name = (
            "Queue-Vergleich aufnehmen: angemeldet"
            if label == "logged_in"
            else "Queue-Vergleich aufnehmen: abgemeldet"
        )
        self._attr_icon = "mdi:camera-marker"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "3CX",
            "model": "Phone System",
        }

    async def async_press(self) -> None:
        await self.coordinator.async_capture_queue_compare(self._label)
