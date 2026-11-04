"""Pure latest-value-wins queue indexed by physical device and parameter.

This module intentionally has no Home Assistant imports, so its replace and
generation-guard semantics can be unit-tested without running HA.
"""

from dataclasses import dataclass
from time import time


@dataclass(frozen=True)
class PendingValue:
    device: str
    parameter: str
    value: float
    revision: int
    queued_at: float


class LatestValueQueue:
    """A separate, coalescing pending command for each (device, parameter)."""

    def __init__(self):
        self._pending: dict[tuple[str, str], PendingValue] = {}
        self._revision = 0

    @staticmethod
    def _key(device: str, parameter: str) -> tuple[str, str]:
        if not device or not parameter:
            raise ValueError("Device and parameter must be non-empty")
        return device, parameter

    def put(
        self, device: str, parameter: str, value: float, *,
        queued_at: float | None = None,
    ) -> PendingValue:
        key = self._key(device, parameter)
        self._revision += 1
        item = PendingValue(
            device, parameter, float(value), self._revision,
            float(time() if queued_at is None else queued_at),
        )
        self._pending[key] = item
        return item

    def get(self, device: str, parameter: str) -> PendingValue | None:
        return self._pending.get(self._key(device, parameter))

    def by_device(self, device: str) -> list[PendingValue]:
        return [v for (d, _), v in self._pending.items() if d == device]

    def ack(self, device: str, parameter: str, revision: int) -> bool:
        """Remove only the version actually acknowledged by the device."""
        key = self._key(device, parameter)
        current = self._pending.get(key)
        if current is None or current.revision != revision:
            return False
        del self._pending[key]
        return True

    def expire(self, max_age_seconds: int, *, now: float | None = None) -> list[PendingValue]:
        clock = time() if now is None else now
        expired = [
            item for item in self._pending.values()
            if clock - item.queued_at >= max_age_seconds
        ]
        for item in expired:
            self.ack(item.device, item.parameter, item.revision)
        return expired

    def dump(self) -> list[dict]:
        return [
            {
                "device": item.device,
                "parameter": item.parameter,
                "value": item.value,
                "queued_at": item.queued_at,
            }
            for item in self._pending.values()
        ]

    def restore(self, rows: list[dict], *, now: float | None = None, max_age: int = 3600):
        """Restore only unexpired commands; later duplicate rows win."""
        clock = time() if now is None else now
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                device, parameter = row["device"], row["parameter"]
                value, queued_at = float(row["value"]), float(row["queued_at"])
                if (
                    not isinstance(device, str) or not isinstance(parameter, str)
                    or not device or not parameter or clock - queued_at >= max_age
                    or queued_at > clock + 300
                ):
                    continue
                from math import isfinite

                if not isfinite(value):
                    continue
                self.put(device, parameter, value, queued_at=queued_at)
            except (KeyError, ValueError, TypeError):
                continue
