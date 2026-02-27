"""AmpliPi Music Assistant player provider implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import aiohttp

from music_assistant.models.player_provider import PlayerProvider

from .constants import (
    CONF_AUTO_ZONES,
    CONF_DEBUG_LOGS,
    CONF_HOST,
    CONF_PLAY_VOL_F,
    CONF_SOURCE_ID,
    CONF_ZONE_ID,
    DEFAULT_AUTO_ZONES,
    DEFAULT_DEBUG_LOGS,
    DEFAULT_PLAY_VOL_F,
    DEFAULT_SOURCE_ID,
)
from .player import AmpliPiPlayer

if TYPE_CHECKING:
    from music_assistant_models.player import Player as MAPlayer


class AmpliPiPlayerProvider(PlayerProvider):
    """AmpliPi player provider."""

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
        debug_logs = cast(bool, self.config.get_value(CONF_DEBUG_LOGS, DEFAULT_DEBUG_LOGS))

        data = await self._request(host, "GET", "/api")
        zones = data.get("zones", [])

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
                debug_logs=debug_logs,
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
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if method == "GET":
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    return await resp.json()
            if method == "PATCH":
                async with session.patch(url, json=payload or {}) as resp:
                    resp.raise_for_status()
                    return await resp.json()
            async with session.post(url, json=payload or {}) as resp:
                resp.raise_for_status()
                return await resp.json()
