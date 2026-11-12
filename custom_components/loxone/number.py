"""
Loxone Numbers

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/PyLoxone
"""

import asyncio
import logging
import math
from datetime import timedelta

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
from .valve_air import (
    air_terminal_poll_interval, async_get_air_valves, async_read_terminal,
    async_set_valve_virtual_input, expected_valve_input_name,
    valve_output_to_percent, serial_from_ha_valve_slider,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
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
    # Loxone publishes analog Virtual Inputs as ordinary Slider controls in
    # LoxAPP3. Prefer their event-backed native entity and reparent that same
    # entity to the physical Air valve. Avoid a second physical slider.
    from collections import defaultdict

    unique = {t.device_uuid: t for t in air_valves}
    head_by_serial = {t.serial: t for t in unique.values() if t.serial}
    ui_sliders = get_all(loxconfig, ["Slider", "ValueSelector"])
    matches: dict[str, list[dict]] = defaultdict(list)
    for control in ui_sliders:
        if control.get("type") != "Slider":
            continue
        serial = serial_from_ha_valve_slider(control.get("name"))
        if serial in head_by_serial:
            matches[serial].append(control)

    selected_controls: dict[int, object] = {}
    for serial, candidates in matches.items():
        if len(candidates) == 1:
            selected_controls[id(candidates[0])] = head_by_serial[serial]
        else:
            _LOGGER.warning(
                "Several Loxone HA Valve sliders match Air serial %s; "
                "refusing ambiguous valve mapping",
                serial,
            )

    mapped_devices = {t.device_uuid for t in selected_controls.values()}
    for terminal in unique.values():
        if terminal.device_uuid not in mapped_devices:
            # Fallback to an existing verified input in the installed
            # program, if the visualization did not publish a Slider.
            entities.append(LoxoneAirValveVirtualInputNumber(terminal, config_entry))

    for raw in ui_sliders:
        serial = serial_from_ha_valve_slider(raw.get("name"))
        if id(raw) in selected_controls:
            terminal = selected_controls[id(raw)]
            enriched = add_room_and_cat_to_value_values(loxconfig, raw)
            entities.append(LoxoneAirValveMappedSlider(terminal, **enriched))
        elif serial in head_by_serial:
            # When >1 Slider matches a head, skip them rather than show
            # independently actionable duplicate valve controls.
            continue
        else:
            enriched = add_room_and_cat_to_value_values(loxconfig, raw)
            entities.append(LoxoneNumber(**enriched))

    async_add_entities(entities)


class LoxoneAirValveVirtualInputNumber(NumberEntity):
    """HA 0–100% setpoint using a named Loxone analog VirtualIn 0–10.

    Home Assistant sends the standard Miniserver input command; Loxone
    itself delivers the actuator output during the next Air cycle.
    No direct Air shell commands, automatic wake polling, or queued DriveTo.
    """

    _attr_has_entity_name = True
    _attr_name = "Otwarcie zadane (HA)"
    _attr_should_poll = False
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:valve"
    _attr_assumed_state = False

    def __init__(self, terminal, config_entry):
        # Reuse the b5/b6 ID so the user's existing HA entity can retain its
        # entity_id. The old DriveTo path is completely removed from this class.
        self._attr_unique_id = f"{terminal.device_uuid}_air_direct_drive_to"
        self._attr_device_info = get_or_create_device(
            terminal.device_uuid, terminal.device_name, "Valve Actuator Air", ""
        )
        self._input_uuid = terminal.manual_value_uuid
        self._expected_input_name = expected_valve_input_name(
            terminal.serial, terminal.device_name
        )
        self._config_entry = config_entry
        self._poll_interval = air_terminal_poll_interval(config_entry.options)
        self._attr_native_value = None
        self._attr_available = bool(self._input_uuid)
        self._lock = asyncio.Lock()
        self._state_source = "unavailable" if not self._input_uuid else "not_read_yet"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self._input_uuid:
            self.async_on_remove(async_track_time_interval(
                self.hass, self._on_poll_timer,
                timedelta(seconds=self._poll_interval),
            ))
            self.async_schedule_update_ha_state(force_refresh=True)

    def _on_poll_timer(self, _now):
        self.async_schedule_update_ha_state(force_refresh=True)

    async def async_update(self):
        if not self._input_uuid:
            return
        async with self._lock:
            raw = await async_read_terminal(
                async_get_clientsession(self.hass),
                self._config_entry, self._input_uuid,
            )
            percent = valve_output_to_percent(raw)
            if percent is not None:
                self._attr_native_value = float(percent)
                self._attr_available = True
                self._state_source = "loxone_virtual_input"
            elif self._attr_native_value is None:
                self._attr_available = False
                self._state_source = "read_failed"
            # Keep last known state on transient read failures.

    @property
    def extra_state_attributes(self):
        return {
            "source": self._state_source,
            "loxone_input_uuid": self._input_uuid,
            "expected_virtual_input_name": self._expected_input_name,
            "loxone_input_range": "0–10",
            "home_assistant_range": "0–100%",
            "poll_interval_seconds": self._poll_interval,
            "control_mode": "loxone_analog_virtual_input",
            "drive_to_disabled": True,
        }

    async def async_set_native_value(self, value: float):
        if not self._input_uuid:
            raise HomeAssistantError(
                "Brak wejścia analogowego w Loxone Config: "
                + self._expected_input_name
            )
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value != int(value)
            or not 0 <= value <= 100
        ):
            raise HomeAssistantError("Otwarcie musi wynosić pełne 0–100%")
        async with self._lock:
            await async_set_valve_virtual_input(
                async_get_clientsession(self.hass),
                self._config_entry, self._input_uuid,
                float(value) / 10,
            )
            self._attr_native_value = float(value)
            self._attr_available = True
            self._state_source = "confirmed_virtual_input_write"
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


class LoxoneAirValveMappedSlider(LoxoneNumber):
    """Reuse the actual UI Slider but show it inside its physical Air valve.

    The underlying Loxone Slider communicates over the existing websocket
    and retains its original unique ID. Only UI display/units are scaled.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:valve"

    def __init__(self, terminal, **kwargs):
        kwargs["parent_id"] = terminal.device_uuid
        kwargs["name"] = "Otwarcie zadane (HA)"
        super().__init__(**kwargs)
        self._attr_device_info = get_or_create_device(
            terminal.device_uuid, terminal.device_name, "Valve Actuator Air", ""
        )
        self._native_max_value = 100
        self._native_min_value = 0
        self._native_step = 1
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._valve_serial = terminal.serial

    @property
    def native_value(self):
        # The true Loxone control state is 0..10; show 0..100 in HA.
        return valve_output_to_percent(self._state)

    @property
    def extra_state_attributes(self):
        base = dict(super().extra_state_attributes)
        base.update({
            "control_mode": "loxone_visualized_virtual_input",
            "loxone_range": "0–10",
            "ha_range": "0–100%",
            "air_serial": self._valve_serial,
            "drive_to_disabled": True,
        })
        return base

    async def async_set_native_value(self, value: float):
        if (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value != int(value)
            or not 0 <= value <= 100
        ):
            raise HomeAssistantError("Otwarcie musi być od 0 do 100%")
        # The original Loxone UI Slider UUID is the supported write target.
        # Loxone then handles the physical actuator in its normal Air cycle.
        self.hass.bus.async_fire(
            SENDDOMAIN,
            {"uuid": self.uuidAction, "value": f"{float(value) / 10:.1f}"},
        )

