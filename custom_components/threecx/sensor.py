"""Sensors for the 3CX integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

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
        [
            *(ThreeCXSensor(coordinator, entry, description) for description in SENSORS),
            ThreeCXUserImportDiagnosticSensor(coordinator, entry),
            ThreeCXQueueOverviewSensor(coordinator, entry),
        ]
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
        entities: list[SensorEntity] = []
        for record in new_records:
            entities.extend(
                (
                    ThreeCXExtensionSensor(coordinator, entry, record.extension_id),
                    ThreeCXStatusSensor(coordinator, entry, record.extension_id),
                    ThreeCXQueueStatusSensor(coordinator, entry, record.extension_id),
                )
            )
        async_add_entities(entities)

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
        return self.entity_description.value_fn(self.coordinator.data)


class ThreeCXCentralSensor(
    CoordinatorEntity[ThreeCXDataUpdateCoordinator], SensorEntity
):
    """Common central-device sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "3CX",
            "model": "Phone System V20",
        }


class ThreeCXUserImportDiagnosticSensor(ThreeCXCentralSensor):
    """Show how many user records the API returned and imported."""

    _attr_name = "User import diagnostic"
    _attr_icon = "mdi:account-search"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_user_import_diagnostic"

    @property
    def native_value(self) -> int:
        return self.coordinator.data.api_users_received

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "api_users_received": data.api_users_received,
            "users_imported": data.api_users_imported,
            "users_skipped": data.api_users_skipped,
            "odata_pages": data.api_pages,
            "skipped_records": list(data.skipped_records),
            "permission_hint": (
                "Wenn api_users_received bereits zu niedrig ist, begrenzt die "
                "3CX-Rolle oder Abteilung des Dienstprinzipals die Sichtbarkeit."
            ),
        }


class ThreeCXQueueOverviewSensor(ThreeCXCentralSensor):
    """Show all queues and the agents currently logged into them."""

    _attr_name = "Warteschleifen"
    _attr_icon = "mdi:account-group"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_queues"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.queue_records)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        queues: dict[str, Any] = {}
        for queue in data.queue_records:
            queues[queue.display_name] = {
                "number": queue.number,
                "members": list(queue.members),
                "logged_in_members": list(queue.logged_in_members),
                "logged_in_count": len(queue.logged_in_members),
                **dict(queue.raw_fields),
            }
        return {
            "queues_available": data.queues_available,
            "queue_pages": data.queue_pages,
            "queue_error": data.queue_error,
            "queues": queues,
        }


class ThreeCXExtensionEntity(CoordinatorEntity[ThreeCXDataUpdateCoordinator]):
    """Common base for entities belonging to one V20 extension."""

    _attr_has_entity_name = True

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


class ThreeCXExtensionSensor(ThreeCXExtensionEntity, SensorEntity):
    """Representation of one 3CX V20 extension number."""

    _attr_icon = "mdi:deskphone"

    def __init__(self, coordinator, entry, extension_id: str) -> None:
        super().__init__(coordinator, entry, extension_id)
        self._attr_unique_id = f"{entry.entry_id}_extension_{extension_id}"
        self._attr_name = "Extension"

    @property
    def native_value(self) -> str | None:
        record = self._record
        return record.number if record else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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


class ThreeCXStatusSensor(ThreeCXExtensionEntity, SensorEntity):
    """Expose the best available presence status and every supplied status field."""

    _attr_icon = "mdi:account-circle"

    def __init__(self, coordinator, entry, extension_id: str) -> None:
        super().__init__(coordinator, entry, extension_id)
        self._attr_unique_id = f"{entry.entry_id}_status_{extension_id}"
        self._attr_name = "Status"

    @property
    def native_value(self) -> str | None:
        record = self._record
        return record.presence_status if record else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        record = self._record
        if record is None:
            return {"3cx_id": self._extension_id}
        return {
            "3cx_id": record.extension_id,
            "number": record.number,
            "display_name": record.name,
            "registered": record.registered,
            **record.status_attributes,
        }


class ThreeCXQueueStatusSensor(ThreeCXExtensionEntity, SensorEntity):
    """Show queue membership and current queue login state for one user."""

    _attr_icon = "mdi:account-multiple-check"

    def __init__(self, coordinator, entry, extension_id: str) -> None:
        super().__init__(coordinator, entry, extension_id)
        self._attr_unique_id = f"{entry.entry_id}_queue_status_{extension_id}"
        self._attr_name = "Warteschleifenstatus"

    @property
    def native_value(self) -> str | None:
        record = self._record
        if record is None:
            return None
        if record.queue_logged_in_names:
            return "angemeldet"
        if record.queue_names:
            return "abgemeldet"
        return "kein Mitglied"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        record = self._record
        if record is None:
            return {"3cx_id": self._extension_id}
        return {
            "3cx_id": record.extension_id,
            "number": record.number,
            "display_name": record.name,
            "warteschleifen_mitglied": list(record.queue_names),
            "angemeldet_in": list(record.queue_logged_in_names),
            "an_der_anlage_angemeldet": record.registered,
        }
