"""Standalone tests for LoxAPP3 Slider and button title matching.

Do not import Home Assistant; compile only the pure XML/title helpers.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
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

    def test_button_alias_updates_immediately_and_emits_one_event_per_press(self):
        path = Path(__file__).resolve().parents[1] / "custom_components/loxone/binary_sensor.py"
        source = ast.parse(path.read_text(encoding="utf-8"))
        klass = next(
            n for n in source.body
            if isinstance(n, ast.ClassDef)
            and n.name == "LoxonePhysicalAirValveBinarySensor"
        )
        method = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "_on_event")
        env = {"datetime": datetime, "timezone": timezone, "monotonic": lambda: 111.0}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), env)
        callback = env["_on_event"]

        events, writes = [], []
        self_obj = types.SimpleNamespace(
            _event_uuids={"physical", "mirrored-event-uuid"},
            _terminal=types.SimpleNamespace(serial="504F94FFFEC51C12", device_uuid="device", uuid="physical"),
            _attr_is_on=False,
            _attr_available=False,
            _attr_extra_state_attributes={},
            _is_button=True,
            _press_count=0,
            _last_press=None,
            _last_poll=0,
            hass=types.SimpleNamespace(bus=types.SimpleNamespace(
                async_fire=lambda name, data: events.append((name, data))
            )),
            async_write_ha_state=lambda: writes.append(True),
        )
        callback(self_obj, types.SimpleNamespace(data={"mirrored-event-uuid": 1.0}))
        self.assertTrue(self_obj._attr_is_on)
        self.assertEqual(self_obj._press_count, 1)
        self.assertIn("last_press", self_obj._attr_extra_state_attributes)
        self.assertEqual(events[0][0], "loxone_air_button_pressed")
        self.assertEqual(events[0][1]["air_serial"], "504F94FFFEC51C12")
        # Duplicate on-state does not emit another press.
        callback(self_obj, types.SimpleNamespace(data={"mirrored-event-uuid": 1.0}))
        self.assertEqual(len(events), 1)
        callback(self_obj, types.SimpleNamespace(data={"mirrored-event-uuid": 0.0}))
        callback(self_obj, types.SimpleNamespace(data={"mirrored-event-uuid": 1.0}))
        self.assertEqual(len(events), 2)
        self.assertEqual(len(writes), 4)

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
