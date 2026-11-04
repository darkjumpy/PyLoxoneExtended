"""
Loxone Switches

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

from functools import cached_property
import json
import logging
from time import monotonic

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .const import SENDDOMAIN
from .helpers import (add_room_and_cat_to_value_values, get_all,
                      get_or_create_device)
from .miniserver import get_miniserver_from_hass
from .valve_air import async_get_air_valves, async_read_terminal, linked_terminal_ids
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Switch."""
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
    valves = await async_get_air_valves(hass, miniserver, config_entry)
    manual_devices = {
        terminal.device_uuid: terminal for terminal in valves
        if terminal.manual_value_uuid and terminal.manual_enable_uuid
    }
    visualized = linked_terminal_ids(loxconfig)
    manual_by_uuid = {t.manual_enable_uuid.lower(): t for t in manual_devices.values()}
    for terminal in manual_devices.values():
        if terminal.manual_enable_uuid.lower() not in visualized:
            entities.append(LoxoneAirValveManualModeSwitch(terminal, config_entry))

    for switch_entity in get_all(loxconfig, ["Switch", "TimedSwitch", "Intercom", "IRoomControllerV2", "LightControllerV2"]):
        switch_entity = add_room_and_cat_to_value_values(loxconfig, switch_entity)
        if match := manual_by_uuid.get(switch_entity["uuidAction"].lower()):
            switch_entity["parent_id"] = match.device_uuid

        if switch_entity["type"] in ["Switch"]:
            new_switch = LoxoneSwitch(**switch_entity)
            entities.append(new_switch)

        elif switch_entity["type"] == "TimedSwitch":
            new_switch = LoxoneTimedSwitch(**switch_entity)
            entities.append(new_switch)

        elif switch_entity["type"] == "Intercom":
            if "subControls" in switch_entity:
                for sub_name in switch_entity["subControls"]:
                    subcontol = switch_entity["subControls"][sub_name]

                    _ = subcontol
                    _ = add_room_and_cat_to_value_values(loxconfig, _)
                    _.update(
                        {
                            "name": "{} - {}".format(
                                switch_entity["name"], subcontol["name"]
                            )
                        }
                    )

                    new_switch = LoxoneIntercomSubControl(**_)
                    entities.append(new_switch)
        elif switch_entity["type"] == "IRoomControllerV2":
            states = switch_entity.get("states", {})
            if "overrideEntries" in states:
                override_kwargs = {**switch_entity, "type": "RoomControllerOverride"}
                entities.append(LoxoneRoomControllerOverride(**override_kwargs))
        elif switch_entity["type"] == "LightControllerV2":
            if switch_entity.get("presence", None):
                override_kwargs = {**switch_entity, "type": "PresenceDetectionSwitch"}
                entities.append(LoxoneLightPresenceSwitch(**override_kwargs))
    async_add_entities(entities)


class LoxoneAirValveManualModeSwitch(SwitchEntity):
    """Select AUTO/manual through a dedicated Loxone virtual digital input."""

    _attr_should_poll = True
    _attr_has_entity_name = True
    _attr_name = "Tryb ręczny"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, terminal, config_entry):
        self._attr_unique_id = f"{terminal.device_uuid}_ha_manual_enable"
        self._attr_device_info = get_or_create_device(
            terminal.device_uuid, terminal.device_name, "Valve Actuator Air", ""
        )
        self._control_uuid = terminal.manual_enable_uuid
        self._config_entry = config_entry
        self._attr_is_on = None
        self._attr_available = False
        self._last_poll = 0.0

    async def async_update(self):
        now = monotonic()
        if now - self._last_poll < 30:
            return
        self._last_poll = now
        value = await async_read_terminal(
            async_get_clientsession(self.hass), self._config_entry, self._control_uuid
        )
        self._attr_available = value is not None
        if value is not None:
            self._attr_is_on = bool(value)

    async def async_turn_on(self, **kwargs):
        if not self._attr_available:
            raise ValueError("Loxone manual override input is unavailable")
        self.hass.bus.async_fire(
            SENDDOMAIN, {"uuid": self._control_uuid, "value": "1"}
        )

    async def async_turn_off(self, **kwargs):
        if not self._attr_available:
            raise ValueError("Loxone manual override input is unavailable")
        self.hass.bus.async_fire(
            SENDDOMAIN, {"uuid": self._control_uuid, "value": "0"}
        )


class LoxoneTimedSwitch(LoxoneEntity, SwitchEntity):
    """Representation of a loxone switch"""

    _attr_available = False
    _attr_is_on: bool | None = None
    _attr_state: None = None
    _attr_assumed_state: None = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_state = STATE_UNKNOWN
        self._attr_is_on = STATE_UNKNOWN
        self._icon = None
        self._delay_remain = 0.0
        self._delay_time_total = 0.0

        if "deactivationDelay" in self.states:
            self._deactivation_delay = self.states["deactivationDelay"]
        else:
            self._deactivation_delay = ""

        if "deactivationDelayTotal" in self.states:
            self._deactivation_delay_total = self.states["deactivationDelayTotal"]
        else:
            self._deactivation_delay_total = ""

        self.type = "TimeSwitch"
        self._attr_device_info = get_or_create_device(
            self.unique_id, self.name, self.type, self.room
        )

    @property
    def should_poll(self):
        """No polling needed for a demo switch."""
        return False

    @property
    def icon(self):
        """Return the icon to use for device if any."""
        return self._icon

    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="pulse"))
        self._attr_is_on = True
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Turn the device off."""
        self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="off"))
        self._attr_is_on = False
        self.schedule_update_ha_state()

    async def event_handler(self, e):
        """Handle timed-switch events and update state attributes."""
        data = e.data
        should_update = False

        # If we're currently unavailable but incoming data contains relevant keys,
        # schedule an async update immediately (preserves original behavior).
        if not self._attr_available and (
            self._deactivation_delay in data or self._deactivation_delay_total in data
        ):
            self.async_schedule_update_ha_state()

        if self._deactivation_delay in data:
            # Preserve original comparison to 0.0
            self._attr_is_on = False if data[self._deactivation_delay] == 0.0 else True
            self._delay_remain = int(data[self._deactivation_delay])
            should_update = True

        if self._deactivation_delay_total in data:
            self._delay_time_total = int(data[self._deactivation_delay_total])
            should_update = True

        if should_update:
            # Make entity available if it wasn't and schedule a final update
            if not self._attr_available:
                self._attr_available = True
            self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes.

        Implemented by platform classes.
        """
        state_dict = {
            **self._attr_extra_state_attributes,
            "device_type": self.type,
        }

        if self._attr_is_on == False:
            state_dict.update({"delay_time_total": str(self._delay_time_total)})

        else:
            state_dict.update(
                {
                    "delay": str(self._delay_remain),
                    "delay_time_total": str(self._delay_time_total),
                }
            )
        return state_dict


class LoxoneSwitch(LoxoneEntity, SwitchEntity):
    """Representation of a loxone switch"""

    _attr_available = False
    _attr_is_on: bool | None = None
    _attr_state: None = None
    _attr_assumed_state: None = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._attr_state = STATE_UNKNOWN
        self._attr_is_on = STATE_UNKNOWN

        """Initialize the Loxone switch."""
        self._icon = None
        self._assumed = False

        self.type = "Switch"
        self._attr_device_info = get_or_create_device(
            kwargs.get("parent_id") or self.unique_id, self.name, self.type, self.room
        )

    @property
    def should_poll(self):
        """No polling needed for a demo switch."""
        return False

    @property
    def icon(self):
        """Return the icon to use for device if any."""
        return self._icon

    def turn_on(self, **kwargs):
        """Turn the switch on."""
        if not self._attr_is_on:
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="On"))
            self._attr_is_on = True
            self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Turn the device off."""
        if self._attr_is_on:
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="Off"))
            self._attr_is_on = False
            self.schedule_update_ha_state()

    async def event_handler(self, event):
        if self.uuidAction in event.data or self.states["active"] in event.data:
            if not self._attr_available:
                self.async_schedule_update_ha_state()
            if self.states["active"] in event.data:
                self._attr_is_on = event.data[self.states["active"]]

            if not self._attr_available:
                self._attr_available = True
            self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes.

        Implemented by platform classes.
        """
        return {
            "uuid": self.uuidAction,
            "state_uuid": self.states.get("active", None),
            "room": self.room,
            "category": self.cat,
            "device_type": self.type,
            "platform": "loxone",
        }


class LoxoneIntercomSubControl(LoxoneSwitch):
    def __init__(self, **kwargs):
        LoxoneSwitch.__init__(self, **kwargs)

        self.type = "IntercomSubControl"
        self._attr_device_info = get_or_create_device(
            self.unique_id, self.name, self.type, self.room
        )

    def turn_on(self, **kwargs):
        """Turn the switch on."""
        if not self._attr_is_on:
            self.hass.bus.fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="on"))
            self._attr_is_on = True
            self.schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes.

        Implemented by platform classes.
        """
        return {
            "uuid": self.uuidAction,
            "state_uuid": self.states["active"],
            "room": self.room,
            "category": self.cat,
            "device_type": self.type,
            "platform": "loxone",
        }


class LoxoneRoomControllerOverride(LoxoneEntity, SwitchEntity):
    """Switch to trigger/stop comfort override on IRoomControllerV2."""

    _attr_available = True
    _attr_is_on = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._override_uuid = self.states.get("overrideEntries")
        self._base_name = self.name  # Original IRC name before modification
        self._attr_name = f"{self.name} Comfort Override"
        self.type = "RoomControllerOverride"

        # Use uuidAction (not unique_id) so this groups with the climate entity
        self._attr_device_info = get_or_create_device(
            self.uuidAction, self._base_name, "RoomControllerV2", self.room
        )

    @cached_property
    def unique_id(self) -> str:
        """Return unique ID based on override state UUID."""
        return f"{self.uuidAction}_override"

    def turn_on(self, **kwargs):
        """Trigger comfort override (mode 1)."""
        self.hass.bus.fire(
            SENDDOMAIN, dict(uuid=self.uuidAction, value="override/1")
        )
        self._attr_is_on = True
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Stop the active override."""
        self.hass.bus.fire(
            SENDDOMAIN, dict(uuid=self.uuidAction, value="stopOverride")
        )
        self._attr_is_on = False
        self.schedule_update_ha_state()

    async def event_handler(self, e):
        if self._override_uuid and self._override_uuid in e.data:
            raw = e.data[self._override_uuid]
            try:
                entries = json.loads(raw) if isinstance(raw, str) else raw
                self._attr_is_on = isinstance(entries, list) and len(entries) > 0
            except (json.JSONDecodeError, TypeError):
                self._attr_is_on = False
            self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {
            "uuid": self.uuidAction,
            "room": self.room,
            "device_type": self.type,
            "platform": "loxone",
        }


class LoxoneLightPresenceSwitch(LoxoneSwitch):
    """Representation of a light controller presence detection switch."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, **kwargs):
        self._presence_id = kwargs["states"]["presence"]
        super().__init__(**kwargs)
        self._attr_device_info = get_or_create_device(self.uuidAction, self.name, "LightControllerV2", self.room)
        self.name = f"{self.name} Presence Detection"

    @cached_property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._presence_id

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction + "/presence", value="on"))
        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction + "/presence", value="off"))
        self.async_schedule_update_ha_state()

    async def event_handler(self, event):
        request_update = False
        if self._presence_id in event.data:
            active = event.data[self._presence_id]
            new_state = True if int(active) & 2 else False
            if new_state != self._attr_is_on:
                self._attr_is_on = new_state
                request_update = True
            if not self._attr_available:
                self._attr_available = True

        if request_update:
            self.async_schedule_update_ha_state()
