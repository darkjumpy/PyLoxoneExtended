"""Support for Loxone Audio zone media player."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiohttp import ClientError
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .audio_client import AudioStatusClient
from .audio_metadata import AudioMetadata, parse_player_id
from .const import DEFAULT_AUDIO_ZONE_V2_PLAY_STATE, SENDDOMAIN
from .helpers import add_room_and_cat_to_value_values, get_all, get_or_create_device
from .miniserver import get_miniserver_from_hass

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0
DEFAULT_FORCE_UPDATE = False
SCAN_INTERVAL = timedelta(seconds=20)

# This is the "optimistic" view of supported features and will be returned until the
# actual set of supported feature have been determined (will always be all or a subset
# of these).
SUPPORT_LOXONE_AUDIO_ZONE = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.STOP
)


def _state_uuid(states: dict, name: str) -> str | None:
    """Un-guarded ``states[name]`` indexing crashes the media player platform when a
    structure file omits an attribute; return ``None`` instead."""
    return states.get(name)


def play_state_to_media_player_state(play_state: int) -> MediaPlayerState:
    """Map a Loxone AudioZoneV2 ``playState`` value to the matching HA state.

    Unknown values fall back to ``IDLE`` (a non-playing state): the entity
    must never be left without a state just because the server sent a new
    value we do not know about.
    """
    match play_state:
        case 0:
            return MediaPlayerState.IDLE
        case 1:
            return MediaPlayerState.PAUSED
        case 2:
            return MediaPlayerState.PLAYING
        case -1:
            return MediaPlayerState.OFF
        case _:
            _LOGGER.debug("Unknown playState %r; defaulting to %s", play_state, MediaPlayerState.IDLE)
            return MediaPlayerState.IDLE


def audio_zone_stop_value() -> str:
    """Command value behind the ``STOP`` feature.

    AudioZoneV2 has no ``stop`` sub-command; pausing makes the zone silent,
    which is the closest stop semantic the device offers.
    """
    return "pause"


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Audio zones."""


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Load Loxone Audio zones based on a config entry."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    loxconfig = miniserver.lox_config.json
    entities = []

    for audioZone in get_all(loxconfig, "AudioZoneV2"):
        audioZone = add_room_and_cat_to_value_values(loxconfig, audioZone)
        audioZone.update(
            {
                "hass": hass,
                "audio_host": config_entry.options.get(CONF_HOST),
                "compact_audio": miniserver.miniserver_type == 4,
            }
        )
        entities.append(LoxoneAudioZoneV2(**audioZone))

    async_add_entities(entities)


class LoxoneAudioZoneV2(LoxoneEntity, MediaPlayerEntity):
    """Representation of a AudioZoneV2 Loxone device."""

    def __init__(self, **kwargs):
        _LOGGER.debug("Input AudioZoneV2 uuidAction=%s", kwargs.get("uuidAction"))
        super().__init__(**kwargs)
        self.hass = kwargs["hass"]

        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER
        self._state = play_state_to_media_player_state(DEFAULT_AUDIO_ZONE_V2_PLAY_STATE)
        self._volume = 0
        self._audio_metadata = AudioMetadata()
        details = kwargs.get("details") or {}
        self._audio_player_id = parse_player_id(details.get("playerid")) if isinstance(details, dict) else None
        self._audio_client = None
        if kwargs.get("compact_audio") and self._audio_player_id is not None and kwargs.get("audio_host"):
            self._audio_client = AudioStatusClient(
                async_get_clientsession(self.hass), kwargs["audio_host"]
            )
        self._audio_updated_at = None

        self.type = "AudioZoneV2"
        self._attr_device_info = get_or_create_device(self.unique_id, self.name, self.type, self.room)

    async def event_handler(self, event):
        should_update = False

        if (u := _state_uuid(self.states, "volume")) and u in event.data:
            self._volume = float(event.data[u]) / 100
            should_update = True

        if (u := _state_uuid(self.states, "playState")) and u in event.data:
            self._state = play_state_to_media_player_state(event.data[u])
            should_update = True

        if should_update:
            self.async_schedule_update_ha_state()

    async def async_update(self) -> None:
        """Refresh audio metadata without modifying the Miniserver play state."""
        if self._audio_client is None:
            return
        try:
            metadata = await self._audio_client.async_get_metadata(self._audio_player_id)
        except (ClientError, TimeoutError, ValueError, OSError) as err:
            _LOGGER.debug(
                "Audio metadata not available for %s (zone %s): %s",
                self.name, self._audio_player_id, err,
            )
            return
        if metadata is not None:
            self._audio_metadata = metadata
            self._audio_updated_at = datetime.now(timezone.utc)

    @property
    def media_title(self) -> str | None:
        return self._audio_metadata.title

    @property
    def media_artist(self) -> str | None:
        return self._audio_metadata.artist

    @property
    def media_album_name(self) -> str | None:
        return self._audio_metadata.album

    @property
    def media_image_url(self) -> str | None:
        return self._audio_metadata.image_url

    @property
    def source(self) -> str | None:
        return self._audio_metadata.source

    @property
    def media_duration(self) -> float | None:
        return self._audio_metadata.duration

    @property
    def media_position(self) -> float | None:
        return self._audio_metadata.position

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self._audio_updated_at if self._audio_metadata.position is not None else None

    # properties
    @property
    def state(self) -> MediaPlayerState:
        """Return the playback state."""
        return self._state

    @property
    def volume_level(self) -> float | None:
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Flag media player features that are supported."""
        return SUPPORT_LOXONE_AUDIO_ZONE

    # commands
    async def async_media_play(self) -> None:
        """Send play command to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="play"))
        self.async_schedule_update_ha_state()

    async def async_media_pause(self) -> None:
        """Send pause command to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="pause"))
        self.async_schedule_update_ha_state()

    async def async_media_stop(self) -> None:
        """Send stop command to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=audio_zone_stop_value()))
        self.async_schedule_update_ha_state()

    async def async_media_next_track(self) -> None:
        """Send next track command to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="next"))
        self.async_schedule_update_ha_state()

    async def async_media_previous_track(self) -> None:
        """Send previous track command to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="prev"))
        self.async_schedule_update_ha_state()

    async def async_set_volume_level(self, volume: float) -> None:
        """Send new volume_level to device."""
        volume_int = int(volume * 100)
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=f"volume/{volume_int}"))
        self.async_schedule_update_ha_state()

    async def async_volume_up(self) -> None:
        """Send volume UP to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="volUp"))
        self.async_schedule_update_ha_state()

    async def async_volume_down(self) -> None:
        """Send volume DOWN to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="volDown"))
        self.async_schedule_update_ha_state()