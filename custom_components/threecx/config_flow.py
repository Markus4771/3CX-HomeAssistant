"""Config flow for the 3CX integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ThreeCXApiClient,
    ThreeCXAuthenticationError,
    ThreeCXConnectionError,
)
from .const import (
    API_MODE_V20,
    CONF_API_MODE,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)


class ThreeCXConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for 3CX V20."""

    VERSION = 2

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
                client_id=user_input[CONF_CLIENT_ID].strip(),
                client_secret=user_input[CONF_CLIENT_SECRET],
                verify_ssl=user_input[CONF_VERIFY_SSL],
                api_mode=API_MODE_V20,
            )
            try:
                await client.async_test_connection()
            except ThreeCXAuthenticationError:
                errors["base"] = "invalid_auth"
            except ThreeCXConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # Config flows must not crash on unknown API errors.
                errors["base"] = "unknown"
            else:
                user_input[CONF_HOST] = host
                user_input[CONF_API_MODE] = API_MODE_V20
                return self.async_create_entry(title=f"3CX V20 ({host})", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_CLIENT_ID): str,
                vol.Required(CONF_CLIENT_SECRET): str,
                vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
