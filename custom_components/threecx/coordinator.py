"""Data coordinator for the 3CX integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ThreeCXApiClient, ThreeCXApiError, ThreeCXSnapshot
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN


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
            logger=__import__("logging").getLogger(__name__),
            name=f"{DOMAIN}-{entry.entry_id}",
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=entry,
        )
        self.client = client

    async def _async_update_data(self) -> ThreeCXSnapshot:
        try:
            return await self.client.async_get_snapshot()
        except ThreeCXApiError as err:
            raise UpdateFailed(f"3CX update failed: {err}") from err
