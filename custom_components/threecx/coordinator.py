"""Data coordinator for the 3CX integration."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ThreeCXApiClient, ThreeCXApiError, ThreeCXSnapshot
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    XAPI_QUEUE_AGENT_PATH_TEMPLATES,
)

_LOGGER = logging.getLogger(__name__)


def _normalized_key(value: str) -> str:
    """Normalize a 3CX field name for tolerant matching."""
    return value.lower().replace("_", "").replace("-", "").replace(" ", "")


def _as_bool(value: Any) -> bool | None:
    """Convert common 3CX boolean/status representations."""
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
    """Detect a global queue/agent login flag from all user status fields."""
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
    """Use user-level queue login fields when queue-agent rows lack the flag."""
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
    """Fetch and normalize data from 3CX."""

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

    async def _async_probe_queue_agents(
        self, snapshot: ThreeCXSnapshot
    ) -> ThreeCXSnapshot:
        """Load queue agents through build-specific navigation endpoints."""
        if not snapshot.queue_records:
            self.queue_agent_diagnostics = {}
            return snapshot

        updated_queues = []
        diagnostics: dict[str, Any] = {}
        for queue in snapshot.queue_records:
            members = set(queue.members)
            logged_in = set(queue.logged_in_members)
            selected_endpoint: str | None = None
            errors: list[str] = []
            agent_fields: set[str] = set()
            agent_count = 0

            for template in XAPI_QUEUE_AGENT_PATH_TEMPLATES:
                queue_id = queue.queue_id.replace("'", "''")
                path = template.format(queue_id=queue_id)
                try:
                    values, _pages = await self.client._async_get_all_odata(path)
                except ThreeCXApiError as err:
                    errors.append(f"{path}: {err}")
                    continue

                selected_endpoint = path
                agent_count = len(values)
                for agent in values:
                    if isinstance(agent, dict):
                        agent_fields.update(str(key) for key in agent)
                        for nested_key in ("User", "Extension", "Dn"):
                            nested = agent.get(nested_key)
                            if isinstance(nested, dict):
                                agent_fields.update(
                                    f"{nested_key}.{key}" for key in nested
                                )
                    number, identifier = self.client._agent_identity(agent)
                    identity = number or identifier
                    if not identity:
                        continue
                    members.add(identity)
                    if self.client._agent_logged_in(agent) is True:
                        logged_in.add(identity)
                break

            diagnostics[queue.display_name] = {
                "queue_id": queue.queue_id,
                "number": queue.number,
                "endpoint": selected_endpoint,
                "agent_count": agent_count,
                "agent_fields": sorted(agent_fields),
                "errors": errors[-5:],
            }
            updated_queues.append(
                replace(
                    queue,
                    members=tuple(sorted(members)),
                    logged_in_members=tuple(sorted(logged_in)),
                )
            )

        snapshot.queue_records = tuple(updated_queues)
        snapshot.extension_records = self.client._enrich_extensions_with_queues(
            snapshot.extension_records, snapshot.queue_records
        )
        self.queue_agent_diagnostics = diagnostics
        return snapshot

    async def _async_update_data(self) -> ThreeCXSnapshot:
        try:
            snapshot = await self.client.async_get_snapshot()
            snapshot = await self._async_probe_queue_agents(snapshot)
            return _apply_user_queue_login_fallback(snapshot)
        except ThreeCXApiError as err:
            raise UpdateFailed(f"3CX update failed: {err}") from err
