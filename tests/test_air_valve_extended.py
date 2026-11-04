"""Pure-data tests for read-only physical Loxone Valve Actuator Air discovery."""

import struct
from custom_components.loxone.valve_air import (
    _decode_loxcc,
    decode_program,
    extract_air_valves,
    battery_supply_from_attribute,
    linked_terminal_ids,
    _manual_inputs,
    _device_serial,
)

PROGRAM = b"""<?xml version="1.0"?>
<Loxone>
  <C Type="LoxAIRDevice" U="device-valve-1" Title="Si\xc5\x82ownik Air Salon" Battery="true" Serial="50:4F:94:AA:BB:CC">
    <C Type="LoxAIRAsensor" U="valve-t" Title="Temperatura">
      <Display Unit="&lt;v.1&gt;\xc2\xb0"/>
    </C>
    <C Type="LoxAIRAactor" U="valve-a" Title="Si\xc5\x82ownik Air Salon"/>
    <C Type="LoxAIRsensor" U="valve-i" Title="Wej\xc5\x9bcie 1"/>
    <C Type="Online" U="valve-o" Title="Status Online"/>
  </C>
  <C Type="LoxAIRDevice" U="device-valve-2" Title="Valve Actuator Kitchen" Battery="false">
    <C Type="LoxAIRAactor" U="valve-b" Title="Valve Actuator Kitchen"/>
  </C>
  <C Type="LoxAIRDevice" U="device-valve-3" Title="Valve Actuator Office">
    <C Type="LoxAIRAactor" U="valve-c" Title="Valve Actuator Office"/>
  </C>
  <C Type="VirtualIn" U="manual-v" Analog="true" Title="HA Valve | Si\xc5\x82ownik Air Salon | Value"/>
  <C Type="VirtualIn" U="manual-e" Analog="false" Title="HA Valve | Si\xc5\x82ownik Air Salon | Manual"/>
  <C Type="LoxAIRDevice" U="device-touch" Title="Touch Air">
    <C Type="LoxAIRAsensor" U="touch-t" Title="Temperatura"/>
  </C>
</Loxone>
"""


def test_extract_air_valve_not_other_air_devices():
    controls = extract_air_valves(PROGRAM)
    assert {terminal.uuid for terminal in controls} == {"valve-t", "valve-a", "valve-i", "valve-o", "valve-b", "valve-c"}
    assert {terminal.device_uuid for terminal in controls} == {"device-valve-1", "device-valve-2", "device-valve-3"}
    assert next(t for t in controls if t.uuid == "valve-t").unit == "\u00b0C"
    assert next(t for t in controls if t.uuid == "valve-a").category == "position"


def test_known_visualization_controls_deduplicated():
    visible = {
        "controls": {"abc": {"uuidAction": "VALVE-T", "subControls": {"child": {"uuidAction": "VALVE-A"}}}}
    }
    assert {"valve-t", "valve-a"} <= linked_terminal_ids(visible)


def test_lz4_literal_block():
    xml = b"<a/>"
    compressed = bytes([len(xml) << 4]) + xml
    data = struct.pack("<4I", 0xAABBCCEE, len(compressed), len(xml), 0) + compressed
    assert _decode_loxcc(data) == xml
    assert decode_program(data) == xml


def test_bad_program_rejected():
    assert decode_program(b"garbage") is None
    assert extract_air_valves(b"<broken>") == []


def test_pairing_power_mode_is_not_guessed():
    sensors = extract_air_valves(PROGRAM)
    assert all(t.battery_powered is True for t in sensors if t.device_uuid == "device-valve-1")
    assert all(t.battery_powered is False for t in sensors if t.device_uuid == "device-valve-2")
    assert all(t.battery_powered is None for t in sensors if t.device_uuid == "device-valve-3")
    assert battery_supply_from_attribute("true") is True
    assert battery_supply_from_attribute(" FALSE ") is False
    assert battery_supply_from_attribute("0") is False
    assert battery_supply_from_attribute(None) is None
    assert battery_supply_from_attribute("unknown") is None


def test_manual_pair_only_for_exact_analog_and_digital_virtual_inputs():
    from xml.etree import ElementTree as ET
    terminal = next(t for t in extract_air_valves(PROGRAM) if t.device_uuid == "device-valve-1")
    assert terminal.manual_value_uuid == "manual-v"
    assert terminal.manual_enable_uuid == "manual-e"
    assert terminal.serial == "504F94AABBCC"
    other = next(t for t in extract_air_valves(PROGRAM) if t.device_uuid == "device-valve-2")
    assert other.manual_value_uuid is None
    assert other.manual_enable_uuid is None
    assert _manual_inputs(ET.fromstring(PROGRAM), "unknown") == (None, None)
    assert _device_serial("50:4F:94:AA:BB:CC") == "504F94AABBCC"
    assert _device_serial("../Reboot") is None
    assert _device_serial("oops") is None
