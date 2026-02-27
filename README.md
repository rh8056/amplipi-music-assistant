# AmpliPi Test Home Assistant Integration

Minimal custom `media_player` platform for testing AmpliPi zones in Home Assistant and exposing them to Music Assistant via the Home Assistant players provider.

Now supports Home Assistant **config-entry (UI) setup** with options editing and integration reload.

## Features

- Auto-discover all AmpliPi zones (`auto_zones: true`)
- One HA `media_player` per AmpliPi zone
- Play media via `POST /api/play`
- Transport control via `POST /api/streams/{sid}/{cmd}` (`play`, `pause`, `stop`)
- Dynamic source selection (reuse current source, else first idle source, else fallback source)
- 1-second polling for quick state sync
- Zone/source/stream diagnostics in entity attributes
- Zone-name-based entity IDs and friendly names
- Optional concise debug logs (`debug_logs: true`)

## Configuration (recommended: UI config-entry)

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **AmpliPi Test**
3. Enter host/options:
   - `host`
   - `auto_zones`
   - `zone_id` (used when `auto_zones: false`)
   - `source_id` fallback
   - `play_vol_f`
   - `debug_logs`

After setup, use **Configure** on the integration to adjust options and **Reload** to apply without full core restart.

YAML setup is no longer supported.

## Entity attributes

Useful diagnostics in Developer Tools → States:

- `zone_id`
- `source_id`, `source_name`, `source_input`, `source_state`
- `stream_id`, `stream_name`, `stream_type`
- `source_supported_cmds`, `stream_supported_cmds`
- `disabled`

## Notes / Known behavior

- Config-entry setup is preferred for faster reload/test loops.
- Python code changes may still require HA restart depending on module reload behavior.
- Existing entity registry customizations can override friendly names.
- If names/IDs don’t update as expected, remove old entities and restart.
- This is a testing bridge; long-term ideal architecture is a native Music Assistant provider for full queue/session modeling of AmpliPi source limits.

## Troubleshooting

1. Set `debug_logs: true`
2. Restart HA core
3. Check logs for concise events (selected source, stream id, command success/fallback)

---

## Music Assistant native provider (experimental scaffold)

This repository now also includes an initial Music Assistant player-provider scaffold:

- `ma_provider/amplipi/manifest.json`
- `ma_provider/amplipi/__init__.py`
- `ma_provider/amplipi/constants.py`
- `ma_provider/amplipi/provider.py`
- `ma_provider/amplipi/player.py`

### What it currently does

- Registers an AmpliPi player provider for MA v2.3-style APIs
- Discovers zones from `/api`
- Creates one MA player per zone
- Supports core controls (`play_media`, `play`, `pause`, `stop`, power, volume)
- Uses stream transport commands and source selection/fallback logic similar to HA integration

### Important

This is a **starting scaffold**, not a fully production-hardened MA provider yet.
You’ll likely still want to add:

- richer error handling/reporting
- full sync-group semantics
- queue metadata/state nuances and long-run soak testing

### Installing in Music Assistant (development)

1. Copy `ma_provider/amplipi` into your MA Server providers path (or local custom provider path in your MA dev setup).
2. Restart MA Server.
3. Add/configure provider instance in MA UI with host/options.
4. Verify discovered players and test transport/volume commands.

### Docker install/upgrade notes (important)

For MA `2.7.x` (including `2.7.9`), provider discovery in server runtime scans the internal
`music_assistant/providers` path. If you run MA in Docker and want this custom provider to survive upgrades,
bind-mount the provider folder to the in-container providers path.

Example compose snippet:

```yaml
services:
  music-assistant:
    image: ghcr.io/music-assistant/server:latest
    container_name: music-assistant
    restart: unless-stopped
    network_mode: host
    volumes:
      - /opt/music-assistant/data:/data
      - /opt/music-assistant/providers/amplipi:/app/venv/lib/python3.13/site-packages/music_assistant/providers/amplipi
```

> Note: the Python minor version in the path (`python3.13`) may change on future MA images.
> After upgrades, verify and adjust the mount target if needed.

#### Post-upgrade verification

```bash
docker exec music-assistant sh -c 'ls -la /app/venv/lib/python3.13/site-packages/music_assistant/providers/amplipi'
docker logs --tail=300 music-assistant | grep -Ei 'Loaded player provider AmpliPi|Initializing AmpliPi provider|amplipi'
```

#### Behavior checklist (current provider)

- grouped playback keeps members attached to leader source (self-heal path)
- syncgroup power-off/stop triggers mute-then-detach for all attached zones
- queue skip/seek should not repeatedly reset volume from `play_vol_f`
