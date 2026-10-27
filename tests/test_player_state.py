"""Focused state handoff tests without a running Music Assistant server."""

import asyncio
import importlib.util
import sys
import types
import unittest
from enum import Enum
from pathlib import Path


class PlaybackState(Enum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"


class PlayerFeature(Enum):
    POWER = "power"
    VOLUME_SET = "volume_set"
    VOLUME_MUTE = "volume_mute"
    PAUSE = "pause"
    SET_MEMBERS = "set_members"
    PLAY_MEDIA = "play_media"


class PlayerType(Enum):
    PLAYER = "player"


class Player:
    def __init__(self, provider, player_id):
        self.provider = provider
        self.player_id = player_id
        self.mass = provider.mass
        self._attr_current_media = None
        self._attr_playback_state = PlaybackState.IDLE

    def update_state(self):
        pass


class PlayerMedia:
    def __init__(self, uri, source_id):
        self.uri = uri
        self.source_id = source_id


def load_player_class():
    """Supply the MA models needed to import the player under test."""
    models = types.ModuleType("music_assistant_models")
    models.__path__ = []
    models_player = types.ModuleType("music_assistant_models.player")
    models_player.DeviceInfo = lambda **kwargs: types.SimpleNamespace(**kwargs)
    models_enums = types.ModuleType("music_assistant_models.enums")
    models_enums.PlaybackState = PlaybackState
    models_enums.PlayerFeature = PlayerFeature
    models_enums.PlayerType = PlayerType
    assistant = types.ModuleType("music_assistant")
    assistant.__path__ = []
    assistant_models = types.ModuleType("music_assistant.models")
    assistant_models.__path__ = []
    assistant_player = types.ModuleType("music_assistant.models.player")
    assistant_player.Player = Player
    assistant_player.PlayerMedia = PlayerMedia
    for module in (models, models_player, models_enums, assistant, assistant_models, assistant_player):
        sys.modules[module.__name__] = module
    path = Path(__file__).resolve().parents[1] / "ma_provider/amplipi/player.py"
    spec = importlib.util.spec_from_file_location("amplipi_player_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AmpliPiPlayer


AmpliPiPlayer = load_player_class()


class FakeProvider:
    instance_id = "amplipi-test"

    def __init__(self):
        self.mass = types.SimpleNamespace(player_queues=types.SimpleNamespace(get=lambda _: None))
        self.warnings = []
        self.logger = types.SimpleNamespace(
            isEnabledFor=lambda _: False,
            warning=lambda *args: self.warnings.append(args),
        )
        self.source_state = "stopped"
        self.source_info_missing = False
        self.stream_id = 10

    def _extract_firmware_version(self, data):
        return None

    async def _request(self, host, method, path, payload=None):
        if method == "GET":
            return {
                "zones": [{"id": 1, "source_id": 0, "mute": False, "vol_f": 0.2}],
                "sources": [{
                    "id": 0,
                    "input": f"stream={self.stream_id}",
                    "info": None if self.source_info_missing else {"state": self.source_state},
                }],
                "streams": [{"id": self.stream_id}],
            }
        return {}


class PlayerStateTests(unittest.TestCase):
    def make_player(self):
        provider = FakeProvider()
        player = AmpliPiPlayer(provider, "zone-1", "amplipi.local", 1, "Zone 1", 0, 0.2)

        async def no_stream_command():
            return False

        player._play_with_retry = no_stream_command
        return player, provider

    def test_delayed_stream_change_preserves_ma_media_and_queue(self):
        async def scenario():
            player, provider = self.make_player()
            media = PlayerMedia("http://ma/track", "ma-queue-1")
            await player.play_media(media)
            self.assertEqual(player._attr_playback_state, PlaybackState.IDLE)
            self.assertEqual(player.poll_interval, 1)
            provider.stream_id = 11
            provider.source_state = "playing"
            await player.poll()
            self.assertIs(player._attr_current_media, media)
            self.assertEqual(player._attr_active_source, "ma-queue-1")
            self.assertEqual(player._attr_playback_state, PlaybackState.PLAYING)

        asyncio.run(scenario())

    def test_command_success_does_not_fake_playing_state(self):
        async def scenario():
            player, _ = self.make_player()
            await player.play_media(PlayerMedia("http://ma/track", "ma-queue-1"))
            self.assertEqual(player._attr_playback_state, PlaybackState.IDLE)

        asyncio.run(scenario())

    def test_announcement_exit_does_not_reenter_restoration(self):
        async def scenario():
            player, provider = self.make_player()
            provider.source_state = "playing"
            player._announce_source_active = True
            player._resume_after_announce = True
            player._attr_playback_state = PlaybackState.PLAYING
            await asyncio.wait_for(player.poll(), timeout=1)
            self.assertFalse(player._announce_source_active)
            self.assertFalse(player._resume_after_announce)

        asyncio.run(scenario())

    def test_unconfirmed_play_expires_without_stale_queue_state(self):
        async def scenario():
            player, provider = self.make_player()
            await player.play_media(PlayerMedia("http://ma/track", "ma-queue-1"))
            player._pending_media_until = 0.0
            await player.poll()
            self.assertIsNone(player._attr_current_media)
            self.assertIsNone(player._attr_active_source)
            self.assertEqual(len(provider.warnings), 1)

        asyncio.run(scenario())

    def test_missing_optional_source_info_does_not_break_poll(self):
        async def scenario():
            player, provider = self.make_player()
            provider.source_info_missing = True
            await player.poll()
            self.assertEqual(player._attr_playback_state, PlaybackState.IDLE)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
