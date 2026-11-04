"""Pure-data tests for read-only physical Loxone Valve Actuator Air discovery."""

import struct
from custom_components.loxone.valve_air import (
    _decode_loxcc,
    decode_program,
    extract_air_valves,
    linked_terminal_ids,
)

PROGRAM = b"""<?xml version="1.0"?>
<Loxone>
  <C Type="LoxAIRDevice" U="device-valve-1" Title="Si\xc5\x82ownik Air Salon">
    <C Type="LoxAIRAsensor" U="valve-t" Title="Temperatura">
      <Display Unit="&lt;v.1&gt;\xc2\xb0"/>
    </C>
    <C Type="LoxAIRAactor" U="valve-a" Title="Si\xc5\x82ownik Air Salon"/>
    <C Type="LoxAIRsensor" U="valve-i" Title="Wej\xc5\x9bcie 1"/>
    <C Type="Online" U="valve-o" Title="Status Online"/>
  </C>
  <C Type="LoxAIRDevice" U="device-touch" Title="Touch Air">
    <C Type="LoxAIRAsensor" U="touch-t" Title="Temperatura"/>
  </C>
</Loxone>
"""


def test_extract_air_valve_not_other_air_devices():
    controls = extract_air_valves(PROGRAM)
    assert {terminal.uuid for terminal in controls} == {"valve-t", "valve-a", "valve-i", "valve-o"}
    assert {terminal.device_uuid for terminal in controls} == {"device-valve-1"}
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
