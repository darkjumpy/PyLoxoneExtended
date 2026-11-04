"""Experimental coalescing command delivery for sleeping Loxone Air heads.

Generic queue keys are (Air serial, entity/parameter key), not just device:
a future light can independently queue brightness and colour temperature.
Only Valve Actuator Air / DriveTo is registered as a sender in b5.

The wake detector uses a change in WkupCnt[] observed through TechReport.
Whether this faithfully tracks *natural* wake-ups on every firmware is not
yet proven. No blind DriveTo polling is done while the head sleeps.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import defaultdict
from time import monotonic, time
from typing import Awaitable, Callable

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .air_command_queue_core import LatestValueQueue
from .valve_air import _device_serial, _read_device_shell_report, tech_report_confirms_awake

_LOGGER = logging.getLogger(__name__)
POLL_WHILE_PENDING = 10  # seconds, probes MINISERVER, only with pending commands
MAX_PENDING_AGE = 3600   # expire stale heating requests after 1 h
Key = tuple[str, str]
Sender = Callable[[float], Awaitable[None]]


def wake_counter_from_tech_report(report: str | None) -> int | None:
    """Wake sequence reported by real Valve Actuator Air (WkupCnt[])."""
    if not report:
        return None
    match = re.search(r"(?:^|[;\r\n])\s*WkupCnt\[\]\s*:\s*(\d+)", report)
    return int(match.group(1)) if match else None


class AirCommandQueue:
    """Persist pending targets and send once after observing a new Air wake."""

    def __init__(self, hass, config_entry):
        self.hass = hass
        self.config_entry = config_entry
        self.pending = LatestValueQueue()
        self.store = Store(
            hass, 1, f"loxone_extended_air_queue_{config_entry.entry_id}"
        )
        self._senders: dict[Key, Sender] = {}
        self._battery: dict[str, bool | None] = {}
        self._listeners: dict[Key, set[Callable[[], None]]] = defaultdict(set)
        self._tasks: dict[str, asyncio.Task] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._last_ack: dict[Key, float] = {}
        self._errors: dict[Key, str] = {}
        self._wake_seen: dict[str, int] = {}
        self._attempted_wake: dict[Key, int] = {}
        self._attempted_at: dict[Key, float] = {}
        self._attempted_revision: dict[Key, int] = {}
        self._save_lock = asyncio.Lock()
        self._stopped = False

    async def async_load(self):
        raw = await self.store.async_load() or {}
        if isinstance(raw, dict):
            self.pending.restore(
                raw.get("pending", []), now=time(), max_age=MAX_PENDING_AGE
            )
            for row in raw.get("last_ack", []):
                if not isinstance(row, dict):
                    continue
                try:
                    serial = _device_serial(row.get("serial"))
                    parameter = row.get("parameter")
                    value = float(row.get("value"))
                    if serial and isinstance(parameter, str) and parameter and math.isfinite(value):
                        self._last_ack[(serial, parameter)] = value
                except (TypeError, ValueError):
                    continue

    async def _save(self):
        async with self._save_lock:
            await self.store.async_save({
                "pending": self.pending.dump(),
                "last_ack": [
                    {"serial": serial, "parameter": parameter, "value": value}
                    for (serial, parameter), value in self._last_ack.items()
                ],
            })

    def register_sender(
        self, serial: str, parameter: str, sender: Sender,
        *, battery_powered: bool | None,
    ):
        serial = _device_serial(serial)
        if not serial or not parameter or self._stopped:
            raise ValueError("Invalid Air command sender")
        self._senders[(serial, parameter)] = sender
        self._battery[serial] = battery_powered
        self._start_worker(serial)

    def subscribe(self, serial: str, parameter: str, callback: Callable[[], None]):
        key = (serial, parameter)
        self._listeners[key].add(callback)

        def unsubscribe():
            self._listeners[key].discard(callback)

        return unsubscribe

    def _notify(self, key: Key):
        for callback in tuple(self._listeners[key]):
            callback()

    def get_pending(self, serial: str, parameter: str):
        return self.pending.get(serial, parameter)

    def last_ack(self, serial: str, parameter: str) -> float | None:
        return self._last_ack.get((serial, parameter))

    def last_error(self, serial: str, parameter: str) -> str | None:
        return self._errors.get((serial, parameter))

    async def async_enqueue(self, serial: str, parameter: str, value: float):
        """Replace the unsent value of one entity; leave all other keys alone."""
        serial = _device_serial(serial)
        if not serial or (serial, parameter) not in self._senders:
            raise ValueError("No handler registered for this Air parameter")
        if not math.isfinite(value):
            raise ValueError("Value must be finite")
        item = self.pending.put(serial, parameter, value)
        self._errors.pop((serial, parameter), None)
        self._notify((serial, parameter))
        await self._save()
        self._start_worker(serial)
        self._events[serial].set()
        return item

    def _start_worker(self, serial: str):
        if self._stopped or not any(
            (serial, item.parameter) in self._senders
            for item in self.pending.by_device(serial)
        ):
            return
        task = self._tasks.get(serial)
        if task is None or task.done():
            self._events[serial] = asyncio.Event()
            self._tasks[serial] = self.hass.async_create_task(
                self._worker(serial), name="loxone air queued delivery"
            )

    async def _wait(self, serial: str, seconds: float):
        event = self._events[serial]
        try:
            await asyncio.wait_for(event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        event.clear()

    async def _attempt(self, item, wake: int | None):
        key = (item.device, item.parameter)
        sender = self._senders.get(key)
        if sender is None:
            return
        # No two sends of the same parameter in a single observed Air wake.
        if wake is not None:
            if self._attempted_wake.get(key) == wake:
                return
            self._attempted_wake[key] = wake
        elif monotonic() - self._attempted_at.get(key, 0.0) < 300:
            return
        self._attempted_at[key] = monotonic()
        self._attempted_revision[key] = item.revision
        try:
            await sender(item.value)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - retain pending, no blind retries
            self._errors[key] = type(exc).__name__ + ": " + str(exc)[:180]
            self._notify(key)
        else:
            self._last_ack[key] = item.value
            self._errors.pop(key, None)
            # If setpoint changed while old command was in-flight, keep new one.
            self.pending.ack(item.device, item.parameter, item.revision)
            self._notify(key)
            await self._save()

    async def _worker(self, serial: str):
        try:
            while not self._stopped:
                expired = self.pending.expire(MAX_PENDING_AGE)
                if expired:
                    for item in expired:
                        key = (item.device, item.parameter)
                        self._errors[key] = "Żądanie wygasło po godzinie bez potwierdzenia"
                        self._notify(key)
                    await self._save()
                available = [
                    item for item in self.pending.by_device(serial)
                    if (serial, item.parameter) in self._senders
                ]
                if not available:
                    return

                if self._battery.get(serial) is False:
                    for item in available:
                        key = (serial, item.parameter)
                        if self._attempted_revision.get(key) != item.revision:
                            await self._attempt(item, None)
                    await self._wait(serial, POLL_WHILE_PENDING)
                    continue

                # Probe the Miniserver for a real device report. Online status
                # is NOT proof of being awake. Require a *new* WkupCnt[] value
                # before transmitting to a battery head.
                report = await _read_device_shell_report(
                    async_get_clientsession(self.hass), self.config_entry,
                    serial, "TechReport"
                )
                if report and tech_report_confirms_awake(report, serial):
                    wake = wake_counter_from_tech_report(report)
                    if wake is not None:
                        prev = self._wake_seen.get(serial)
                        self._wake_seen[serial] = wake
                        if prev is not None and wake != prev:
                            for item in available:
                                # Snapshot may have been replaced mid-probe.
                                fresh = self.pending.get(serial, item.parameter)
                                if fresh:
                                    await self._attempt(fresh, wake)
                await self._wait(serial, POLL_WHILE_PENDING)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Air command queue stopped unexpectedly")
            # Pending values remain persisted so a reload can resume.
        finally:
            self._tasks.pop(serial, None)

    async def async_stop(self):
        self._stopped = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()


async def async_get_air_command_queue(hass, miniserver, config_entry) -> AirCommandQueue:
    task = getattr(miniserver, "_air_command_queue_task", None)
    if task is None:
        async def load():
            queue = AirCommandQueue(hass, config_entry)
            await queue.async_load()
            return queue

        task = hass.async_create_task(load(), name="loxone air command queue load")
        miniserver._air_command_queue_task = task
    return await task
