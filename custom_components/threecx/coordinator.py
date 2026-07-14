"""Data coordinator for the 3CX integration."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ThreeCXApiClient, ThreeCXApiError, ThreeCXSnapshot
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

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

    updated_queues = tuple(
        replace(
            queue,
            logged_in_members=tuple(sorted(queue_logged_in[queue.queue_id])),
        )
        for queue in snapshot.queue_records
    )
    updated_extensions = tuple(
        replace(
            extension,
            queue_logged_in_names=tuple(
                sorted(extension_queue_names[extension.extension_id])
            ),
        )
        for extension in snapshot.extension_records
    )
    snapshot.queue_records = updated_queues
    snapshot.extension_records = updated_extensions
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

    async def _async_update_data(self) -> ThreeCXSnapshot:
        try:
            snapshot = await self.client.async_get_snapshot()
            return _apply_user_queue_login_fallback(snapshot)
        except ThreeCXApiError as err:
            raise UpdateFailed(f"3CX update failed: {err}") from err
