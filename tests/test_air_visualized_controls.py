"""Standalone tests for LoxAPP3 Slider and button title matching.

Do not import Home Assistant; compile only the pure XML/title helpers.
"""

from __future__ import annotations

import ast
import re
import sys
import types
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET


def _load_pure_helpers():
    path = Path(__file__).resolve().parents[1] / "custom_components/loxone/valve_air.py"
    source = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        "_device_serial",
        "_serial_from_visualization_title",
        "serial_from_ha_valve_slider",
        "serial_from_ha_valve_button",
        "expected_valve_input_name",
        "_direct_target_input",
    }
    nodes = [
        n for n in source.body
        if isinstance(n, ast.FunctionDef) and n.name in names
    ]
    assert len(nodes) == len(names)
    space = {
        "re": re,
        "ET": ET,
        "_LOGGER": types.SimpleNamespace(warning=lambda *args: None),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), space)
    return space


class AirVisualizedControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_pure_helpers()

    def test_exact_serial_in_rewritten_loxone_slider_title(self):
        match = self.api["serial_from_ha_valve_slider"]
        serial = "504F94FFFEC51AEA"
        for title in [
            "HA Valve | 504F94FFFEC51AEA | Value",
            r"HA Valve \_ 504F94FFFEC51AEA \_ Value",
            "HA Valve _ 50:4F:94:FF:FE:C5:1A:EA _ Value",
        ]:
            self.assertEqual(match(title), serial, title)
        self.assertIsNone(match("Dimmer | 504F94FFFEC51AEA | Value"))
        self.assertIsNone(match("HA Valve | 504F94FFFEC51AEA | Color"))
        self.assertIsNone(match("HA Valve | 504F94FFFEC51AEA 504F94FFFEC51B23 | Value"))
        self.assertIsNone(match("HA Valve | NOTASERIAL | Value"))

    def test_button_titles_have_independent_matching(self):
        match_button = self.api["serial_from_ha_valve_button"]
        match_slider = self.api["serial_from_ha_valve_slider"]
        title = r"HA Valve \_ 504F94FFFEC51B34 \_ Button"
        self.assertEqual(match_button(title), "504F94FFFEC51B34")
        self.assertIsNone(match_slider(title))
        self.assertIsNone(match_button("HA Valve | 504F94FFFEC51B34 | Value"))

    def test_virtual_input_discovery_with_escaped_title(self):
        discover = self.api["_direct_target_input"]
        serial = "504F94FFFEC51C12"
        title = r"HA Valve \_ 504F94FFFEC51C12 \_ Value"
        root = ET.fromstring(
            f'<Loxone><C Type="VirtualIn" Title="{title}" U="input-1"/></Loxone>'
        )
        self.assertEqual(discover(root, "Siłownik Air WC góra", serial), "input-1")

    def test_reject_ambiguous_or_digital_inputs(self):
        discover = self.api["_direct_target_input"]
        serial = "504F94FFFEC51C12"
        title = f"HA Valve | {serial} | Value"
        root = ET.fromstring(
            f'<Loxone><C Type="VirtualIn" Title="{title}" U="one" />'
            f'<C Type="VirtualIn" Title="{title}" U="two" /></Loxone>'
        )
        self.assertIsNone(discover(root, "WC", serial))
        root = ET.fromstring(
            f'<Loxone><C Type="VirtualIn" Title="{title}" U="one" Digital="true"/></Loxone>'
        )
        self.assertIsNone(discover(root, "WC", serial))

    def test_only_one_valve_slider_implementation_is_active(self):
        root = Path(__file__).resolve().parents[1] / "custom_components/loxone"
        number = (root / "number.py").read_text(encoding="utf-8")
        binary = (root / "binary_sensor.py").read_text(encoding="utf-8")
        self.assertIn("LoxoneAirValveMappedSlider(", number)
        self.assertIn("serial_from_ha_valve_slider(", number)
        self.assertIn("mapped_devices", number)
        self.assertNotIn("async_drive_to(", number)
        self.assertIn("serial_from_ha_valve_button(", binary)
        self.assertIn("loxone_air_button_pressed", binary)


if __name__ == "__main__":
    unittest.main()
