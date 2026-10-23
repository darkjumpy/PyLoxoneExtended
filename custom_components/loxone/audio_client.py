"""Read-only status client for audio hosted by a Miniserver Compact.

The Miniserver's main websocket remains the authority for play/pause/volume;
this client only supplements those states with track metadata. The port and
response format need verification against real firmware before release.
"""

from __future__ import annotations

import logging

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
        self._base_url = str(URL.build(scheme="http", host=host, port=AUDIO_PORT))

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
