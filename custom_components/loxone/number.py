"""
Loxone Numbers

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import logging

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
from .valve_air import (async_get_air_valves, async_drive_to,
                        async_get_valve_diagnostics)
from .air_command_queue import async_get_air_command_queue
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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
    # Each physical head owns an independent queue parameter: valve_position.
    # The generic queue can also handle brightness, CCT, etc. on future devices.
    queue = await async_get_air_command_queue(hass, miniserver, config_entry)
    unique = {terminal.device_uuid: terminal for terminal in air_valves if terminal.serial}
    for terminal in unique.values():
        serial, battery_powered = terminal.serial, terminal.battery_powered

        async def deliver(value: float, head=serial):
            # The queue always checks the physical head by TechReport first,
            # and dynamically reads its actual BatteryPowered mode.
            # Never trust a stale Loxone project Battery flag for delivery.
            await async_drive_to(
                async_get_clientsession(hass), config_entry, head,
                value, None, already_probed=True,
            )

        queue.register_sender(
            serial, "valve_position", deliver, battery_powered=battery_powered
        )
        entities.append(LoxoneAirValveDirectPositionNumber(
            terminal, queue, miniserver, config_entry,
        ))

    for number_entity in get_all(loxconfig, ["Slider", "ValueSelector"]):
        number_entity = add_room_and_cat_to_value_values(loxconfig, number_entity)
        new_number = LoxoneNumber(**number_entity)
        entities.append(new_number)

    async_add_entities(entities)


class LoxoneAirValveDirectPositionNumber(NumberEntity):
    """Queued manual target, not the actuator's verified mechanical position."""

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
    _attr_assumed_state = True

    def __init__(self, terminal, queue, miniserver, config_entry):
        self._attr_unique_id = f"{terminal.device_uuid}_air_direct_drive_to"
        self._attr_device_info = get_or_create_device(
            terminal.device_uuid, terminal.device_name, "Valve Actuator Air", ""
        )
        self._serial = terminal.serial
        self._queue = queue
        self._miniserver = miniserver
        self._config_entry = config_entry
        self._pairing_battery = terminal.battery_powered
        self._initial_source = "unknown"
        self._parameter = "valve_position"
        pending = queue.get_pending(self._serial, self._parameter)
        self._attr_native_value = (
            pending.value if pending is not None
            else queue.last_ack(self._serial, self._parameter)
        )
        if pending is not None:
            self._initial_source = "queued_target"
        elif self._attr_native_value is not None:
            self._initial_source = "last_acknowledged_request"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._queue.subscribe(
            self._serial, self._parameter, self._refresh_from_queue
        ))
        if self._attr_native_value is None:
            self.async_schedule_update_ha_state(force_refresh=True)

    async def async_update(self):
        """Initialize the slider from estimated settled motor position.

        This is read-only, and only when there is no known HA command.
        The ordinary Loxone actor output is often fixed at 0 despite
        DriveTo motor movements, so never treat that 0 as a position.
        """
        if (
            self._queue.get_pending(self._serial, self._parameter) is not None
            or self._queue.last_ack(self._serial, self._parameter) is not None
            or self._attr_native_value is not None
        ):
            return
        values = await async_get_valve_diagnostics(
            self.hass, self._miniserver, self._config_entry,
            self._serial, self._pairing_battery,
        )
        estimate = values.get("estimated_opening")
        if (
            estimate is not None
            and self._queue.get_pending(self._serial, self._parameter) is None
            and self._queue.last_ack(self._serial, self._parameter) is None
            and self._attr_native_value is None
        ):
            self._attr_native_value = float(estimate)
            self._initial_source = "estimated_idle_motor_position"

    def _refresh_from_queue(self):
        pending = self._queue.get_pending(self._serial, self._parameter)
        if pending is not None:
            self._attr_native_value = pending.value
            self._initial_source = "queued_target"
        elif self._queue.last_ack(self._serial, self._parameter) is not None:
            self._attr_native_value = self._queue.last_ack(
                self._serial, self._parameter
            )
            self._initial_source = "last_acknowledged_request"
        # If queue just expired and there is no acknowledgement, retain
        # the last requested setpoint as assumed rather than forcing zero.
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        pending = self._queue.get_pending(self._serial, self._parameter)
        return {
            "queue_parameter": self._parameter,
            "pending": pending is not None,
            "pending_value": pending.value if pending else None,
            "last_acknowledged_value": self._queue.last_ack(
                self._serial, self._parameter
            ),
            "last_error": self._queue.last_error(self._serial, self._parameter),
            "value_meaning": "assumed target or initial estimated idle position; not confirmed opening",
            "value_source": self._initial_source,
            "wake_detection": "TechReport WkupCnt[] change (experimental)",
        }

    async def async_set_native_value(self, value: float):
        """Replace pending setpoint only for this head's valve_position."""
        from homeassistant.exceptions import HomeAssistantError
        import math

        if (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or int(value) != value
            or not 0 <= value <= 100
        ):
            raise HomeAssistantError("Otwarcie zaworu musi wynosić 0–100%")
        await self._queue.async_enqueue(self._serial, self._parameter, value)


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
