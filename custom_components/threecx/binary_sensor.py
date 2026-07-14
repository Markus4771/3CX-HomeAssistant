"""Binary sensors for the 3CX integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ThreeCXExtension
from .const import DOMAIN
from .coordinator import ThreeCXDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up connection and extension binary sensors."""
    coordinator: ThreeCXDataUpdateCoordinator = entry.runtime_data
    async_add_entities([ThreeCXConnectionBinarySensor(coordinator, entry)])

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
        entities: list[BinarySensorEntity] = []
        for record in new_records:
            entities.extend(
                (
                    ThreeCXRegisteredBinarySensor(coordinator, entry, record.extension_id),
                    ThreeCXQueueLoggedInBinarySensor(coordinator, entry, record.extension_id),
                )
            )
        async_add_entities(entities)

    async_add_new_extensions()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_extensions))


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
        return self.coordinator.data.connected


class ThreeCXExtensionBinarySensor(
    CoordinatorEntity[ThreeCXDataUpdateCoordinator], BinarySensorEntity
):
    """Common base for one extension binary sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, extension_id: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._extension_id = extension_id
        record = self._record
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
        return super().available and self._record is not None


class ThreeCXRegisteredBinarySensor(ThreeCXExtensionBinarySensor):
    """Show whether an extension is registered at the PBX."""

    _attr_name = "An Anlage angemeldet"
    _attr_icon = "mdi:phone-check"

    def __init__(self, coordinator, entry, extension_id: str) -> None:
        super().__init__(coordinator, entry, extension_id)
        self._attr_unique_id = f"{entry.entry_id}_registered_{extension_id}"

    @property
    def is_on(self) -> bool | None:
        record = self._record
        return record.registered if record else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        record = self._record
        if record is None:
            return {"3cx_id": self._extension_id}
        return {
            "3cx_id": record.extension_id,
            "number": record.number,
            "display_name": record.name,
            "source": "3CX V20 user status fields",
        }


class ThreeCXQueueLoggedInBinarySensor(ThreeCXExtensionBinarySensor):
    """Show whether an extension is logged into at least one queue."""

    _attr_name = "In Warteschleife angemeldet"
    _attr_icon = "mdi:account-group"

    def __init__(self, coordinator, entry, extension_id: str) -> None:
        super().__init__(coordinator, entry, extension_id)
        self._attr_unique_id = f"{entry.entry_id}_queue_logged_in_{extension_id}"

    @property
    def is_on(self) -> bool:
        record = self._record
        return record.queue_logged_in if record else False

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        record = self._record
        if record is None:
            return {"3cx_id": self._extension_id}
        return {
            "3cx_id": record.extension_id,
            "number": record.number,
            "display_name": record.name,
            "warteschleifen_mitglied": list(record.queue_names),
            "angemeldet_in": list(record.queue_logged_in_names),
        }
