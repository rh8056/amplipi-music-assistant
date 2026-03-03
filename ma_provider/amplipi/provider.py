"""AmpliPi Music Assistant player provider implementation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

import aiohttp

from music_assistant.models.player_provider import PlayerProvider

from .constants import (
    CONF_AUTO_ZONES,
    CONF_HOST,
    CONF_PLAY_VOL_F,
    CONF_SOURCE_ID,
    CONF_ZONE_ID,
    DEFAULT_AUTO_ZONES,
    DEFAULT_PLAY_VOL_F,
    DEFAULT_SOURCE_ID,
)
from .player import AmpliPiPlayer

if TYPE_CHECKING:
    from music_assistant_models.player import Player as MAPlayer


class AmpliPiPlayerProvider(PlayerProvider):
    """AmpliPi player provider."""

    @property
    def _debug_enabled(self) -> bool:
        """Use MA/global logger level as primary debug control."""
        return self.logger.isEnabledFor(logging.DEBUG)

    @staticmethod
    def _extract_firmware_version(api_data: dict) -> str | None:
        """Best-effort extraction of firmware version from AmpliPi /api payload."""
        info = api_data.get("info") or {}
        fw_entries = info.get("fw") or []
        fw_version = None
        if isinstance(fw_entries, list) and fw_entries:
            first_fw = fw_entries[0] or {}
            fw_val = first_fw.get("version") if isinstance(first_fw, dict) else None
            if fw_val is not None and str(fw_val).strip():
                fw_version = str(fw_val).strip()

        candidates = (
            api_data.get("version"),
            info.get("version"),
            info.get("fw_version"),
            (api_data.get("system") or {}).get("version"),
            (api_data.get("system") or {}).get("fw_version"),
        )
        for value in candidates:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                if fw_version:
                    return f"{text} (fw {fw_version})"
                return text
        return f"fw {fw_version}" if fw_version else None

    async def handle_async_init(self) -> None:
        """Handle async initialization of provider."""
        self.logger.info("Initializing AmpliPi provider")

    async def loaded_in_mass(self) -> None:
        """Call after provider is loaded."""
        await self.discover_players()

    async def unload(self, is_removed: bool = False) -> None:
        """Handle unload/close of provider."""
        for player in self.players:
            await self.mass.players.unregister(player.player_id)

    async def discover_players(self) -> None:
        """Discover AmpliPi zones and register players."""
        host = cast(str, self.config.get_value(CONF_HOST))
        auto_zones = cast(bool, self.config.get_value(CONF_AUTO_ZONES, DEFAULT_AUTO_ZONES))
        source_id = cast(int, self.config.get_value(CONF_SOURCE_ID, DEFAULT_SOURCE_ID))
        play_vol_f = cast(float, self.config.get_value(CONF_PLAY_VOL_F, DEFAULT_PLAY_VOL_F))

        data = await self._request(host, "GET", "/api")
        zones = data.get("zones", [])
        firmware_version = self._extract_firmware_version(data)

        if not auto_zones:
            single_zone = cast(int, self.config.get_value(CONF_ZONE_ID, 0))
            zones = [z for z in zones if int(z.get("id", -1)) == single_zone]

        for zone in zones:
            zone_id = int(zone.get("id", -1))
            if zone_id < 0:
                continue
            zone_name = str(zone.get("name") or f"Zone {zone_id}").strip()
            player = AmpliPiPlayer(
                provider=self,
                player_id=f"amplipi_zone_{zone_id}",
                host=host,
                zone_id=zone_id,
                zone_name=zone_name,
                preferred_source_id=source_id,
                play_vol_f=play_vol_f,
                firmware_version=firmware_version,
            )
            existing: MAPlayer | None = self.mass.players.get(player.player_id)
            if existing is None:
                await self.mass.players.register(player)
            else:
                existing.display_name = zone_name
                existing.update_state()

    async def _request(
        self,
        host: str,
        method: str,
        path: str,
        payload: dict | None = None,
    ):
        """Issue request to AmpliPi API."""
        url = f"http://{host}{path}"
        should_log_poll = not (method == "GET" and path == "/api")
        if self._debug_enabled and should_log_poll:
            self.logger.debug("AmpliPi API request: %s %s payload=%s", method, path, payload or {})
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                if method == "GET":
                    async with session.get(url) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                elif method == "PATCH":
                    async with session.patch(url, json=payload or {}) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                elif method == "DELETE":
                    async with session.delete(url, json=payload or {}) as resp:
                        resp.raise_for_status()
                        if resp.content_type == "application/json":
                            data = await resp.json()
                        else:
                            data = {"status": resp.status}
                else:
                    async with session.post(url, json=payload or {}) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
            except Exception as err:
                if self._debug_enabled:
                    self.logger.debug("AmpliPi API error: %s %s err=%s", method, path, err)
                raise

        # Avoid very noisy logs for frequent polling calls.
        if self._debug_enabled and should_log_poll:
            if isinstance(data, dict):
                self.logger.debug("AmpliPi API response: %s %s keys=%s", method, path, sorted(data.keys()))
            else:
                self.logger.debug("AmpliPi API response: %s %s type=%s", method, path, type(data).__name__)
        return data
