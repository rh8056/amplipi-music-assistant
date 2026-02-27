"""Minimal AmpliPi media_player platform for Home Assistant testing."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.util import slugify
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.const import (
    CONF_HOST,
    STATE_IDLE,
    STATE_OFF,
    STATE_PAUSED,
    STATE_PLAYING,
)
from homeassistant.components.media_player.const import DOMAIN as MP_DOMAIN
from homeassistant.helpers import entity_registry as er
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

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=1)


async def _build_entities(
    host: str,
    source_id: int,
    play_vol_f: float,
    auto_zones: bool,
    debug_logs: bool,
    single_zone_id: int | None = None,
) -> list["AmpliPiZonePlayer"]:
    """Build media_player entities based on config/options."""

    entities: list[AmpliPiZonePlayer] = []

    if single_zone_id is not None and not auto_zones:
        zone_id = single_zone_id
        name = f"Zone {zone_id}"
        entities.append(
            AmpliPiZonePlayer(
                host,
                zone_id,
                name,
                source_id,
                play_vol_f,
                zone_name=name,
                debug_logs=debug_logs,
            )
        )
        return entities

    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"http://{host}/api") as resp:
            resp.raise_for_status()
            data = await resp.json()

    for zone in data.get("zones", []):
        zid = int(zone.get("id", -1))
        if zid < 0:
            continue
        zname = str(zone.get("name") or f"Zone {zid}").strip()
        entities.append(
            AmpliPiZonePlayer(
                host,
                zid,
                zname,
                source_id,
                play_vol_f,
                zone_name=zname,
                debug_logs=debug_logs,
            )
        )

    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up media players from config entry."""
    cfg = hass.data[DOMAIN][entry.entry_id]
    entities = await _build_entities(
        host=cfg[CONF_HOST],
        source_id=cfg.get(CONF_SOURCE_ID, DEFAULT_SOURCE_ID),
        play_vol_f=cfg.get(CONF_PLAY_VOL_F, DEFAULT_PLAY_VOL_F),
        auto_zones=cfg.get(CONF_AUTO_ZONES, DEFAULT_AUTO_ZONES),
        debug_logs=cfg.get(CONF_DEBUG_LOGS, DEFAULT_DEBUG_LOGS),
        single_zone_id=cfg.get(CONF_ZONE_ID),
    )
    async_add_entities(entities, True)


class AmpliPiZonePlayer(MediaPlayerEntity):
    """Represent one AmpliPi zone as a Home Assistant media_player."""

    _attr_should_poll = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        host: str,
        zone_id: int,
        name: str,
        source_id: int,
        play_vol_f: float,
        zone_name: str | None = None,
        debug_logs: bool = False,
    ) -> None:
        self._host = host
        self._zone_id = zone_id
        self._preferred_source_id = source_id
        self._play_vol_f = play_vol_f
        self._attr_name = name
        self._zone_name = (zone_name or name or f"zone_{zone_id}").strip()
        self._debug_logs = debug_logs
        self.entity_id = f"{MP_DOMAIN}.amplipi_ma_{slugify(self._zone_name)}"

        # Keep unique_id stable for migration from older integration builds.
        self._attr_unique_id = f"amplipi_{host.replace('.', '_')}_zone_{zone_id}"
        self._attr_state = STATE_OFF
        self._attr_volume_level = 0.5
        self._attr_is_volume_muted = False
        self._attr_available = True
        self._attr_extra_state_attributes = {
            "host": host,
            "zone_id": zone_id,
        }
        self._last_source_id: int = -1
        self._last_stream_id: int = -1
        self._last_stream_supported_cmds: list[str] = []

    def _log_debug_event(self, msg: str, *args: Any) -> None:
        """Emit concise debug logs only when debug_logs option is enabled."""
        if self._debug_logs:
            _LOGGER.debug("[zone=%s name=%s] " + msg, self._zone_id, self._zone_name, *args)

    @property
    def suggested_object_id(self) -> str:
        """Suggest a stable, human-readable entity_id object_id.

        Example: media_player.amplipi_ma_bathroom
        """
        return f"amplipi_ma_{slugify(self._zone_name)}"

    async def async_added_to_hass(self) -> None:
        """Ensure registry entity_id uses zone-name based object_id when possible."""
        await super().async_added_to_hass()
        registry = er.async_get(self.hass)
        desired_entity_id = f"{MP_DOMAIN}.amplipi_ma_{slugify(self._zone_name)}"

        try:
            existing = registry.async_get(self.entity_id)
            if (
                self.entity_id != desired_entity_id
                and existing
                and existing.unique_id == self.unique_id
            ):
                # Update entity_id when needed; HA will suffix automatically if needed.
                registry.async_update_entity(self.entity_id, new_entity_id=desired_entity_id)

            # Also normalize registry display name to zone friendly name.
            renamed_entry = registry.async_get(desired_entity_id) or registry.async_get(self.entity_id)
            if renamed_entry and renamed_entry.unique_id == self.unique_id and renamed_entry.name != self._zone_name:
                registry.async_update_entity(renamed_entry.entity_id, name=self._zone_name)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to normalize entity_id for zone %s: %s", self._zone_id, err)

    def _select_source_for_play(self, sources: list[dict[str, Any]]) -> int:
        """Choose best source id for new playback.

        Strategy:
        1) Reuse currently attached source for this zone if valid.
        2) Pick first idle/stopped source.
        3) Fallback to configured preferred source.
        """
        if self._last_source_id >= 0:
            return self._last_source_id

        for src in sources:
            sid = int(src.get("id", -1))
            state = str((src.get("info") or {}).get("state", "")).lower()
            if sid >= 0 and state in {"stopped", "idle", "none"}:
                return sid

        return self._preferred_source_id

    @staticmethod
    def _parse_stream_id_from_source_input(source_input: str | None) -> int:
        """Parse stream id from a source input string like 'stream=996'."""
        if not source_input:
            return -1
        match = re.search(r"stream=(\d+)", source_input)
        if not match:
            return -1
        return int(match.group(1))

    async def _exec_stream_cmd(self, stream_id: int, cmd: str) -> bool:
        """Execute stream transport command. Returns True if successful."""
        if stream_id < 0:
            return False

        # Respect reported stream capabilities when available.
        if self._last_stream_supported_cmds and cmd not in self._last_stream_supported_cmds:
            self._log_debug_event(
                "skip unsupported stream cmd='%s' stream=%s supported=%s",
                cmd,
                stream_id,
                self._last_stream_supported_cmds,
            )
            return False

        try:
            await self._request("POST", f"/api/streams/{stream_id}/{cmd}")
            self._log_debug_event("stream cmd ok cmd='%s' stream=%s", cmd, stream_id)
            return True
        except Exception as err:  # noqa: BLE001
            self._log_debug_event("stream cmd fail cmd='%s' stream=%s err=%s", cmd, stream_id, err)
            return False

    async def _play_with_retry(self, attempts: int = 3, delay_seconds: float = 0.25) -> bool:
        """Retry play command to handle stream/source mapping races after /api/play."""
        for _ in range(attempts):
            await self.async_update()
            if await self._exec_stream_cmd(self._last_stream_id, "play"):
                self._log_debug_event("play retry succeeded stream=%s", self._last_stream_id)
                return True
            await asyncio.sleep(delay_seconds)
        self._log_debug_event("play retry exhausted attempts=%s", attempts)
        return False

    async def _stream_cmd_with_retry(
        self,
        cmd: str,
        attempts: int = 2,
        delay_seconds: float = 0.2,
    ) -> bool:
        """Retry generic stream command with short backoff."""
        for _ in range(attempts):
            await self.async_update()
            if await self._exec_stream_cmd(self._last_stream_id, cmd):
                self._log_debug_event("%s retry succeeded stream=%s", cmd, self._last_stream_id)
                return True
            await asyncio.sleep(delay_seconds)
        self._log_debug_event("%s retry exhausted attempts=%s", cmd, attempts)
        return False

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"http://{self._host}{path}"
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
            if method == "PUT":
                async with session.put(url, json=payload or {}) as resp:
                    resp.raise_for_status()
                    return await resp.json()
            async with session.post(url, json=payload or {}) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _set_zone(self, payload: dict[str, Any]) -> None:
        """Set zone fields, trying PATCH first (AmpliPi API), then POST fallback."""
        path = f"/api/zones/{self._zone_id}"
        try:
            await self._request("PATCH", path, payload)
            return
        except Exception:  # noqa: BLE001
            await self._request("POST", path, payload)

    async def async_update(self) -> None:
        """Poll AmpliPi API and update entity state."""
        try:
            data = await self._request("GET", "/api")
            zones = data.get("zones", [])
            sources = data.get("sources", [])
            zone = next((z for z in zones if int(z.get("id", -1)) == self._zone_id), None)
            if zone is None:
                self._attr_available = False
                return

            self._attr_available = True
            vol_f = zone.get("vol_f")
            if vol_f is not None:
                self._attr_volume_level = max(0.0, min(1.0, float(vol_f)))
            elif zone.get("vol") is not None and zone.get("vol_min") is not None and zone.get("vol_max") is not None:
                vol = float(zone["vol"])
                vol_min = float(zone["vol_min"])
                vol_max = float(zone["vol_max"])
                if vol_max > vol_min:
                    self._attr_volume_level = max(0.0, min(1.0, (vol - vol_min) / (vol_max - vol_min)))

            self._attr_is_volume_muted = bool(zone.get("mute", False))

            disabled = bool(zone.get("disabled", False))
            source_id = int(zone.get("source_id", -1))

            source_state = "stopped"
            source_input = None
            source_name = None
            source_supported_cmds: list[str] = []
            stream_name = None
            stream_type = None
            stream_supported_cmds: list[str] = []
            if source_id >= 0:
                src = next((s for s in sources if int(s.get("id", -1)) == source_id), None)
                source_state = str((src or {}).get("info", {}).get("state", "stopped")).lower()
                source_input = (src or {}).get("input")
                source_name = (src or {}).get("name")
                source_supported_cmds = (src or {}).get("info", {}).get("supported_cmds", []) or []

            stream_id = self._parse_stream_id_from_source_input(source_input)
            if stream_id >= 0:
                stream = next((s for s in data.get("streams", []) if int(s.get("id", -1)) == stream_id), None)
                stream_name = (stream or {}).get("name")
                stream_type = (stream or {}).get("type")
                # Some docs mention info.stream_cmds, keep both keys just in case.
                stream_supported_cmds = (
                    (stream or {}).get("info", {}).get("stream_cmds", [])
                    or (stream or {}).get("stream_cmds", [])
                    or []
                )

            self._last_source_id = source_id
            self._last_stream_id = stream_id
            self._last_stream_supported_cmds = stream_supported_cmds
            self._log_debug_event(
                "state source_id=%s stream_id=%s source_state=%s muted=%s disabled=%s",
                source_id,
                stream_id,
                source_state,
                self._attr_is_volume_muted,
                disabled,
            )

            if disabled:
                self._attr_state = STATE_OFF
            elif source_id < 0:
                self._attr_state = STATE_IDLE
            elif source_state in {"playing", "buffering"}:
                self._attr_state = STATE_PLAYING
            elif source_state in {"paused"}:
                self._attr_state = STATE_PAUSED
            elif source_state in {"stopped", "idle", "none"}:
                self._attr_state = STATE_IDLE
            elif source_id >= 0 and not self._attr_is_volume_muted:
                # Fallback only for unknown source states.
                self._attr_state = STATE_PLAYING
            else:
                self._attr_state = STATE_IDLE

            self._attr_extra_state_attributes = {
                "host": self._host,
                "zone_id": self._zone_id,
                "source_id": source_id,
                "source_name": source_name,
                "source_input": source_input,
                "stream_id": stream_id,
                "stream_name": stream_name,
                "stream_type": stream_type,
                "source_state": source_state,
                "source_supported_cmds": source_supported_cmds,
                "stream_supported_cmds": stream_supported_cmds,
                "disabled": disabled,
            }
        except Exception as err:  # noqa: BLE001
            self._log_debug_event("poll failed err=%s", err)
            self._attr_available = False

    async def async_set_volume_level(self, volume: float) -> None:
        await self._set_zone({"vol_f": max(0.0, min(1.0, float(volume)))})

    async def async_mute_volume(self, mute: bool) -> None:
        await self._set_zone({"mute": bool(mute)})

    async def async_turn_on(self) -> None:
        source_to_restore = self._last_source_id if self._last_source_id >= 0 else self._preferred_source_id
        await self._set_zone({"disabled": False, "source_id": source_to_restore, "mute": False})

    async def async_turn_off(self) -> None:
        await self._set_zone({"source_id": -1, "disabled": True})

    async def async_media_play(self) -> None:
        """Resume stream playback if possible, otherwise unmute zone."""
        did_play = await self._play_with_retry()
        await self._set_zone({"mute": False, "disabled": False})
        if not did_play:
            if self._last_stream_id < 0:
                _LOGGER.warning(
                    "AmpliPi zone %s: no stream id found; unmuted zone without transport play",
                    self._zone_id,
                )
            else:
                _LOGGER.warning(
                    "AmpliPi zone %s: play transport command failed after retries; unmuted zone as fallback",
                    self._zone_id,
                )
        self._attr_state = STATE_PLAYING

    async def async_media_pause(self) -> None:
        """Pause stream playback if possible, fallback to mute."""
        did_pause = await self._stream_cmd_with_retry("pause")
        if not did_pause:
            _LOGGER.warning(
                "AmpliPi zone %s: pause transport command unavailable/failed; muting zone as fallback",
                self._zone_id,
            )
            await self._set_zone({"mute": True})
        self._attr_state = STATE_PAUSED

    async def async_media_play_pause(self) -> None:
        """Toggle play/pause explicitly using transport commands.

        AmpliPi requires explicit play for resume (not another pause call).
        """
        if self._attr_state == STATE_PLAYING:
            await self.async_media_pause()
        else:
            await self.async_media_play()

    async def async_media_stop(self) -> None:
        """Stop playback in this zone by detaching source."""
        did_stop = await self._stream_cmd_with_retry("stop")
        if not did_stop:
            _LOGGER.warning(
                "AmpliPi zone %s: stop transport command unavailable/failed; detaching source as fallback",
                self._zone_id,
            )
        await self._set_zone({"source_id": -1})
        self._attr_state = STATE_IDLE

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Play media.

        Uses AmpliPi /api/play endpoint with payload:
        {"media": <url-or-path>, "source_id": <configured>, "vol_f": <configured>}.

        Fallback: if media_id is an integer-like string, treat it as source selection.
        """
        try:
            source_id = int(media_id)
        except (TypeError, ValueError):
            data = await self._request("GET", "/api")
            sources = data.get("sources", [])
            selected_source_id = self._select_source_for_play(sources)
            self._log_debug_event("play_media media='%s' selected_source_id=%s", media_id, selected_source_id)
            await self._request(
                "POST",
                "/api/play",
                {
                    "media": media_id,
                    "source_id": selected_source_id,
                    "vol_f": self._play_vol_f,
                },
            )
            await self._set_zone({"source_id": selected_source_id, "mute": False, "disabled": False})
            did_play = await self._play_with_retry()
            if not did_play:
                _LOGGER.warning(
                    "AmpliPi zone %s: /api/play succeeded but stream play transport did not; continuing with source attach",
                    self._zone_id,
                )
            self._attr_state = STATE_PLAYING
            return

        await self._set_zone({"source_id": source_id, "disabled": False})
        self._attr_state = STATE_PLAYING
