"""Pure unit tests of physical Air button UUID correlation (no HA required)."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import types
import unittest
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1] / "custom_components/loxone"


def load_helpers():
    path = ROOT / "valve_air.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        "_device_serial", "parse_air_button_event_map",
        "extract_air_valves",
    }
    funcs = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert len(funcs) == len(names), [f.name for f in funcs]
    ns = {
        "re": re, "ET": ET,
        "_LOGGER": types.SimpleNamespace(debug=lambda *a: None),
        "_ANALOG_TYPES": {"LoxAIRAsensor", "LoxAIRAactor"},
        "_DIGITAL_TYPES": {"LoxAIRsensor", "Online"},
        "_VALVE_NAMES": ("siłownik air", "valve"),
        "_POSITION_NAMES": ("valve",),
        "_TEMPERATURE_NAMES": ("temp",),
        "_BATTERY_NAMES": ("bateria",),
        "_RSSI_NAMES": ("rssi",),
        "_LINK_NAMES": ("link quality",),
        "battery_supply_from_attribute": lambda x: x == "true",
        "_air_device_serial": lambda el: ns["_device_serial"](el.get("Serial")),
        "_direct_target_input": lambda root, title, serial: None,
        "_name_contains": lambda label, terms: any(t in label.casefold() for t in terms),
        "_display_unit": lambda el: None,
        "AirValveTerminal": lambda **kwargs: types.SimpleNamespace(**kwargs),
    }
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(path), "exec"), ns)
    return ns


class AirButtonMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load_helpers()

    def test_explicit_single_uuid_works_for_correct_serial(self):
        raw = (
            "50:4F:94:FF:FE:00:00:01="
            "11111111-2222-3333-4444-555555555555"
        )
        self.assertEqual(self.h["parse_air_button_event_map"](raw), {
            "504F94FFFE000001": "11111111-2222-3333-4444-555555555555"
        })

    def test_mapping_may_contain_comments_multiple_heads_and_uppercase(self):
        m = self.h["parse_air_button_event_map"](
            "# head 1\n"
            "504F94FFFE000001=11111111-2222-3333-4444-555555555555\n"
            "\n504F94FFFE000002=AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        )
        self.assertEqual(len(m), 2)
        self.assertEqual(
            m["504F94FFFE000002"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )

    def test_invalid_or_ambiguous_maps_are_rejected(self):
        parse = self.h["parse_air_button_event_map"]
        for text in [
            "0000=something",
            "badline",
            "504F94FFFE000001=not-a-uuid",
            ("504F94FFFE000001=11111111-2222-3333-4444-555555555555\n"
             "504F94FFFE000002=11111111-2222-3333-4444-555555555555"),
            ("504F94FFFE000001=11111111-2222-3333-4444-555555555555\n"
             "504F94FFFE000001=99999999-2222-3333-4444-555555555555"),
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse(text)

    def test_real_program_output_connector_uuid_extracted(self):
        root = (
            '<Loxone><C Type="LoxAIRDevice" U="device-1" '
            'Title="Siłownik Air WC góra" Serial="504F94FFFE000001">'
            '<C Type="LoxAIRsensor" U="physical-button-uuid" Title="Wejście 1">'
            '<Co K="Q" U="11111111-2222-3333-4444-555555555555"/>'
            '<Co K="I1" U="ignore-input-connector"/>'
            '</C></C></Loxone>'
        )
        values = self.h["extract_air_valves"](root.encode())
        self.assertEqual(len(values), 1)
        item = values[0]
        self.assertEqual(item.name, "Wejście 1")
        self.assertEqual(item.uuid, "physical-button-uuid")
        self.assertEqual(
            item.event_uuids, ("11111111-2222-3333-4444-555555555555",)
        )

    def test_setup_has_manual_alias_and_connector_fallback(self):
        binary = (ROOT / "binary_sensor.py").read_text(encoding="utf-8")
        config_flow = (ROOT / "config_flow.py").read_text(encoding="utf-8")
        self.assertIn("terminal.event_uuids", binary)
        self.assertIn("parse_air_button_event_map(", binary)
        self.assertIn("mirrored_events.setdefault", binary)
        self.assertIn('"last_event_uuid"', binary)
        self.assertIn("CONF_AIR_BUTTON_EVENT_MAP", config_flow)


if __name__ == "__main__":
    unittest.main()
