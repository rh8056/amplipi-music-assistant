"""AmpliPi Player implementation for Music Assistant."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from music_assistant_models.player import DeviceInfo
from music_assistant_models.enums import PlaybackState, PlayerFeature, PlayerType

from music_assistant.models.player import Player, PlayerMedia

if TYPE_CHECKING:
    from .provider import AmpliPiPlayerProvider


class AmpliPiPlayer(Player):
    """AmpliPi zone mapped as a Music Assistant player."""

    def __init__(
        self,
        provider: AmpliPiPlayerProvider,
        player_id: str,
        host: str,
        zone_id: int,
        zone_name: str,
        preferred_source_id: int,
        play_vol_f: float,
        firmware_version: str | None = None,
    ) -> None:
        super().__init__(provider, player_id)
        self.host = host
        self.zone_id = zone_id
        self.zone_name = zone_name
        self.preferred_source_id = preferred_source_id
        self.play_vol_f = play_vol_f
        self.firmware_version = firmware_version

        self.last_source_id: int = -1
        self.last_stream_id: int = -1
        self.last_stream_supported_cmds: list[str] = []
        self._last_play_cmd_ts: float = 0.0
        self._last_play_media_start_ts: float = 0.0
        self._last_pause_cmd_ts: float = 0.0
        self._replay_guard_started_ts: float = 0.0
        self._replay_guard_media_uri: str | None = None
        self._last_media_uri: str | None = None
        self._sync_leader: str | None = None

        # group model: leader has group_members list including itself, followers have synced_to.
        self._attr_group_members = [player_id]
        self._attr_can_group_with = {provider.instance_id}

        self._attr_name = zone_name
        self._attr_type = PlayerType.PLAYER
        self._attr_device_info = DeviceInfo(
            model="AmpliPro",
            manufacturer="MicroNova",
            software_version=firmware_version,
            ip_address=host,
        )
        self._attr_available = True
        self._attr_powered = True
        self._attr_supported_features = {
            PlayerFeature.POWER,
            PlayerFeature.VOLUME_SET,
            PlayerFeature.VOLUME_MUTE,
            PlayerFeature.PAUSE,
            PlayerFeature.SET_MEMBERS,
        }
        self._attr_elapsed_time = 0
        self._attr_elapsed_time_last_updated = time.time()

    @property
    def synced_to(self) -> str | None:
        """Return sync leader player id if this player is grouped as follower."""
        return self._sync_leader

    def _log_debug(self, msg: str, *args: Any) -> None:
        if self.provider.logger.isEnabledFor(logging.DEBUG):
            self.provider.logger.debug("[zone=%s name=%s] " + msg, self.zone_id, self.zone_name, *args)

    def _log_action(self, action: str, **kwargs: Any) -> None:
        """Log high-signal action breadcrumbs for playback debugging."""
        if not self.provider.logger.isEnabledFor(logging.DEBUG):
            return
        details = " ".join(f"{k}={v}" for k, v in kwargs.items())
        self.provider.logger.debug(
            "[zone=%s name=%s] action=%s %s",
            self.zone_id,
            self.zone_name,
            action,
            details,
        )

    async def _set_zone_by_id(self, zone_id: int, payload: dict[str, Any]) -> None:
        """Set a zone by numeric id with PATCH->POST fallback."""
        try:
            await self.provider._request(self.host, "PATCH", f"/api/zones/{zone_id}", payload)
        except Exception:
            await self.provider._request(self.host, "POST", f"/api/zones/{zone_id}", payload)

    @property
    def needs_poll(self) -> bool:
        """Return True: polling is used for zone/source/stream updates."""
        return True

    @property
    def poll_interval(self) -> int:
        """Return polling interval in seconds."""
        return 1

    @staticmethod
    def _parse_stream_id(source_input: str | None) -> int:
        if not source_input:
            return -1
        match = re.search(r"stream=(\d+)", source_input)
        return int(match.group(1)) if match else -1

    def _current_media_uri(self) -> str | None:
        """Return current media URI when known."""
        media = self._attr_current_media
        if media is not None:
            return getattr(media, "uri", None)
        return self._last_media_uri

    def _queue_tail_reached(self) -> bool:
        """Best-effort signal that current queue item is at/near end and no next item exists."""
        try:
            queue = self.mass.player_queues.get(self.player_id)
        except Exception:  # noqa: BLE001
            return False
        if not queue:
            return False

        next_item = getattr(queue, "next_item", None)
        current_item = getattr(queue, "current_item", None)
        if next_item is not None or current_item is None:
            return False

        try:
            elapsed = float(getattr(queue, "elapsed_time", 0) or 0)
            duration = float(getattr(current_item, "duration", 0) or 0)
        except (TypeError, ValueError):
            return False
        if duration <= 0:
            return False

        # tolerate small timing jitter at track tail
        return elapsed >= (duration - 1.0)

    async def _set_zone(self, payload: dict[str, Any]) -> None:
        """Patch zone values with fallback to POST."""
        try:
            await self.provider._request(self.host, "PATCH", f"/api/zones/{self.zone_id}", payload)
        except Exception:
            await self.provider._request(self.host, "POST", f"/api/zones/{self.zone_id}", payload)

    async def _exec_stream_cmd(self, cmd: str) -> bool:
        """Execute a stream command when available/supported."""
        if self.last_stream_id < 0:
            return False
        if self.last_stream_supported_cmds and cmd not in self.last_stream_supported_cmds:
            self._log_debug("skip unsupported cmd=%s supported=%s", cmd, self.last_stream_supported_cmds)
            return False
        try:
            await self.provider._request(self.host, "POST", f"/api/streams/{self.last_stream_id}/{cmd}")
            return True
        except Exception as err:  # noqa: BLE001
            self._log_debug("stream cmd failed cmd=%s err=%s", cmd, err)
            return False

    async def _play_with_retry(self, attempts: int = 3, delay_seconds: float = 0.25) -> bool:
        for _ in range(attempts):
            await self.poll()
            if await self._exec_stream_cmd("play"):
                return True
            await asyncio.sleep(delay_seconds)
        return False

    def _sync_child_states_from_leader(self) -> None:
        """Mirror leader playback/media state onto grouped children in MA."""
        for member_id in self._attr_group_members:
            if member_id == self.player_id:
                continue
            member = self.mass.players.get(member_id)
            if not isinstance(member, AmpliPiPlayer):
                continue
            member._attr_playback_state = self._attr_playback_state
            member._attr_current_media = self._attr_current_media
            member._attr_powered = self._attr_powered
            member._attr_elapsed_time = self._attr_elapsed_time
            member._attr_elapsed_time_last_updated = self._attr_elapsed_time_last_updated
            member.update_state()

    def _select_source_for_play(self, sources: list[dict[str, Any]], zones: list[dict[str, Any]]) -> int:
        """Pick a source that avoids pulling in non-group zones when possible."""
        grouped_zone_ids = {self.zone_id}
        for member_id in self._attr_group_members:
            if member_id == self.player_id:
                continue
            member = self.mass.players.get(member_id)
            if isinstance(member, AmpliPiPlayer):
                grouped_zone_ids.add(member.zone_id)

        # Determine which sources are currently used by zones that are NOT in this group.
        external_source_ids: set[int] = set()
        for zone in zones:
            zid = int(zone.get("id", -1))
            sid = int(zone.get("source_id", -1))
            if zid not in grouped_zone_ids and sid >= 0:
                external_source_ids.add(sid)

        # Reuse current source only if it is not feeding external zones.
        if self.last_source_id >= 0 and self.last_source_id not in external_source_ids:
            return self.last_source_id

        # Prefer configured fallback first (commonly source_id=0 for best codec/bitrate support)
        # when it is not feeding external zones.
        if self.preferred_source_id >= 0 and self.preferred_source_id not in external_source_ids:
            return self.preferred_source_id

        # Prefer an idle source that is not currently used by external zones.
        for src in sources:
            sid = int(src.get("id", -1))
            state = str((src.get("info") or {}).get("state", "")).lower()
            if sid >= 0 and sid not in external_source_ids and state in {"stopped", "idle", "none"}:
                return sid

        # Next-best: any source not in use by external zones.
        for src in sources:
            sid = int(src.get("id", -1))
            if sid >= 0 and sid not in external_source_ids:
                return sid

        # Last resort fallback (may also feed other zones if all sources are occupied).
        return self.preferred_source_id

    async def poll(self) -> None:
        """Poll player for updates."""
        data = await self.provider._request(self.host, "GET", "/api")
        zones = data.get("zones", [])
        sources = data.get("sources", [])

        zone = next((z for z in zones if int(z.get("id", -1)) == self.zone_id), None)
        if zone is None:
            self._attr_available = False
            self.update_state()
            return

        if self.firmware_version is None:
            self.firmware_version = self.provider._extract_firmware_version(data)
            if self.firmware_version and self._attr_device_info:
                self._attr_device_info.software_version = self.firmware_version

        self._attr_available = True
        self._attr_volume_muted = bool(zone.get("mute", False))
        self._attr_volume_level = int(max(0.0, min(1.0, float(zone.get("vol_f", 0.5)))) * 100)

        source_id = int(zone.get("source_id", -1))
        source_state = "stopped"
        source_input = None
        stream_supported_cmds: list[str] = []
        if source_id >= 0:
            src = next((s for s in sources if int(s.get("id", -1)) == source_id), None)
            source_state = str((src or {}).get("info", {}).get("state", "stopped")).lower()
            source_input = (src or {}).get("input")

        # MA power model: zone is considered powered when attached to an active source.
        # This keeps Power On/Off UI semantics usable even when we do not set disabled=true.
        self._attr_powered = source_id >= 0 and not bool(zone.get("disabled", False))

        stream_id = self._parse_stream_id(source_input)
        if stream_id >= 0:
            stream = next((s for s in data.get("streams", []) if int(s.get("id", -1)) == stream_id), None)
            stream_supported_cmds = (
                (stream or {}).get("info", {}).get("stream_cmds", [])
                or (stream or {}).get("stream_cmds", [])
                or []
            )

        previous_stream_id = self.last_stream_id
        self.last_source_id = source_id
        self.last_stream_id = stream_id
        self.last_stream_supported_cmds = stream_supported_cmds

        if previous_stream_id != stream_id:
            self._attr_elapsed_time = 0
            self._attr_elapsed_time_last_updated = time.time()
            # New underlying stream context means prior track metadata/progress is stale.
            self._attr_current_media = None

        if not self._attr_powered:
            self._attr_playback_state = PlaybackState.IDLE
        elif source_id < 0:
            self._attr_playback_state = PlaybackState.IDLE
        elif source_state in {"playing", "buffering"}:
            self._attr_playback_state = PlaybackState.PLAYING
        elif source_state in {"paused"}:
            self._attr_playback_state = PlaybackState.PAUSED
        else:
            self._attr_playback_state = PlaybackState.IDLE

        # Keep elapsed timer sane for UI even when AmpliPi API lacks per-track progress.
        now = time.time()
        if self._attr_playback_state == PlaybackState.PLAYING:
            if self._attr_elapsed_time_last_updated:
                self._attr_elapsed_time += max(0, now - self._attr_elapsed_time_last_updated)
            self._attr_elapsed_time_last_updated = now
            # If elapsed drifts past known duration, metadata is stale: clear current track.
            duration = getattr(self._attr_current_media, "duration", None) if self._attr_current_media else None
            if duration and self._attr_elapsed_time > float(duration) + 2:
                self._attr_current_media = None
                self._attr_elapsed_time = 0
                self._attr_elapsed_time_last_updated = now
        else:
            self._attr_elapsed_time_last_updated = now

        # Replay guard:
        # Arm only when queue tail is reached (not generic attached-idle),
        # then detach if same URI comes back/stays after grace period.
        current_media_uri = self._current_media_uri()
        tail_reached = self._queue_tail_reached()
        if (
            source_id >= 0
            and current_media_uri
            and tail_reached
            and self._replay_guard_started_ts == 0.0
        ):
            self._replay_guard_started_ts = now
            self._replay_guard_media_uri = current_media_uri
            self._log_debug(
                "replay_guard armed media_uri=%s reason=queue_tail",
                current_media_uri,
            )
            # In your environment, queue-tail arm consistently occurs after replay starts.
            # Stop immediately to avoid replaying the same track.
            if (now - self._last_pause_cmd_ts) > 75.0:
                self._log_debug(
                    "replay_guard immediate detach on arm media_uri=%s",
                    current_media_uri,
                )
                if self.last_stream_id >= 0:
                    await self.provider._request(
                        self.host,
                        "DELETE",
                        f"/api/streams/{self.last_stream_id}",
                    )
                await self._set_zone({"mute": True})
                await self._set_zone({"source_id": -1})
                self._attr_powered = False
                self._attr_playback_state = PlaybackState.IDLE
                self._attr_elapsed_time = 0
                self._attr_elapsed_time_last_updated = now
                self._attr_current_media = None
                self._replay_guard_started_ts = 0.0
                self._replay_guard_media_uri = None
                self.update_state()
                return

        if self._replay_guard_started_ts > 0.0:
            # cancel guard on natural state changes
            if source_id < 0 or not current_media_uri or current_media_uri != self._replay_guard_media_uri:
                self._replay_guard_started_ts = 0.0
                self._replay_guard_media_uri = None
            # If replay already restarted on the same URI after guard matured,
            # stop it immediately instead of waiting extra time.
            elif (
                self._attr_playback_state == PlaybackState.PLAYING
                and (now - self._replay_guard_started_ts) >= 25.0
                and (now - self._last_pause_cmd_ts) > 75.0
            ):
                self._log_debug(
                    "replay_guard immediate detach on replay media_uri=%s age=%.1fs",
                    self._replay_guard_media_uri,
                    now - self._replay_guard_started_ts,
                )
                if self.last_stream_id >= 0:
                    await self.provider._request(
                        self.host,
                        "DELETE",
                        f"/api/streams/{self.last_stream_id}",
                    )
                await self._set_zone({"mute": True})
                await self._set_zone({"source_id": -1})
                self._attr_powered = False
                self._attr_playback_state = PlaybackState.IDLE
                self._attr_elapsed_time = 0
                self._attr_elapsed_time_last_updated = now
                self._attr_current_media = None
                self._replay_guard_started_ts = 0.0
                self._replay_guard_media_uri = None
                self.update_state()
                return
            elif (now - self._replay_guard_started_ts) >= 30.0 and (now - self._last_pause_cmd_ts) > 75.0:
                self._log_debug(
                    "replay_guard detach media_uri=%s age=%.1fs",
                    self._replay_guard_media_uri,
                    now - self._replay_guard_started_ts,
                )
                await self._set_zone({"source_id": -1})
                self._attr_powered = False
                self._attr_playback_state = PlaybackState.IDLE
                self._attr_elapsed_time = 0
                self._attr_elapsed_time_last_updated = now
                self._attr_current_media = None
                self._replay_guard_started_ts = 0.0
                self._replay_guard_media_uri = None
                self.update_state()
                return

        # Keep grouped follower UI in MA aligned with leader state/media.
        if self._sync_leader:
            leader = self.mass.players.get(self._sync_leader)
            if isinstance(leader, AmpliPiPlayer):
                self._attr_playback_state = leader.playback_state
                self._attr_current_media = leader.current_media
                self._attr_elapsed_time = leader.elapsed_time
                self._attr_elapsed_time_last_updated = leader.elapsed_time_last_updated
                # avoid "Off" while source is active as part of grouped playback
                if source_id >= 0:
                    self._attr_powered = True
        elif source_id >= 0 and self._attr_playback_state == PlaybackState.PLAYING:
            # Self-heal: ensure group members stay attached to leader source during playback.
            # This guards against missing/late set_members calls from MA syncgroup flows.
            for member_id in self._attr_group_members:
                if member_id == self.player_id:
                    continue
                member = self.mass.players.get(member_id)
                if not isinstance(member, AmpliPiPlayer):
                    continue
                if member.last_source_id != source_id or not member.powered:
                    self._log_debug(
                        "poll self-heal attach member=%s to source_id=%s (member_source=%s)",
                        member.player_id,
                        source_id,
                        member.last_source_id,
                    )
                    await member._set_zone({"source_id": source_id, "mute": False, "disabled": False})
                    member._sync_leader = self.player_id
                    member._attr_powered = True
                    member.update_state()

        self.update_state()

    async def power(self, powered: bool) -> None:
        """Power management with group-aware shutdown semantics.

        Notes:
        - If invoked on a grouped child, delegate to leader.
        - On power-off, mute first, then detach from source.
        - For grouped playback, resolve targets from MA state and live AmpliPi source usage.
        """
        self._log_action("power", requested=powered, sync_leader=self._sync_leader)
        # If called on a synced child, delegate to leader for coherent group behavior.
        if self._sync_leader:
            leader = self.mass.players.get(self._sync_leader)
            if isinstance(leader, AmpliPiPlayer):
                self._log_debug("power(%s) delegated to leader=%s", powered, leader.player_id)
                await leader.power(powered)
            return

        if powered:
            # Power-on should not implicitly join an already playing source used by other zones.
            data = await self.provider._request(self.host, "GET", "/api")
            zones = data.get("zones", [])
            sources = data.get("sources", [])
            external_source_ids: set[int] = {
                int(z.get("source_id", -1))
                for z in zones
                if int(z.get("id", -1)) != self.zone_id and int(z.get("source_id", -1)) >= 0
            }

            source = -1
            if self.last_source_id >= 0 and self.last_source_id not in external_source_ids:
                source = self.last_source_id
            elif self.preferred_source_id >= 0 and self.preferred_source_id not in external_source_ids:
                source = self.preferred_source_id
            else:
                for src in sources:
                    sid = int(src.get("id", -1))
                    state = str((src.get("info") or {}).get("state", "")).lower()
                    if sid >= 0 and sid not in external_source_ids and state in {"stopped", "idle", "none"}:
                        source = sid
                        break
                if source < 0:
                    for src in sources:
                        sid = int(src.get("id", -1))
                        if sid >= 0 and sid not in external_source_ids:
                            source = sid
                            break

            if source >= 0:
                await self._set_zone({"source_id": source, "mute": False})
                self._attr_powered = True
            else:
                # No safe source available without joining other active zones.
                self._log_debug("power(True): no free source available; refusing auto-join")
                self._attr_powered = False
        else:
            self._log_debug(
                "power(False) start: last_source_id=%s explicit_group_members=%s",
                self.last_source_id,
                list(self._attr_group_members),
            )
            # Build robust sibling set: explicit members, synced followers and
            # players currently sharing the same source.
            members: list[AmpliPiPlayer] = []
            for mass_player in self.mass.players.all(True, False):
                if not isinstance(mass_player, AmpliPiPlayer):
                    continue
                if mass_player.player_id == self.player_id:
                    continue
                is_explicit_member = mass_player.player_id in self._attr_group_members
                is_synced_follower = mass_player._sync_leader == self.player_id
                shares_source = self.last_source_id >= 0 and mass_player.last_source_id == self.last_source_id
                if is_explicit_member or is_synced_follower or shares_source:
                    members.append(mass_player)

            self._log_debug(
                "power(False) MA-derived target players=%s",
                [m.player_id for m in members],
            )

            # Also include any zones currently on the same source from live AmpliPi state,
            # in case MA grouping metadata is stale.
            live_shared_zone_ids: set[int] = set()
            try:
                data = await self.provider._request(self.host, "GET", "/api")
                zones = data.get("zones", [])
                live_shared_zone_ids = {
                    int(z.get("id", -1))
                    for z in zones
                    if int(z.get("source_id", -1)) == self.last_source_id and int(z.get("id", -1)) >= 0
                }
                for mass_player in self.mass.players.all(True, False):
                    if not isinstance(mass_player, AmpliPiPlayer):
                        continue
                    if mass_player.player_id == self.player_id:
                        continue
                    if mass_player.zone_id in live_shared_zone_ids and mass_player not in members:
                        members.append(mass_player)
                self._log_debug(
                    "power(False) live same-source zone_ids=%s",
                    sorted(live_shared_zone_ids),
                )
            except Exception:
                self._log_debug("power(False) failed to read live zone/source data", exc_info=True)
                pass

            # Build a de-duplicated zone target set for fast bulk operations.
            target_zone_ids = {m.zone_id for m in members}
            target_zone_ids.update(z for z in live_shared_zone_ids if z != self.zone_id)

            # Mute all targets in parallel.
            await asyncio.gather(
                *(self._set_zone_by_id(zid, {"mute": True}) for zid in sorted(target_zone_ids)),
                return_exceptions=True,
            )

            await asyncio.sleep(0.15)
            # Detach all targets in parallel (do not disable zones).
            await asyncio.gather(
                *(
                    self._set_zone_by_id(zid, {"source_id": -1})
                    for zid in sorted(target_zone_ids)
                ),
                return_exceptions=True,
            )

            # Keep MA player model state aligned for mapped members.
            for member in members:
                member._attr_volume_muted = True
                member._attr_powered = False
                member._attr_playback_state = PlaybackState.IDLE
                member._sync_leader = None
                member.update_state()

            # Requested shutdown behavior: mute first, then detach from source.
            self._log_debug("power(False) muting/detaching leader zone_id=%s", self.zone_id)
            await self._set_zone({"mute": True})
            await asyncio.sleep(0.15)
            await self._set_zone({"source_id": -1})
            self._attr_volume_muted = True
            self._attr_powered = False
            self._attr_playback_state = PlaybackState.IDLE
            self._attr_elapsed_time = 0
            self._attr_elapsed_time_last_updated = time.time()
            self._attr_current_media = None
        self.update_state()

    async def volume_set(self, volume_level: int) -> None:
        self._log_action("volume_set", level=volume_level)
        await self._set_zone({"vol_f": max(0.0, min(1.0, volume_level / 100))})
        self._attr_volume_level = volume_level
        self.update_state()

    async def volume_mute(self, muted: bool) -> None:
        self._log_action("volume_mute", muted=muted)
        await self._set_zone({"mute": bool(muted)})
        self._attr_volume_muted = muted
        self.update_state()

    async def play(self) -> None:
        now = time.time()
        # Debounce duplicate play requests: MA may issue rapid repeat PLAY calls
        # while state converges, which can cause stream reconnect/log spam.
        if (
            self._attr_playback_state == PlaybackState.PLAYING
            and not self._attr_volume_muted
            and (now - self._last_play_cmd_ts) < 2.0
        ):
            self._log_debug("skip duplicate play within debounce window")
            return

        self._last_play_cmd_ts = now
        self._log_action("play", stream_id=self.last_stream_id, source_id=self.last_source_id)
        prev_state = self._attr_playback_state
        prev_elapsed = float(self._attr_elapsed_time or 0)
        await self._set_zone({"mute": False})
        await self._play_with_retry()
        self._attr_playback_state = PlaybackState.PLAYING
        # On resume-from-pause, preserve elapsed position for UI continuity.
        if prev_state in (PlaybackState.PAUSED, PlaybackState.PLAYING) and prev_elapsed > 0:
            self._attr_elapsed_time = prev_elapsed
        else:
            self._attr_elapsed_time = 0
        self._attr_elapsed_time_last_updated = time.time()
        self.update_state()
        self._sync_child_states_from_leader()

    async def pause(self) -> None:
        self._log_action("pause", stream_id=self.last_stream_id, source_id=self.last_source_id)
        self._last_pause_cmd_ts = time.time()
        did_pause = await self._exec_stream_cmd("pause")
        if not did_pause:
            await self._set_zone({"mute": True})
        self._attr_playback_state = PlaybackState.PAUSED
        self.update_state()
        self._sync_child_states_from_leader()

    async def stop(self) -> None:
        self._log_action("stop", stream_id=self.last_stream_id, source_id=self.last_source_id)
        # If MA sends STOP shortly after PAUSE (e.g., idle timeout behavior),
        # keep the zone paused and attached instead of hard power-off/detach.
        if (
            self._attr_playback_state == PlaybackState.PAUSED
            and (time.time() - self._last_pause_cmd_ts) < 60.0
        ):
            self._log_debug("stop() ignored due to recent pause grace window")
            self.update_state()
            self._sync_child_states_from_leader()
            return

        # Syncgroup virtual player power-off often routes as STOP to child players,
        # not POWER(False). Mirror shutdown behavior here so all linked zones detach.
        self._log_debug("stop() invoked; applying group-aware mute+detach behavior")
        try:
            await self.power(False)
            return
        except Exception:
            self._log_debug("stop() fallback to legacy stop path due to power(False) failure", exc_info=True)

        await self._exec_stream_cmd("stop")
        await self._set_zone({"source_id": -1})
        self._attr_playback_state = PlaybackState.IDLE
        self._attr_elapsed_time = 0
        self._attr_elapsed_time_last_updated = time.time()
        self._attr_current_media = None
        self.update_state()
        self._sync_child_states_from_leader()

    async def set_members(
        self,
        player_ids_to_add: list[str] | None = None,
        player_ids_to_remove: list[str] | None = None,
    ) -> None:
        """Group/ungroup players by aligning members to this leader's active source."""
        self._log_action(
            "set_members",
            add=player_ids_to_add or [],
            remove=player_ids_to_remove or [],
            leader_source=self.last_source_id,
        )
        # MA may call SET_MEMBERS on any involved player. If this player is currently
        # a synced child, delegate to leader to avoid split-brain group state.
        if self._sync_leader:
            leader = self.mass.players.get(self._sync_leader)
            if isinstance(leader, AmpliPiPlayer):
                await leader.set_members(player_ids_to_add, player_ids_to_remove)
            return

        # determine leader source to share across grouped zones
        leader_source = self.last_source_id if self.last_source_id >= 0 else self.preferred_source_id

        # add members to this leader
        for player_id in player_ids_to_add or []:
            if player_id == self.player_id:
                continue
            member = self.mass.players.get(player_id)
            if not isinstance(member, AmpliPiPlayer):
                continue
            await member._set_zone(
                {
                    "source_id": leader_source,
                    "mute": False,
                }
            )
            member._sync_leader = self.player_id
            member._attr_volume_muted = False
            if player_id not in self._attr_group_members:
                self._attr_group_members.append(player_id)
            member.update_state()

        # remove members from this leader
        for player_id in player_ids_to_remove or []:
            member = self.mass.players.get(player_id)
            if isinstance(member, AmpliPiPlayer):
                member._sync_leader = None
                # Explicit ungroup from this leader: detach the member from this source
                # so AmpliPi no longer keeps that zone on shared playback.
                await member._set_zone({"mute": True})
                await asyncio.sleep(0.1)
                await member._set_zone({"source_id": -1})
                member._attr_playback_state = PlaybackState.IDLE
                member.update_state()
            if player_id in self._attr_group_members:
                self._attr_group_members.remove(player_id)

        # enforce invariant that leader is always in its own members list
        if self.player_id not in self._attr_group_members:
            self._attr_group_members.insert(0, self.player_id)
        self.update_state()
        self._sync_child_states_from_leader()

    async def play_media(self, media: PlayerMedia) -> None:
        """Play media URL/path on selected AmpliPi source."""
        self._log_action("play_media_start", uri=media.uri, sync_leader=self._sync_leader)
        self._last_play_media_start_ts = time.time()
        self._replay_guard_started_ts = 0.0
        self._replay_guard_media_uri = None
        self._last_media_uri = media.uri
        # If play is invoked on a synced child, route to leader so grouped zones
        # are handled coherently.
        if self._sync_leader:
            leader = self.mass.players.get(self._sync_leader)
            if isinstance(leader, AmpliPiPlayer):
                await leader.play_media(media)
            return

        data = await self.provider._request(self.host, "GET", "/api")
        zone = next((z for z in data.get("zones", []) if int(z.get("id", -1)) == self.zone_id), None)
        current_zone_source = int((zone or {}).get("source_id", -1))
        selected_source_id = self._select_source_for_play(
            data.get("sources", []),
            data.get("zones", []),
        )
        self._log_action(
            "play_media_source_selected",
            selected_source_id=selected_source_id,
            current_zone_source=current_zone_source,
            preferred_source_id=self.preferred_source_id,
        )
        play_payload = {
            "media": media.uri,
            "source_id": selected_source_id,
        }
        # Apply startup volume only when beginning a fresh session on this zone/source.
        # Avoid resetting volume on queue track changes/seek operations.
        if current_zone_source < 0 or current_zone_source != selected_source_id:
            play_payload["vol_f"] = self.play_vol_f
        await self.provider._request(
            self.host,
            "POST",
            "/api/play",
            play_payload,
        )
        await self._set_zone({"source_id": selected_source_id, "mute": False})

        # Ensure currently grouped members are explicitly attached to the same source.
        for member_id in self._attr_group_members:
            if member_id == self.player_id:
                continue
            member = self.mass.players.get(member_id)
            if not isinstance(member, AmpliPiPlayer):
                continue
            await member._set_zone({"source_id": selected_source_id, "mute": False})
            member._sync_leader = self.player_id
            member._attr_powered = True
            member.update_state()

        await self._play_with_retry()
        self._last_play_cmd_ts = time.time()
        self._attr_current_media = media
        self._attr_playback_state = PlaybackState.PLAYING
        self._attr_elapsed_time = 0
        self._attr_elapsed_time_last_updated = time.time()
        self.update_state()
        self._sync_child_states_from_leader()
