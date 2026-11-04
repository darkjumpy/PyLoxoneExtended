"""
Loxone Numbers

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import asyncio
import logging
from time import monotonic

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .const import SENDDOMAIN
from .helpers import (add_room_and_cat_to_value_values, clean_unit, get_all,
                      get_or_create_device)
from .miniserver import get_miniserver_from_hass
from .valve_air import async_get_air_valves, async_drive_to
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Number."""
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
    # One control per physical head, no virtual input or Loxone Config rewiring.
    unique = {terminal.device_uuid: terminal for terminal in air_valves if terminal.serial}
    for terminal in unique.values():
        entities.append(LoxoneAirValveDirectPositionNumber(terminal, config_entry))

    for number_entity in get_all(loxconfig, ["Slider", "ValueSelector"]):
        number_entity = add_room_and_cat_to_value_values(loxconfig, number_entity)
        new_number = LoxoneNumber(**number_entity)
        entities.append(new_number)

    async_add_entities(entities)


class LoxoneAirValveDirectPositionNumber(NumberEntity):
    """Set physical Valve Actuator Air position with the device's DriveTo shell.

    The state is only the last ACKNOWLEDGED request in this HA session, not
    a measured mechanical position. The direct shell bypasses normal
    RequestedPercent/DesiredPercent values in TechReport.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Bezpośrednie otwarcie"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:valve"

    def __init__(self, terminal, config_entry):
        self._attr_unique_id = f"{terminal.device_uuid}_air_direct_drive_to"
        self._attr_device_info = get_or_create_device(
            terminal.device_uuid, terminal.device_name, "Valve Actuator Air", ""
        )
        self._serial = terminal.serial
        self._config_entry = config_entry
        self._attr_native_value = None
        self._attr_available = True
        self._attr_assumed_state = True
        self._attr_extra_state_attributes = {
            "value_meaning": "last acknowledged DriveTo command (not measured position)",
            "unconfirmed_on_timeout": True,
            "protocol": "experimental_air_device_shell",
        }
        self._lock = asyncio.Lock()
        self._last_attempt = None

    async def async_set_native_value(self, value: float):
        """Issue one command; never auto-retry on timeout or sleeping Air."""
        async with self._lock:
            now = monotonic()
            if self._last_attempt is not None and now - self._last_attempt < 15:
                raise HomeAssistantError(
                    "Odczekaj co najmniej 15 sekund między poleceniami DriveTo"
                )
            self._last_attempt = now
            await async_drive_to(
                async_get_clientsession(self.hass), self._config_entry,
                self._serial, value,
            )
            # Successful LL 'ok' confirms command reception only.
            self._attr_native_value = float(value)
            self.async_write_ha_state()


class LoxoneNumber(LoxoneEntity, NumberEntity):
    """Representation of a loxone number"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        """Initialize the Loxone number."""
        self._state = STATE_UNKNOWN
        self._icon = None
        self._assumed = False
        # A Slider carries min, max and step in its details. A ValueSelector (the
        # Up-Down Buttons block) publishes them as states instead, so they start
        # from placeholders and follow the state events.
        details = kwargs["details"]
        self._native_max_value = details.get("max", 100)
        self._native_min_value = details.get("min", 0)
        self._native_step = details.get("step", 1)
        if unit := clean_unit(details.get("format", "")):
            self._attr_native_unit_of_measurement = unit

        self.type = kwargs.get("type", "Slider")
        self._attr_device_info = get_or_create_device(
            kwargs.get("parent_id") or self.unique_id, self.name, self.type, self.room
        )

    @property
    def should_poll(self):
        """No polling needed for a demo number."""
        return False

    @property
    def icon(self):
        """Return the icon to use for device if any."""
        return self._icon

    @property
    def native_max_value(self):
        """Return the native_max_value to use for device if any."""
        return self._native_max_value

    @property
    def native_min_value(self):
        """Return the native_min_value to use for device if any."""
        return self._native_min_value

    @property
    def native_step(self):
        """Return the native_min_value to use for device if any."""
        return self._native_step

    @property
    def assumed_state(self):
        """Return if the state is based on assumptions."""
        return self._assumed

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._state

    async def event_handler(self, e):
        if self.type == "ValueSelector":
            for key, attr in (("min", "_native_min_value"), ("max", "_native_max_value"),
                              ("step", "_native_step")):
                uuid = self.states.get(key)
                if uuid in e.data:
                    setattr(self, attr, e.data[uuid])
                    self.schedule_update_ha_state()
        value_uuid = self.states.get("value", self.uuidAction)
        if value_uuid in e.data:
            data = e.data[value_uuid]
            if isinstance(data, (list, dict)):
                data = str(data)
                if len(data) >= 255:
                    self._state = data[:255]
                else:
                    self._state = data
            else:
                self._state = data

            self.schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes.

        Implemented by platform classes.
        """
        return {
            **self._attr_extra_state_attributes,
            "state_uuid": self.states["value"],
            "device_type": self.type,
            "platform": "loxone",
        }

    async def async_set_native_value(self, value: float):
        """Set new value."""
        self.hass.bus.async_fire(
            SENDDOMAIN, dict(uuid=self.uuidAction, value="{}".format(value))
        )
        self.async_schedule_update_ha_state()
