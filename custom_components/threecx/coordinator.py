"""Data coordinator for the 3CX integration."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ThreeCXApiClient, ThreeCXApiError, ThreeCXSnapshot
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .entity_set_status import async_apply_entity_set_status
from .live_state import ThreeCXLiveState
from .metadata_explorer import async_discover_queue_agent_metadata
from .queue_agents import async_enrich_queue_agents
from .state_engine import apply_state_engine

_LOGGER = logging.getLogger(__name__)


def _normalized_key(value: str) -> str:
    return value.lower().replace("_", "").replace("-", "").replace(" ", "")


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {
            "true", "yes", "1", "on", "online", "active", "enabled",
            "loggedin", "logged in", "login", "available",
        }:
            return True
        if normalized in {
            "false", "no", "0", "off", "offline", "inactive", "disabled",
            "loggedout", "logged out", "logout", "unavailable",
        }:
            return False
    return None


def _user_queue_login(record) -> tuple[bool | None, tuple[str, ...]]:
    matched_keys: list[str] = []
    result: bool | None = None
    for key, value in record.status_attributes.items():
        normalized = _normalized_key(key)
        queue_related = "queue" in normalized or "agent" in normalized
        login_related = (
            "login" in normalized
            or "logged" in normalized
            or normalized in {"isqueueactive", "queueactive", "agentactive"}
        )
        if not (queue_related and login_related):
            continue
        parsed = _as_bool(value)
        if parsed is None:
            continue
        matched_keys.append(key)
        if parsed is True:
            result = True
        elif result is None:
            result = False
    return result, tuple(sorted(matched_keys))


def _apply_user_queue_login_fallback(snapshot: ThreeCXSnapshot) -> ThreeCXSnapshot:
    if not snapshot.queue_records or not snapshot.extension_records:
        return snapshot

    queue_logged_in: dict[str, set[str]] = {
        queue.queue_id: set(queue.logged_in_members) for queue in snapshot.queue_records
    }
    extension_queue_names: dict[str, set[str]] = {
        extension.extension_id: set(extension.queue_logged_in_names)
        for extension in snapshot.extension_records
    }

    for extension in snapshot.extension_records:
        logged_in, matched_keys = _user_queue_login(extension)
        if matched_keys:
            _LOGGER.info(
                "3CX queue login fields for extension %s: %s -> %s",
                extension.number or extension.extension_id,
                ", ".join(matched_keys),
                logged_in,
            )
        if logged_in is not True:
            continue

        identities = {extension.extension_id, extension.number}
        identities.discard("")
        for queue in snapshot.queue_records:
            if not identities.intersection(queue.members):
                continue
            identity = extension.number or extension.extension_id
            queue_logged_in[queue.queue_id].add(identity)
            extension_queue_names[extension.extension_id].add(queue.display_name)

    snapshot.queue_records = tuple(
        replace(
            queue,
            logged_in_members=tuple(sorted(queue_logged_in[queue.queue_id])),
        )
        for queue in snapshot.queue_records
    )
    snapshot.extension_records = tuple(
        replace(
            extension,
            queue_logged_in_names=tuple(
                sorted(extension_queue_names[extension.extension_id])
            ),
        )
        for extension in snapshot.extension_records
    )
    return snapshot


class ThreeCXDataUpdateCoordinator(DataUpdateCoordinator[ThreeCXSnapshot]):
    """Fetch, normalize and combine polling and realtime 3CX data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ThreeCXApiClient,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}-{entry.entry_id}",
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self.call_control: Any | None = None
        self.queue_agent_diagnostics: dict[str, Any] = {}
        self.odata_metadata: dict[str, Any] = {}
        self.entity_set_status_diagnostics: dict[str, Any] = {}
        self.state_engine_diagnostics: dict[str, Any] = {}
        self.live_state = ThreeCXLiveState()
        self.event_history: list[dict[str, Any]] = []

    def ingest_live_event(
        self, payload: dict[str, Any], normalized: dict[str, Any]
    ) -> bool:
        """Apply one Call Control event, record it and refresh entities."""
        applied = self.live_state.ingest(payload, normalized)
        live_event = self.live_state.last_applied_event if applied else {}
        self.event_history.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "raw_type": str(normalized.get("raw_type", "unknown")),
                "normalized_state": str(
                    live_event.get(
                        "normalized_state",
                        normalized.get("normalized_state", "unknown"),
                    )
                ),
                "applied": applied,
                "extension": live_event.get("extension"),
                "queue": live_event.get("queue"),
                "call_id": normalized.get("call_id"),
                "source": normalized.get("source"),
                "destination": normalized.get("destination"),
                "direction": normalized.get("direction"),
                "field_names": list(normalized.get("field_names", []))[:100],
            }
        )
        del self.event_history[:-200]
        if applied and self.data is not None:
            updated = self.live_state.apply_to_snapshot(self.data)
            updated.extension_records = self.client._enrich_extensions_with_queues(  # noqa: SLF001
                updated.extension_records,
                updated.queue_records,
            )
            self.data, self.state_engine_diagnostics = apply_state_engine(updated)
        self.async_update_listeners()
        return applied

    def event_monitor_diagnostics(self) -> dict[str, Any]:
        """Return compact event diagnostics suitable for entity attributes."""
        applied = sum(1 for event in self.event_history if event["applied"])
        ignored = len(self.event_history) - applied
        return {
            "buffer_size": len(self.event_history),
            "buffer_limit": 200,
            "events_applied": applied,
            "events_ignored": ignored,
            "last_event": self.event_history[-1] if self.event_history else None,
            "recent_events": list(self.event_history[-50:]),
        }

    def live_monitor_diagnostics(self) -> dict[str, Any]:
        """Return a concise timeline for the central Live Monitor sensor."""
        return {
            "event_count": len(self.event_history),
            "last_event": self.event_history[-1] if self.event_history else None,
            "timeline": list(self.event_history[-25:]),
            "state_engine": self.state_engine_diagnostics,
            "live_state": self.live_state.diagnostics(),
        }

    async def _async_update_data(self) -> ThreeCXSnapshot:
        try:
            snapshot = await self.client.async_get_snapshot()
            snapshot, diagnostics = await async_enrich_queue_agents(
                self.client, snapshot
            )
            if not self.odata_metadata:
                self.odata_metadata = await async_discover_queue_agent_metadata(
                    self.client
                )
            snapshot, entity_set_diagnostics = await async_apply_entity_set_status(
                self.client,
                snapshot,
                self.odata_metadata,
            )
            self.entity_set_status_diagnostics = entity_set_diagnostics
            diagnostics["_odata_metadata"] = self.odata_metadata
            diagnostics["_entity_set_status"] = entity_set_diagnostics
            self.queue_agent_diagnostics = diagnostics
            snapshot.extension_records = self.client._enrich_extensions_with_queues(  # noqa: SLF001
                snapshot.extension_records,
                snapshot.queue_records,
            )
            snapshot = _apply_user_queue_login_fallback(snapshot)
            snapshot = self.live_state.apply_to_snapshot(snapshot)
            snapshot.extension_records = self.client._enrich_extensions_with_queues(  # noqa: SLF001
                snapshot.extension_records,
                snapshot.queue_records,
            )
            snapshot, self.state_engine_diagnostics = apply_state_engine(snapshot)
            return snapshot
        except ThreeCXApiError as err:
            raise UpdateFailed(f"3CX update failed: {err}") from err
