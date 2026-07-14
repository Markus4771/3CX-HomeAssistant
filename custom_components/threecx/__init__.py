"""3CX integration for Home Assistant."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ThreeCXApiClient
from .call_control import ThreeCXCallControlClient
from .const import (
    API_MODE_V20,
    CALL_CONTROL_WS_PATHS,
    CONF_API_MODE,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    EVENT_CALL_CONTROL,
    PLATFORMS,
)
from .coordinator import ThreeCXDataUpdateCoordinator


type ThreeCXConfigEntry = ConfigEntry[ThreeCXDataUpdateCoordinator]


def _safe_event_name(value: Any) -> str:
    """Create a stable Home Assistant event suffix from a 3CX event type."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "unknown").lower()).strip("_")
    return normalized[:80] or "unknown"


async def async_setup_entry(hass: HomeAssistant, entry: ThreeCXConfigEntry) -> bool:
    """Set up 3CX V20 from a config entry."""
    session = async_get_clientsession(hass)
    client = ThreeCXApiClient(
        session=session,
        host=entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        client_id=entry.data.get(CONF_CLIENT_ID, ""),
        client_secret=entry.data.get(CONF_CLIENT_SECRET, ""),
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        api_mode=entry.data.get(CONF_API_MODE, API_MODE_V20),
    )
    coordinator = ThreeCXDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def async_handle_call_control(payload: dict[str, Any]) -> None:
        """Forward raw and normalized Call Control frames to Home Assistant."""
        normalized = payload.get("_threecx_normalized", {})
        if not isinstance(normalized, dict):
            normalized = {}
        raw_type = normalized.get("raw_type", "unknown")
        normalized_state = normalized.get("normalized_state", "unknown")
        event_data = {
            "config_entry_id": entry.entry_id,
            "event_type": str(raw_type),
            "normalized_state": str(normalized_state),
            "call_id": normalized.get("call_id"),
            "source": normalized.get("source"),
            "destination": normalized.get("destination"),
            "direction": normalized.get("direction"),
            "payload": payload,
        }
        hass.bus.async_fire(EVENT_CALL_CONTROL, event_data)
        hass.bus.async_fire(
            f"{DOMAIN}_{_safe_event_name(normalized_state)}",
            event_data,
        )
        coordinator.async_update_listeners()

    call_control = ThreeCXCallControlClient(
        session=session,
        base_url=client.base_url,
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        token_provider=client.async_authenticate,
        candidate_paths=CALL_CONTROL_WS_PATHS,
        event_callback=async_handle_call_control,
    )
    coordinator.call_control = call_control
    call_control.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ThreeCXConfigEntry) -> bool:
    """Unload a 3CX config entry."""
    coordinator = entry.runtime_data
    if coordinator.call_control is not None:
        await coordinator.call_control.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
