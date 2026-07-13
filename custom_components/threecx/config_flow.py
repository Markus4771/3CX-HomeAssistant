"""Config flow for the 3CX integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ThreeCXApiClient, ThreeCXConnectionError
from .const import (
    API_MODE_AUTO,
    API_MODE_LEGACY,
    API_MODE_V20,
    CONF_API_MODE,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)


class ThreeCXConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for 3CX."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip().rstrip("/")
            await self.async_set_unique_id(host.lower())
            self._abort_if_unique_id_configured()

            client = ThreeCXApiClient(
                session=async_get_clientsession(self.hass),
                host=host,
                port=user_input[CONF_PORT],
                username=user_input.get(CONF_USERNAME, ""),
                password=user_input.get(CONF_PASSWORD, ""),
                verify_ssl=user_input[CONF_VERIFY_SSL],
                api_mode=user_input[CONF_API_MODE],
            )
            try:
                await client.async_test_connection()
            except ThreeCXConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # Home Assistant config flows must not crash on unknown errors.
                errors["base"] = "unknown"
            else:
                user_input[CONF_HOST] = host
                return self.async_create_entry(title=f"3CX ({host})", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_USERNAME, default=""): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
                vol.Required(CONF_API_MODE, default=API_MODE_AUTO): vol.In(
                    [API_MODE_AUTO, API_MODE_V20, API_MODE_LEGACY]
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
