# Experimental AudioZoneV2 metadata (Miniserver Compact)

This is an early implementation of **PyLoxone Extended**. It preserves the
existing `loxone` domain and the same media player entities.

## What it does

* Reads `AudioZoneV2.details.playerid` from the existing Loxone structure.
* On a **Miniserver Compact** (miniserver type 4), polls
  `http://<miniserver-host>:7091/audio/<playerid>/status` every 20 seconds.
* When the API returns usable JSON, maps title, artist, album, cover,
  source, track duration and position to the **existing** media player.
* Keeps the existing Loxone WebSocket as the authority for playback state,
  volume and media control; if the audio endpoint is unavailable,
  those functions continue to work.

## Important limitations / current validation

The route is documented by third-party reverse engineering, but its response
format and authentication have **not yet been confirmed on a physical
Miniserver Compact**. To avoid leaking credentials to an unverified service,
the first version sends **no username, password or token** to port 7091.
If the server requires authentication, the request will fail safely and only
the old play/pause and volume state will appear. A future change will implement
the supported session authentication after the actual response is confirmed.
This does **not** imply that all sources always provide metadata.

The initial poll uses Home Assistant's polling mechanism rather than a second
WebSocket connection. Real-time AudioServer events will be investigated once
the status endpoint has been verified.

## How to test

1. Use a test Home Assistant or create a full backup first; do not install the
   upstream PyLoxone and this fork simultaneously (both use `loxone`).
2. With a Compact and Loxone audio zone configured, play music via Spotify
   Connect.
3. In Developer Tools -> States, inspect the **existing** `media_player`
   entity for `media_title`, `media_artist`, `media_album_name`,
   `media_image_url` and `source`.
4. If nothing appears, enable debug logging:

   ```yaml
   logger:
     logs:
       custom_components.loxone.audio_client: debug
       custom_components.loxone.audio_metadata: debug
       custom_components.loxone.media_player: debug
   ```

5. Report HTTP status / error type and a **redacted** sample of the JSON
   returned by the local status endpoint. Remove all credentials, tokens,
   identifying names, serial numbers and private URLs before sharing.

Only `media_player.py`, the two new audio modules, metadata/tests and
integration branding are altered. The setup wizard and device IDs remain
unchanged.
