"""3CX integration for Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ThreeCXApiClient
from .const import (
    API_MODE_V20,
    CONF_API_MODE,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import ThreeCXDataUpdateCoordinator


type ThreeCXConfigEntry = ConfigEntry[ThreeCXDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ThreeCXConfigEntry) -> bool:
    """Set up 3CX V20 from a config entry."""
    client = ThreeCXApiClient(
        session=async_get_clientsession(hass),
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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ThreeCXConfigEntry) -> bool:
    """Unload a 3CX config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
