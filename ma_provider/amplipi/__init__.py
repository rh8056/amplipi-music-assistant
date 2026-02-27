"""AmpliPi player provider for Music Assistant (v2.3+)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import ConfigEntryType, ProviderFeature

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
from .provider import AmpliPiPlayerProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ConfigValueType, ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType


SUPPORTED_FEATURES = {
    ProviderFeature.SYNC_PLAYERS,
}


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider instance."""
    return AmpliPiPlayerProvider(mass, manifest, config, SUPPORTED_FEATURES)


async def get_config_entries(
    mass: MusicAssistant,
    instance_id: str | None = None,
    action: str | None = None,
    values: dict[str, ConfigValueType] | None = None,
) -> tuple[ConfigEntry, ...]:
    """Return provider configuration entries."""
    # ruff: noqa: ARG001
    return (
        ConfigEntry(
            key=CONF_HOST,
            type=ConfigEntryType.STRING,
            label="AmpliPi host",
            required=True,
        ),
        ConfigEntry(
            key=CONF_AUTO_ZONES,
            type=ConfigEntryType.BOOLEAN,
            label="Auto-discover all zones",
            required=True,
            default_value=DEFAULT_AUTO_ZONES,
        ),
        ConfigEntry(
            key=CONF_ZONE_ID,
            type=ConfigEntryType.INTEGER,
            label="Single zone ID (when auto-zones is off)",
            required=False,
            default_value=0,
        ),
        ConfigEntry(
            key=CONF_SOURCE_ID,
            type=ConfigEntryType.INTEGER,
            label="Preferred fallback source ID",
            required=True,
            default_value=DEFAULT_SOURCE_ID,
        ),
        ConfigEntry(
            key=CONF_PLAY_VOL_F,
            type=ConfigEntryType.FLOAT,
            label="Play volume fraction",
            required=True,
            default_value=DEFAULT_PLAY_VOL_F,
        ),
        ConfigEntry(
            key=CONF_DEBUG_LOGS,
            type=ConfigEntryType.BOOLEAN,
            label="Enable debug logs",
            required=True,
            default_value=DEFAULT_DEBUG_LOGS,
        ),
    )
