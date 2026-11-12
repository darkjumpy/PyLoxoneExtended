"""Standalone tests for generic per-entity latest-wins queue (no HA needed)."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import unittest

PATH = Path(__file__).resolve().parents[1] / "custom_components/loxone/air_command_queue_core.py"
spec = spec_from_file_location("air_command_queue_core", PATH)
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
LatestValueQueue = module.LatestValueQueue


class TestLatestValueQueue(unittest.TestCase):
    def test_latest_wins_only_for_the_same_parameter(self):
        q = LatestValueQueue()
        first = q.put("head1", "valve_position", 30, queued_at=1000)
        q.put("head1", "brightness", 75, queued_at=1001)
        q.put("head1", "colour_temperature", 3000, queued_at=1001)
        newest = q.put("head1", "valve_position", 50, queued_at=1002)
        q.put("head2", "valve_position", 15, queued_at=1003)
        self.assertEqual(q.get("head1", "valve_position").value, 50)
        self.assertEqual(q.get("head1", "brightness").value, 75)
        self.assertEqual(q.get("head1", "colour_temperature").value, 3000)
        self.assertEqual(q.get("head2", "valve_position").value, 15)
        self.assertFalse(q.ack("head1", "valve_position", first.revision))
        self.assertTrue(q.ack("head1", "valve_position", newest.revision))
        self.assertIsNone(q.get("head1", "valve_position"))
        self.assertEqual(len(q.by_device("head1")), 2)

    def test_replaced_while_old_command_was_in_flight(self):
        q = LatestValueQueue()
        old = q.put("serial", "valve_position", 30, queued_at=1000)
        new = q.put("serial", "valve_position", 50, queued_at=1001)
        self.assertFalse(q.ack("serial", "valve_position", old.revision))
        self.assertEqual(q.get("serial", "valve_position"), new)
        self.assertTrue(q.ack("serial", "valve_position", new.revision))

    def test_persistence_restore_and_expiry(self):
        q = LatestValueQueue()
        q.put("one", "brightness", 40, queued_at=1000)
        q.put("one", "cct", 2200, queued_at=1100)
        q.put("two", "valve_position", 22, queued_at=80)
        persisted = q.dump()
        restored = LatestValueQueue()
        restored.restore(persisted, now=1200, max_age=500)
        self.assertEqual(restored.get("one", "brightness").value, 40)
        self.assertEqual(restored.get("one", "cct").value, 2200)
        self.assertIsNone(restored.get("two", "valve_position"))
        self.assertEqual(len(restored.expire(150, now=1200)), 1)
        self.assertIsNone(restored.get("one", "brightness"))
        self.assertIsNotNone(restored.get("one", "cct"))

    def test_bad_stored_rows_do_not_fail_loading(self):
        q = LatestValueQueue()
        q.restore([
            {},
            {"device": "a", "parameter": "brightness", "value": "nan", "queued_at": 100},
            {"device": "a", "parameter": "brightness", "value": 10, "queued_at": 100},
            {"device": "a", "parameter": "brightness", "value": 20, "queued_at": 101},
            {"device": "b", "parameter": "cct", "value": 99, "queued_at": 2000},
        ], now=110, max_age=120)
        self.assertEqual(q.get("a", "brightness").value, 20)
        self.assertIsNone(q.get("b", "cct"))


if __name__ == "__main__":
    unittest.main()
