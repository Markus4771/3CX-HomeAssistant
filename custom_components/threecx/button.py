"""Buttons for controlled 3CX diagnostics."""

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
    """Set up queue comparator and trace buttons."""
    coordinator: ThreeCXDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        [
            ThreeCXQueueCaptureButton(coordinator, entry, "logged_in"),
            ThreeCXQueueCaptureButton(coordinator, entry, "logged_out"),
            ThreeCXTraceButton(coordinator, entry),
        ],
        update_before_add=False,
    )


class _ThreeCXCentralButton(
    CoordinatorEntity[ThreeCXDataUpdateCoordinator], ButtonEntity
):
    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = True
    _attr_should_poll = False

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "3CX",
            "model": "Phone System V20",
        }

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success and self.coordinator.data)


class ThreeCXQueueCaptureButton(_ThreeCXCentralButton):
    """Capture the raw queue API state for one known login condition."""

    def __init__(self, coordinator, entry, label: str) -> None:
        super().__init__(coordinator, entry)
        self._label = label
        self._attr_unique_id = f"{entry.entry_id}_queue_compare_{label}"
        self._attr_name = (
            "Queue-Vergleich: angemeldet aufnehmen"
            if label == "logged_in"
            else "Queue-Vergleich: abgemeldet aufnehmen"
        )
        self._attr_icon = "mdi:camera-marker"

    async def async_press(self) -> None:
        await self.coordinator.async_capture_queue_compare(self._label)


class ThreeCXTraceButton(_ThreeCXCentralButton):
    """Expose and clear the bounded Call Control packet trace."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_call_control_trace"
        self._attr_name = "Call-Control-Trace leeren"
        self._attr_icon = "mdi:delete-sweep"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        analyzer = getattr(self.coordinator, "deep_call_control_analyzer", None)
        if analyzer is None:
            return {"deep_call_control": {"status": "nicht gestartet"}}
        return {"deep_call_control": analyzer.diagnostics(self.coordinator.call_control)}

    async def async_press(self) -> None:
        analyzer = getattr(self.coordinator, "deep_call_control_analyzer", None)
        if analyzer is not None:
            analyzer.clear()
        self.coordinator.async_update_listeners()
