"""Normalize read-only Loxone AudioServer status to Home Assistant metadata.

The AudioServer protocol is undocumented and varies between firmware versions.
Keep its shape handling here, rather than coupling media_player to a single
response format. Missing fields stay unknown (None); never fabricate tracks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

@dataclass(frozen=True)
class AudioMetadata:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    image_url: str | None = None
    source: str | None = None
    duration: float | None = None
    position: float | None = None


def parse_player_id(value: Any) -> int | None:
    """Accept only non-negative numeric IDs from AudioZoneV2.details.playerid."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _value(data: dict, *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    result = str(value).strip()
    return result or None


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number < 86400 * 30 else None


def _unwrap(data: Any) -> dict | None:
    """Unwrap a status command result, without depending on its exact prefix."""
    for _ in range(5):
        if isinstance(data, list):
            if not data:
                return None
            data = data[0]
            continue
        if not isinstance(data, dict) or "error" in data:
            return None
        direct_fields = {
            "title", "artist", "album", "coverurl", "coverUrl", "audiotype",
            "audioType", "track", "metadata", "service", "source",
        }
        if any(field in data for field in direct_fields):
            return data
        next_data = None
        for key in ("status_result", "status", "result", "data", "zone"):
            if isinstance(data.get(key), (dict, list)):
                next_data = data[key]
                break
        if next_data is None:
            for key, value in data.items():
                if key.endswith("/status_result") and isinstance(value, (dict, list)):
                    next_data = value
                    break
        if next_data is None:
            return None
        data = next_data
    return None


def _cover_url(cover: Any, server_base_url: str) -> str | None:
    cover = _text(cover)
    if not cover:
        return None
    parsed = urlsplit(cover)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return cover
    if parsed.scheme or cover.startswith("//"):
        return None
    return urljoin(server_base_url.rstrip("/") + "/", cover)


def parse_audio_status(payload: Any, server_base_url: str) -> AudioMetadata | None:
    """Parse common API response envelopes and audio zone track fields.

    Returns None for errors/unknown envelopes; a valid empty metadata object
    instead clears a previous track when the server reports no current media.
    """
    data = _unwrap(payload)
    if data is None:
        return None

    track = _value(data, "track", "metadata")
    if not isinstance(track, dict):
        track = {}
    title = _text(_value(track, "title", "name") or _value(data, "title", "trackTitle"))
    artist = _text(_value(track, "artist", "artists") or _value(data, "artist", "trackArtist"))
    album = _text(_value(track, "album") or _value(data, "album", "albumName"))
    cover = _value(track, "coverurl", "coverUrl", "cover", "artwork", "image")
    if cover is None:
        cover = _value(data, "coverurl", "coverUrl", "cover", "artwork", "image")
    source = _text(_value(data, "source", "service", "serviceName"))

    duration = _seconds(_value(track, "duration") or _value(data, "duration"))
    position = _seconds(_value(data, "time", "position", "currentTime"))
    return AudioMetadata(
        title=title,
        artist=artist,
        album=album,
        image_url=_cover_url(cover, server_base_url),
        source=source,
        duration=duration,
        position=position,
    )
