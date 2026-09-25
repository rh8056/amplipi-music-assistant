# AmpliPi + Music Assistant (Beginner Guide)

This project lets **Music Assistant** control your **AmpliPi zones directly**.

If you are not a Python or Docker developer, that is okay — this README is written as a step-by-step setup guide.

---

## 1) What this project includes

There are two parts in this repo:

1. **Music Assistant provider** (recommended)
   - Path: `ma_provider/amplipi`
   - This is the main thing you should use.

2. **Home Assistant custom integration** (optional)
   - Path: `custom_components/amplipi_music_assistant`
   - Only use this if you specifically want AmpliPi entities in Home Assistant.

If you already use the official **Music Assistant integration in Home Assistant** and you are using
the native AmpliPi provider in Music Assistant, you do **not** need to also install/use the
`amplipi_music_assistant` custom integration.

In short:
- Use native MA provider for AmpliPi control (recommended)
- Use HA custom integration only for HA-specific dashboards/automations

If your goal is just “make MA control AmpliPi reliably,” use the **Music Assistant provider**.

---

## 2) Quick glossary (plain English)

- **Zone** = a room output (Office, Kitchen, etc.)
- **Source** = AmpliPi input channel feeding a zone
- **Group** = multiple zones sharing the same source
- **Power Off in MA (this provider)** = mute + detach source (does **not** disable the zone)
- **Power On in MA (this provider)** = attach source + unmute

---

## 3) Before you start

You should already have:

- Music Assistant running in Docker
- AmpliPi reachable on your network
- Your AmpliPi IP/hostname
- Terminal access to your Docker host

---

## 4) Install the Music Assistant provider (Docker)

### Step A: Put provider files on host

Copy `ma_provider/amplipi` from this repo to your Docker host, for example:

- `/opt/music-assistant/providers/amplipi`

### Step B: Mount provider into MA container

If you already have a compose file, add this volume under `music-assistant`.

If you want a complete starter compose file, use this template:

```yaml
services:
  music-assistant:
    image: ghcr.io/music-assistant/server:latest
    container_name: music-assistant
    restart: unless-stopped
    network_mode: host
    environment:
      - TZ=America/Los_Angeles
    volumes:
      - /opt/music-assistant/data:/data
      - /opt/music-assistant/providers/amplipi:/app/venv/lib/python3.13/site-packages/music_assistant/providers/amplipi
```

If you only need the mount line, here it is:

```yaml
volumes:
  - /opt/music-assistant/data:/data
  - /opt/music-assistant/providers/amplipi:/app/venv/lib/python3.13/site-packages/music_assistant/providers/amplipi
```

> Important: The `python3.13` part may change on future MA images.

To find the exact provider path in your own running container, use:

```bash
docker exec music-assistant python3 -c "import music_assistant, os; print(os.path.join(os.path.dirname(music_assistant.__file__), 'providers'))"
```

Use the printed path on the right-hand side of your bind mount.

### Step C: Restart MA

```bash
docker compose down
docker compose up -d
```

### Step D: Confirm provider is loaded

```bash
docker logs --tail=300 music-assistant | grep -Ei 'Loaded player provider AmpliPi|Initializing AmpliPi provider|amplipi'
```

You should see AmpliPi provider startup lines.

---

## 5) Add provider in Music Assistant UI

1. Open Music Assistant UI
2. Go to **Providers**
3. Click **Add Provider**
4. Select **AmpliPi**
5. Enter host and options

You should then see one MA player per AmpliPi zone.

---

## 6) Settings explained simply

### `source_id` fallback

This is the backup source MA uses when it cannot choose a better source automatically.

Default is **`0`** (AmpliPi Input 1 / Source 1).

Why this default:
- On many AmpliPi installs, Source 1 (`source_id: 0`) is the most capable input path.
- Some higher bitrate/sample-rate content support is better on Source 1 than other sources.
- Using Source 1 first helps avoid playback limitation surprises.

Used for:
- Power On (if no previous source is known)
- New grouped playback when no safe source is found

Current source pick order is:
1. Reuse current source if safe
2. Prefer configured fallback source (default `0`) if safe
3. Then try idle/non-conflicting alternatives

### `play_vol_f`

Startup play volume (0.0 to 1.0). This is only used if a prior volume for the zone/ source is not found. The intent is to start at a slightly lower volume.

Good starting value: `0.2` to `0.35`.

### Debug logging

Use Music Assistant/server log level (`DEBUG`) when troubleshooting this provider.

Recent provider builds include announce-specific debug markers such as:
- `announce_state_entered ...`
- `announce_snapshot_captured ...`
- `announce_state_exited ...`
- `announce_restore_index_*`
- `announce_restore_seek_*`

---

## 7) What to expect in daily use

### Start playback on one zone

MA selects a source and starts playback.

### Add a zone to group

The added zone is attached to leader source and unmuted.

### Remove a zone from group

Removed zone is:
1. muted
2. detached from source

### Power Off a zone/group

Zones are:
1. muted
2. detached from source

No zone is disabled by MA.

If the backing stream is already gone, stream cleanup may return HTTP 404 internally and is treated as expected.

### Power On a zone

Zone is:
1. attached to source (last used or fallback)
2. unmuted

If stream is not currently playing, press Play.

---

## 8) Upgrade checklist (important)

When upgrading Music Assistant Docker image:

1. Pull latest image and restart MA:

```bash
docker compose pull music-assistant
docker compose up -d
```

2. Re-check provider mount path (python version may have changed)
3. Verify provider logs

Quick check:

```bash
docker exec music-assistant sh -c 'ls -la /app/venv/lib/python3.13/site-packages/music_assistant/providers/amplipi'
```

If folder is missing inside container, MA will not show provider in UI.

---

## 9) Common problems and fixes

### Problem: Provider not visible in MA

Likely cause: wrong Docker mount path.

Fix:
- verify mount target in compose
- restart container
- re-check logs

### Problem: Power On never appears in MA

Likely cause: provider reports player as always powered.

Fix:
- update to latest provider in this repo
- restart MA

### Problem: Zone stays on stream after ungroup

Fix:
- update to latest provider
- removed member should mute then detach

### Problem: Provider disappeared after upgrade

Likely cause: Python minor version path changed in container.

Fix:
- update mount target path
- restart MA

---

## 10) Known limitations

These are current practical limitations to be aware of:

1. **Music Assistant native Announce is not reliable with AmpliPi right now**
   - In some flows it can re-trigger playback unexpectedly/loop.
   - Recommendation: avoid MA native announce for now with this provider.

2. **AmpliPi native `/announce` is usable, but MA transport/UI can drift**
   - Audio may continue correctly, while MA progress/track UI can temporarily be out of sync.
   - Provider attempts queue snapshot/restore, but edge cases still exist.

3. **Transport state is inferred from AmpliPi source state**
   - AmpliPi does not expose perfect per-track transport semantics for all flows.
   - During rapid stream swaps, MA UI state may lag behind actual audio for short periods.

4. **Source selection remains heuristic-based**
   - Provider tries to avoid stealing sources used by other zones, but when all sources are busy,
     fallback selection may still not match every household routing preference.

5. **Power-off stream cleanup races are possible**
   - Stream objects may already be removed by the time cleanup runs; this can produce benign 404
     cleanup conditions (handled as expected by current provider builds).

---

## 11) Optional Home Assistant integration

If you want HA-side entities too, there is an optional custom integration at:

- `custom_components/amplipi_music_assistant`

Integration display name:
- **AmpliPi Music Assistant**

Entity ID style:
- `media_player.amplipi_ma_<zone_name>`

If you do not need HA entities, you can skip this completely.

If you are already running the official Music Assistant integration in Home Assistant and your
AmpliPi zones are controlled through native MA provider, this optional integration is usually
not needed.
