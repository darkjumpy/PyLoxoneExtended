"""Pure-data tests for read-only physical Loxone Valve Actuator Air discovery."""

import struct
from custom_components.loxone.valve_air import (
    _decode_loxcc,
    decode_program,
    extract_air_valves,
    battery_supply_from_attribute,
    linked_terminal_ids,
    _direct_target_input,
    expected_valve_input_name,
    valve_output_to_percent,
    _device_serial,
    _air_device_serial,
    parse_link_quality_report,
    parse_valve_tech_report,
    tech_report_confirms_awake,
    parse_device_report_response,
    parse_drive_to_response,
    estimated_valve_opening,
    air_terminal_poll_interval,
)

PROGRAM = b"""<?xml version="1.0"?>
<Loxone>
  <C Type="LoxAIRDevice" U="device-valve-1" Title="Si\xc5\x82ownik Air Salon" Battery="true" Serial="50:4F:94:AA:BB:CC">
    <C Type="LoxAIRAsensor" U="valve-t" Title="Temperatura">
      <Display Unit="&lt;v.1&gt;\xc2\xb0"/>
    </C>
    <C Type="LoxAIRAactor" U="valve-a" Title="Si\xc5\x82ownik Air Salon"/>
    <C Type="LoxAIRsensor" U="valve-i" Title="Wej\xc5\x9bcie 1"/>
    <C Type="LoxAIRAsensor" U="valve-rssi" Title="RSSI"><Display Unit="&lt;v&gt;dB"/></C>
    <C Type="LoxAIRAsensor" U="valve-link" Title="Link Quality"><Display Unit="&lt;v&gt;%"/></C>
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
    assert {terminal.uuid for terminal in controls} == {"valve-t", "valve-a", "valve-i", "valve-o", "valve-rssi", "valve-link", "valve-b", "valve-c"}
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


def test_direct_target_requires_exact_one_analog_virtual_input():
    from xml.etree import ElementTree as ET
    terminal = next(t for t in extract_air_valves(PROGRAM) if t.device_uuid == "device-valve-1")
    assert terminal.manual_value_uuid == "manual-v"
    assert terminal.serial == "504F94AABBCC"
    other = next(t for t in extract_air_valves(PROGRAM) if t.device_uuid == "device-valve-2")
    assert other.manual_value_uuid is None
    assert _direct_target_input(ET.fromstring(PROGRAM), "unknown") is None
    assert _device_serial("50:4F:94:AA:BB:CC") == "504F94AABBCC"
    assert _device_serial("01:23:45:67:89:AB:CD:EF") == "0123456789ABCDEF"
    assert _device_serial("../Reboot") is None
    assert _device_serial("oops") is None


def test_rssi_and_link_quality_are_measurements_only_when_present():
    terminals = extract_air_valves(PROGRAM)
    rssi = next(t for t in terminals if t.uuid == "valve-rssi")
    assert rssi.category == "rssi"
    assert rssi.unit == "dB"
    lqi = next(t for t in terminals if t.uuid == "valve-link")
    assert lqi.category == "link_quality"
    assert lqi.unit == "%"
    other = [t for t in terminals if t.device_uuid == "device-valve-2"]
    assert not any(t.category in ("rssi", "link_quality") for t in other)


def test_parses_actual_link_quality_report():
    report = """Target: 01:23:45:67:89:ab:cd:ef;
MinRssiRx: -84;
MaxRssiRx: -82;
AvgRssiRx: -83;
MinRssiTx: -83;
MaxRssiTx: -80;
AvgRssiTx: -82;
MinLqiRx %: 84;
MaxLqiRx %: 100;
AvgLqiRx %: 97;
MinLqiTx %: 100;
MaxLqiTx %: 100;
AvgLqiTx %: 100;
Packets: 10;
ErrorRate %: 0; Errors: 0;
"""
    assert parse_link_quality_report(report) == {
        "rssi_rx": -83.0,
        "rssi_tx": -82.0,
        "lqi_rx": 97.0,
        "lqi_tx": 100.0,
        "packets": 10.0,
        "error_rate": 0.0,
    }
    assert parse_link_quality_report("LL Code 200 - command accepted") == {}


def test_tech_report_uses_real_reported_percent_and_ignores_battery_sentinel():
    report = """Serial:0123456789ABCDEF
Pos:2364
StartPos:150
MaxPos:2364
RequestedPos:2364
RequestedPercent:0
DestinationPercent:0
ValveBusy:0
DrvBusy:0
BatteryPowered:0
RefDriveState:successful
Error[#]:0
Batt[%]:127
"""
    assert parse_valve_tech_report(report) == {
        "motor_steps": 2364,
        "start_steps": 150,
        "max_steps": 2364,
        "requested_percent": 0,
        "destination_percent": 0,
        "valve_busy": False,
        "drive_busy": False,
        "battery_powered": False,
        "reference_state": "successful",
        "error_count": 0,
    }
    assert parse_valve_tech_report("RequestedPercent:61\nDestinationPercent:61\nBatteryPowered:1") == {
        "requested_percent": 61,
        "destination_percent": 61,
        "battery_powered": True,
    }
    assert parse_valve_tech_report("RequestedPercent:999\nBatteryPowered:4") == {}


def test_ll_xml_response_and_semicolon_separated_real_tech_report():
    payload = (b'<LL control="dev/sys/wsdevice/0123456789ABCDEF/TechReport" '
               b'value="Serial:0123456789ABCDEF;Pos:2364;StartPos:150;MaxPos:2364;'
               b'RequestedPercent:0;DestinationPercent:0;BatteryPowered:0;'
               b'RefDriveState:successful;Batt[%]:127;" Code="200"/>')
    text = parse_device_report_response(payload)
    assert text is not None
    data = parse_valve_tech_report(text)
    assert data["motor_steps"] == 2364
    assert data["requested_percent"] == 0
    assert data["destination_percent"] == 0
    assert data["reference_state"] == "successful"
    assert data["battery_powered"] is False
    assert "battery_level" not in data
    assert parse_device_report_response(b'<LL Code="401" value="x"/>') is None
    assert parse_device_report_response(b'<html>Unauthorised</html>') is None


def test_drive_to_awake_guard_matches_only_this_physical_head():
    head = "0123456789ABCDEF"
    assert tech_report_confirms_awake(
        f"Serial:{head};UpTime:120s;BatteryPowered:1;", head
    )
    assert tech_report_confirms_awake(
        "Serial:01:23:45:67:89:AB:CD:EF;UpTime:9s;", head
    )
    assert not tech_report_confirms_awake(None, head)
    assert not tech_report_confirms_awake("timeout", head)
    assert not tech_report_confirms_awake(
        "Serial:FEDCBA9876543210;UpTime:9s;", head
    )
    assert not tech_report_confirms_awake("UpTime:9s;", head)


def test_drive_to_requires_explicit_ok_and_exact_response_target():
    from homeassistant.exceptions import HomeAssistantError

    serial = "0123456789ABCDEF"
    expected = "dev/sys/wsdevice/0123456789ABCDEF/DriveTo/50"
    successful = f'<LL control="{expected}" value="ok" Code="200"/>'.encode()
    assert parse_drive_to_response(successful, serial, 50) == "ok"

    for payload in [
        f'<LL control="{expected}" value="timeout" Code="200"/>'.encode(),
        f'<LL control="{expected}" value="Unknown Func" Code="200"/>'.encode(),
        f'<LL control="{expected}" value="ok" Code="401"/>'.encode(),
        b'<LL control="dev/sys/wsdevice/FEDCBA9876543210/DriveTo/50" value="ok" Code="200"/>',
        b'<LL control="dev/sys/wsdevice/0123456789ABCDEF/DriveTo/0" value="ok" Code="200"/>',
        b'<broken>',
    ]:
        try:
            parse_drive_to_response(payload, serial, 50)
        except HomeAssistantError:
            pass
        else:
            raise AssertionError(f"Incorrectly accepted a DriveTo response: {payload!r}")


def test_wake_count_from_real_air_tech_report():
    report = "Serial:0123456789ABCDEF;WkupCnt[]:18;BatteryPowered:1;"
    assert parse_valve_tech_report(report)["wake_count"] == 18
    assert parse_valve_tech_report("WkupCnt[]:not-a-number") == {}



def test_air_serial_from_real_config_ip_eui64():
    # Real Loxone programs frequently place Air serial in IP, not Serial.
    # This is a synthetic public EUI-64 example, not a private hardware ID.
    ip_program = b'''<Loxone>
      <C Type="LoxAIRDevice" U="head-battery" Title="Valve Actuator Air WC"
         IP="FE80000000000000524F94FFFE000001" Battery="true">
        <C Type="LoxAIRAactor" U="battery-output" Title="Valve"/>
        <C Type="Online" U="battery-online"/>
      </C>
      <C Type="LoxAIRDevice" U="head-powered" Title="Valve Actuator Air Salon"
         IP="fe80:0000:0000:0000:524f:94ff:fe00:0002" Battery="true">
        <C Type="LoxAIRAactor" U="powered-output" Title="Valve"/>
      </C>
    </Loxone>'''
    heads = extract_air_valves(ip_program)
    assert {t.device_uuid for t in heads} == {"head-battery", "head-powered"}
    assert all(t.serial == "504F94FFFE000001" for t in heads if t.device_uuid == "head-battery")
    assert all(t.serial == "504F94FFFE000002" for t in heads if t.device_uuid == "head-powered")
    # The project attribute is NOT proof of actual current power.
    assert all(t.battery_powered is True for t in heads)


def test_air_serial_rejects_malformed_or_conflicting_project_fields():
    from xml.etree import ElementTree as ET

    def serial_of(attributes):
        return _air_device_serial(ET.Element("C", attributes))

    assert serial_of({"Serial": "50:4F:94:FF:FE:00:00:01"}) == "504F94FFFE000001"
    assert serial_of({"IP": "FE80000000000000524F94FFFE000001"}) == "504F94FFFE000001"
    assert serial_of({"IP": "FE80000000000000524F94FFFE000001",
                      "Serial": "504F94FFFE000001"}) == "504F94FFFE000001"
    assert serial_of({"IP": "FE80000000000000524F94FFFE000001",
                      "Serial": "504F94FFFE000002"}) is None
    assert serial_of({"IP": "192.168.2.253"}) is None
    assert serial_of({"IP": "FE80000000000000524F94FFFEXXXXXX"}) is None
    assert serial_of({"IP": "20010DB800000000524F94FFFE000001"}) is None
    assert serial_of({"IP": "FE800000000000000000000000000001"}) is None


def test_live_external_supply_is_different_from_project_battery_flag():
    # For a head physically connected to external power, this live value
    # must override an outdated project pairing hint.
    assert parse_valve_tech_report(
        "Serial:504F94FFFE000002;BatteryPowered:0;Batt[%]:127;"
    )["battery_powered"] is False
    assert parse_valve_tech_report(
        "Serial:504F94FFFE000001;BatteryPowered:1;Batt[%]:100;"
    )["battery_powered"] is True


def test_air_terminal_poll_option_validates_range_and_default():
    assert air_terminal_poll_interval({}) == 120
    assert air_terminal_poll_interval({"air_terminal_poll_interval": 5}) == 5
    assert air_terminal_poll_interval({"air_terminal_poll_interval": 600}) == 600
    assert air_terminal_poll_interval({"air_terminal_poll_interval": 1}) == 120
    assert air_terminal_poll_interval({"air_terminal_poll_interval": 601}) == 120
    assert air_terminal_poll_interval({"air_terminal_poll_interval": "nan"}) == 120
    assert air_terminal_poll_interval({"air_terminal_poll_interval": 7.5}) == 120


def test_motor_position_estimate_requires_idle_and_good_reference():
    idle = {
        "motor_steps": 0,
        "max_steps": 2227,
        "valve_busy": False,
        "drive_busy": False,
        "reference_state": "successful",
        "error_count": 0,
    }
    assert estimated_valve_opening(idle) == 100
    idle["motor_steps"] = 1114
    assert estimated_valve_opening(idle) == 50
    idle["motor_steps"] = 2244
    assert estimated_valve_opening(idle) == 0
    idle["valve_busy"] = True
    assert estimated_valve_opening(idle) is None
    idle["valve_busy"] = False
    idle["reference_state"] = "unknown"
    assert estimated_valve_opening(idle) is None
    idle["reference_state"] = "successful"
    idle["error_count"] = 1
    assert estimated_valve_opening(idle) is None



def test_virtual_input_discovery_without_analog_attribute():
    """Loxone Config omits the Analog flag in valid analog VirtualIn."""
    from xml.etree import ElementTree as ET

    name = "Siłownik Air WC góra"
    serial = "504F94FFFEC51C12"
    assert expected_valve_input_name(serial, name) == (
        "HA Valve | 504F94FFFEC51C12 | Value"
    )
    root = ET.fromstring(
        '<Loxone>'
        '<C Type="VirtualIn" Title="HA Valve | 504F94FFFEC51C12 | Value" U="vi1"/>'
        '</Loxone>'
    )
    assert _direct_target_input(root, name, serial) == "vi1"

    # Fall back to prior b4 name-based convention.
    old = ET.fromstring(
        '<Loxone>'
        '<C Type="VirtualIn" Title="HA Valve | Siłownik Air WC góra | Value" U="vi2"/>'
        '</Loxone>'
    )
    assert _direct_target_input(old, name, serial) == "vi2"


def test_virtual_input_rejects_digital_and_duplicates():
    from xml.etree import ElementTree as ET

    name = "Siłownik Air WC góra"
    serial = "504F94FFFEC51C12"
    expected = expected_valve_input_name(serial, name)
    for marker in ['Analog="false"', 'Digital="true"', 'UseAsDigital="1"']:
        root = ET.fromstring(
            f'<Loxone><C Type="VirtualIn" Title="{expected}" U="vi" {marker}/></Loxone>'
        )
        assert _direct_target_input(root, name, serial) is None
    duplicate = ET.fromstring(
        f'<Loxone><C Type="VirtualIn" Title="{expected}" U="a"/>'
        f'<C Type="VirtualIn" Title="{expected}" U="b"/></Loxone>'
    )
    assert _direct_target_input(duplicate, name, serial) is None


def test_valve_setpoint_range_0_to_10_maps_to_0_to_100():
    assert valve_output_to_percent(0) == 0
    assert valve_output_to_percent(3) == 30
    assert valve_output_to_percent(5.5) == 55
    assert valve_output_to_percent(10) == 100
    assert valve_output_to_percent(-0.1) is None
    assert valve_output_to_percent(10.1) is None
