"""Support for Loxone binary sensors."""

from __future__ import annotations

import logging
from datetime import timedelta
from time import monotonic
from typing import Literal, final

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.binary_sensor import (BinarySensorDeviceClass,
                                                    BinarySensorEntity)
from homeassistant.components.sensor import CONF_STATE_CLASS
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (CONF_DEVICE_CLASS, CONF_NAME,
                                 CONF_UNIT_OF_MEASUREMENT, CONF_VALUE_TEMPLATE,
                                 STATE_OFF, STATE_ON, STATE_UNKNOWN)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .const import CONF_ACTIONID, DOMAIN, EVENT, SENDDOMAIN
from .helpers import (add_room_and_cat_to_value_values, get_all,
                      get_or_create_device)
from .miniserver import get_miniserver_from_hass
from .valve_air import (air_terminal_poll_interval, async_get_air_valves,
                        async_read_terminal, linked_terminal_ids)


_LOGGER = logging.getLogger(__name__)
NEW_SENSOR = "binairy_sensors"
DEFAULT_NAME = "Loxone Binary Sensor"

LOXONE_DEVICE_CLASS_MAP: dict[str, BinarySensorDeviceClass] = {
    "presence": BinarySensorDeviceClass.PRESENCE,
    "smoke": BinarySensorDeviceClass.SMOKE,
}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_devices: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Sensor from yaml"""
    value_template = config.get(CONF_VALUE_TEMPLATE)
    if value_template is not None:
        value_template.hass = hass

    # Devices from yaml
    if config != {}:
        # Here setup all Sensors in Yaml-File
        new_sensor = LoxoneCustomBinarySensor(**config)
        async_add_devices([new_sensor])
        return True
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    loxconfig = miniserver.lox_config.json
    entities = []
    air_valves = await async_get_air_valves(hass, miniserver, config_entry)
    air_by_uuid = {terminal.uuid.lower(): terminal for terminal in air_valves}
    visualized = linked_terminal_ids(loxconfig)
    # Do not advertise the old DriveTo delivery queue: Loxone now owns
    # the native Air cycle after the analog Virtual Input changes.
    for terminal in air_valves:
        get_or_create_device(
            terminal.device_uuid, terminal.device_name, "Valve Actuator Air", ""
        )

    for sensor in get_all(loxconfig, "InfoOnlyDigital"):
        sensor = add_room_and_cat_to_value_values(loxconfig, sensor)
        sensor.update({"type": "digital"})
        if air_terminal := air_by_uuid.get(sensor["uuidAction"].lower()):
            sensor["parent_id"] = air_terminal.device_uuid
        entities.append(LoxoneDigitalSensor(**sensor))

    for terminal in air_valves:
        if terminal.kind == "digital" and terminal.uuid.lower() not in visualized:
            entities.append(LoxonePhysicalAirValveBinarySensor(terminal, config_entry))

    for sensor in get_all(loxconfig, "PresenceDetector"):
        sensor = add_room_and_cat_to_value_values(loxconfig, sensor)
        sensor.update({"type": "presence"})
        entities.append(LoxoneDigitalSensor(**sensor))

    for sensor in get_all(loxconfig, "SmokeAlarm"):
        sensor = add_room_and_cat_to_value_values(loxconfig, sensor)
        sensor.update({"type": "smoke"})
        entities.append(LoxoneDigitalSensor(**sensor))

    @callback
    def async_add_binary_sensors(_):
        async_add_entities(_, True)

    miniserver.listeners.append(
        async_dispatcher_connect(
            hass,
            miniserver.async_signal_new_device("sensors"),
            async_add_binary_sensors,
        )
    )
    async_add_entities(entities)


class LoxonePhysicalAirValveBinarySensor(BinarySensorEntity):
    """Physical Air digital terminal with event-first updates and HTTP fallback."""

    # HA's standard poll interval (usually 30s) cannot implement a 5s
    # configured limit. Schedule our own periodic refresh while loaded.
    _attr_should_poll = False

    def __init__(self, terminal, config_entry):
        self._terminal = terminal
        self._config_entry = config_entry
        self._poll_interval = air_terminal_poll_interval(config_entry.options)
        self._last_poll = 0.0
        self._attr_unique_id = terminal.uuid
        self._attr_name = terminal.name
        self._attr_has_entity_name = True
        self._attr_is_on = None
        self._attr_available = False
        self._attr_device_info = get_or_create_device(
            terminal.device_uuid, terminal.device_name, "Valve Actuator Air", ""
        )
        name = terminal.name.casefold()
        if terminal.name == "Online":
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        elif any(word in name for word in ("bateria", "battery", "fehler", "error", "stuck", "blokada")):
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_extra_state_attributes = {
            "loxone_physical_terminal": terminal.uuid,
            "fallback_poll_interval_seconds": self._poll_interval,
            "updates": "Loxone events when available, HTTP fallback",
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Only Loxone state events that contain the *exact terminal UUID*
        # can update this entity. If the Miniserver does not publish the
        # physical terminal in its websocket event feed, HTTP is fallback.
        self.async_on_remove(self.hass.bus.async_listen(EVENT, self._on_event))
        self.async_on_remove(async_track_time_interval(
            self.hass, self._on_poll_timer, timedelta(seconds=self._poll_interval)
        ))
        self.async_schedule_update_ha_state(force_refresh=True)

    def _on_poll_timer(self, _now):
        self.async_schedule_update_ha_state(force_refresh=True)

    def _on_event(self, event):
        raw = event.data.get(self._terminal.uuid)
        if raw is None:
            return
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return
        if value not in (0.0, 1.0):
            return
        self._attr_is_on = value == 1.0
        self._attr_available = True
        # Avoid immediately making a redundant HTTP request.
        self._last_poll = monotonic()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Fetch a fresh terminal state on the configurable fallback timer."""
        now = monotonic()
        if now - self._last_poll < max(1, self._poll_interval * 0.8):
            return
        self._last_poll = now
        value = await async_read_terminal(
            async_get_clientsession(self.hass),
            self._config_entry,
            self._terminal.uuid,
        )
        self._attr_available = value is not None
        if value is not None:
            self._attr_is_on = bool(value)


class LoxoneDigitalSensor(LoxoneEntity, BinarySensorEntity):
    """Representation of a binary Loxone device."""

    _attr_is_on: bool | None = None
    _attr_state: None = None
    _attr_available = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_state = STATE_UNKNOWN
        self._attr_is_on = STATE_UNKNOWN
        self._from_loxone_config = False

        if (
            "type" in kwargs
            and "room" in kwargs
            and "cat" in kwargs
            and hasattr(self, "states")
        ):
            self._from_loxone_config = True
            if self.type == "smoke":
                self._state_uuid = self.states["areAlarmSignalsOff"]
            if self.type == "presence":
                self._state_uuid = self.states["active"]
            elif "active" in self.states:
                self._state_uuid = self.uuidAction
        else:
            self._state_uuid = self.uuidAction

        self._state = STATE_UNKNOWN
        self._format = self._get_format(kwargs.get("details", {}).get("format", ""))
        self._parent_id = kwargs.get("parent_id", None)
        self._on_state = STATE_ON
        self._off_state = STATE_OFF
        self._attr_available = True
        if self.type in LOXONE_DEVICE_CLASS_MAP:
            self._attr_device_class = LOXONE_DEVICE_CLASS_MAP[self.type]
        else:
            self._attr_device_class = None

        device_uuid = self._parent_id or self.unique_id

        if self._from_loxone_config:
            self._attr_device_info = get_or_create_device(
                device_uuid, self.name, self.type, self.room
            )
        else:
            self._attr_device_info = get_or_create_device(
                device_uuid, self.name, self.type, ""
            )

        if self._from_loxone_config:
            self._attr_extra_state_attributes.update(
                {
                    "state_uuid": self._state_uuid,
                    "device_type": self.type,
                }
            )
        else:
            self._attr_extra_state_attributes.update(
                {
                    "device_type": self._attr_device_class,
                }
            )

    async def event_handler(self, e):
        if self._state_uuid in e.data:
            self._state = e.data[self._state_uuid]
            if self._state == 1.0:
                self._state = self._on_state
            else:
                self._state = self._off_state
            if not self._attr_available:
                self._attr_available = True
            self.async_schedule_update_ha_state()

    @final
    @property
    def state(self) -> Literal["on", "off"] | None:
        """Return the state of the binary sensor."""
        if (is_on := self.is_on) is None:
            return None
        return STATE_ON if is_on else STATE_OFF

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor is on."""
        return self._state == self._on_state


class LoxoneCustomBinarySensor(LoxoneEntity, BinarySensorEntity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._name = kwargs["name"]
        self._state = STATE_UNKNOWN
        self._on_state = STATE_ON
        self._off_state = STATE_OFF

        if "uuidAction" in kwargs:
            self.uuidAction = kwargs["uuidAction"]
        else:
            self.uuidAction = ""

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor is on."""
        return self._state == self._on_state

    @property
    def state(self) -> Literal["on", "off"] | None:
        """Return the state of the binary sensor."""
        if (is_on := self.is_on) is None:
            return None
        return STATE_ON if is_on else STATE_OFF

    async def event_handler(self, e):
        if self.uuidAction in e.data:
            data = e.data[self.uuidAction]
            if data == 1.0:
                self._state = self._on_state
            else:
                self._state = self._off_state
            self.async_schedule_update_ha_state()

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name
