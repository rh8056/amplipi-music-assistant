"""Config flow for AmpliPi Music Assistant integration."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST

from .const import (
    CONF_AUTO_ZONES,
    CONF_DEBUG_LOGS,
    CONF_PLAY_VOL_F,
    CONF_SOURCE_ID,
    CONF_ZONE_ID,
    DEFAULT_AUTO_ZONES,
    DEFAULT_DEBUG_LOGS,
    DEFAULT_PLAY_VOL_F,
    DEFAULT_SOURCE_ID,
    DOMAIN,
)


class AmpliPiMusicAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AmpliPi Music Assistant."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            auto_zones = bool(user_input.get(CONF_AUTO_ZONES, DEFAULT_AUTO_ZONES))
            zone_id = user_input.get(CONF_ZONE_ID)
            source_id = user_input.get(CONF_SOURCE_ID, DEFAULT_SOURCE_ID)

            if source_id < 1:
                errors["base"] = "invalid_source_id"
            if not auto_zones and (zone_id is None or zone_id < 0):
                errors["base"] = "invalid_zone_id"

            host = user_input[CONF_HOST]
            if not errors:
                # Normalize zone_id away when auto-zones is enabled.
                if auto_zones:
                    user_input.pop(CONF_ZONE_ID, None)

                try:
                    timeout = aiohttp.ClientTimeout(total=8)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(f"http://{host}/api") as resp:
                            resp.raise_for_status()
                            await resp.json()
                except Exception:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(host)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=f"AmpliPi ({host})", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_AUTO_ZONES, default=DEFAULT_AUTO_ZONES): bool,
                vol.Optional(CONF_ZONE_ID): vol.Coerce(int),
                vol.Optional(CONF_SOURCE_ID, default=DEFAULT_SOURCE_ID): vol.Coerce(int),
                vol.Optional(CONF_PLAY_VOL_F, default=DEFAULT_PLAY_VOL_F): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                vol.Optional(CONF_DEBUG_LOGS, default=DEFAULT_DEBUG_LOGS): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Get options flow handler."""
        return AmpliPiMusicAssistantOptionsFlow(config_entry)


class AmpliPiMusicAssistantOptionsFlow(config_entries.OptionsFlow):
    """Handle options for AmpliPi Music Assistant."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            auto_zones = bool(user_input.get(CONF_AUTO_ZONES, DEFAULT_AUTO_ZONES))
            zone_id = user_input.get(CONF_ZONE_ID)
            source_id = user_input.get(CONF_SOURCE_ID, DEFAULT_SOURCE_ID)

            if source_id < 1:
                errors["base"] = "invalid_source_id"
            if not auto_zones and (zone_id is None or zone_id < 0):
                errors["base"] = "invalid_zone_id"

            if not errors:
                if auto_zones:
                    user_input.pop(CONF_ZONE_ID, None)
                return self.async_create_entry(title="", data=user_input)

        options = {**self._config_entry.data, **self._config_entry.options}
        zone_id_default = options.get(CONF_ZONE_ID)
        if zone_id_default is None:
            zone_id_default = 0
        schema = vol.Schema(
            {
                vol.Optional(CONF_AUTO_ZONES, default=options.get(CONF_AUTO_ZONES, DEFAULT_AUTO_ZONES)): bool,
                vol.Optional(CONF_ZONE_ID, default=zone_id_default): vol.Coerce(int),
                vol.Optional(CONF_SOURCE_ID, default=options.get(CONF_SOURCE_ID, DEFAULT_SOURCE_ID)): vol.Coerce(int),
                vol.Optional(CONF_PLAY_VOL_F, default=options.get(CONF_PLAY_VOL_F, DEFAULT_PLAY_VOL_F)): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                vol.Optional(CONF_DEBUG_LOGS, default=options.get(CONF_DEBUG_LOGS, DEFAULT_DEBUG_LOGS)): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
