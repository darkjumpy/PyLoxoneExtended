"""Read-only status client for audio hosted by a Miniserver Compact.

The Miniserver's main websocket remains the authority for play/pause/volume;
this client only supplements those states with track metadata. The port and
response format need verification against real firmware before release.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout
from yarl import URL

from .audio_metadata import AudioMetadata, parse_audio_status, parse_player_id

_LOGGER = logging.getLogger(__name__)

AUDIO_PORT = 7091
AUDIO_HTTP_TIMEOUT = ClientTimeout(total=5)


class AudioStatusClient:
    """Query the read-only AudioServer zone status endpoint."""

    def __init__(self, session: ClientSession, host: str) -> None:
        self._session = session
        # Options may contain either a bare LAN IP or a full HTTPS
        # Cloud-DNS URL (required on Miniservers preferring HTTPS).
        # The audio-status service uses its own port and HTTP scheme;
        # never pass "https://..." as URL.build(host=...).
        parsed = urlsplit(host if "://" in host else f"//{host}")
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid Miniserver audio hostname")
        self._base_url = str(
            URL.build(scheme="http", host=parsed.hostname, port=AUDIO_PORT)
        )

    async def async_get_metadata(self, player_id: int) -> AudioMetadata | None:
        """Fetch zone metadata; None means an unreadable or unknown response."""
        if parse_player_id(player_id) is None:
            return None

        url = f"{self._base_url}/audio/{player_id}/status"
        # No credentials are sent to an undocumented endpoint. A server
        # requiring the official encrypted handshake will reject this request.
        async with self._session.get(
            url, timeout=AUDIO_HTTP_TIMEOUT, allow_redirects=False
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
        metadata = parse_audio_status(payload, self._base_url)
        if metadata is None:
            _LOGGER.debug("Unsupported AudioServer status response for zone %s", player_id)
        return metadata
