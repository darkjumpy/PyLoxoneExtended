"""Read-only physical Valve Actuator Air discovery for PyLoxone Extended.

LoxAPP3 describes UI controls, not the physical Air/Tree device hierarchy.
We inspect the currently installed Loxone program and only enumerate physical
terminals belonging to an identifiable Valve Actuator Air. No valve commands
are sent. Discovery failures must never prevent ordinary PyLoxone startup.

Protocol investigation inspired by the Apache-2.0 licensed Loxone-Integration
project by smartmacherei (https://github.com/smartmacherei/Loxone-Integration).
This implementation is intentionally limited to Loxone Valve Actuator Air.
"""

from __future__ import annotations

import io
import json
import logging
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from urllib.parse import urlsplit

from aiohttp import BasicAuth, ClientSession, ClientTimeout
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)
MAX_PROGRAM_SIZE = 16 * 1024 * 1024

_ANALOG_TYPES = {"LoxAIRAsensor", "LoxAIRAactor"}
_DIGITAL_TYPES = {"LoxAIRsensor", "Online"}
_VALVE_NAMES = ("siłownik air", "silownik air", "valve", "ventil", "stellantrieb")
_TEMPERATURE_NAMES = ("temp", "temperatur")
_BATTERY_NAMES = ("battery", "batterie", "bateria", "akku")
_RSSI_NAMES = ("rssi", "signal strength", "signalstärke", "signalstarke", "siła sygnału", "sila sygnalu")
_LINK_NAMES = ("link quality", "linkquality", "lqi", "jakość połączenia", "jakosc polaczenia")
_POSITION_NAMES = ("siłown", "silown", "valve", "ventil", "stellantrieb")


@dataclass(frozen=True)
class AirValveTerminal:
    """One input or analog output belonging to a physical Air valve."""

    uuid: str
    device_uuid: str
    device_name: str
    name: str
    kind: str
    unit: str | None
    category: str | None = None
    room_uuid: str | None = None
    battery_powered: bool | None = None
    serial: str | None = None
    manual_value_uuid: str | None = None


def _decode_loxcc(data: bytes) -> bytes | None:
    """Decode Loxone's 16-byte-header + LZ4 block program format."""
    if len(data) < 16:
        return None
    magic, _compressed, expected, _checksum = struct.unpack("<4I", data[:16])
    if magic != 0xAABBCCEE or expected > MAX_PROGRAM_SIZE:
        return None
    src = memoryview(data)[16:]
    out = bytearray()
    idx = 0
    try:
        while idx < len(src) and len(out) < expected:
            token = src[idx]
            idx += 1
            literal = token >> 4
            if literal == 15:
                while True:
                    b = src[idx]
                    idx += 1
                    literal += b
                    if b != 255:
                        break
            if idx + literal > len(src) or len(out) + literal > expected:
                return None
            out.extend(src[idx:idx + literal])
            idx += literal
            if idx >= len(src):
                break
            if idx + 2 > len(src):
                return None
            offset = src[idx] | (src[idx + 1] << 8)
            idx += 2
            if not 0 < offset <= len(out):
                return None
            size = (token & 15) + 4
            if token & 15 == 15:
                while True:
                    b = src[idx]
                    idx += 1
                    size += b
                    if b != 255:
                        break
            if len(out) + size > expected:
                return None
            for _ in range(size):
                out.append(out[-offset])
    except (IndexError, ValueError):
        return None
    return bytes(out) if len(out) == expected else None


def decode_program(data: bytes) -> bytes | None:
    """Handle raw XML, LoxCC and Loxone program ZIP files."""
    if data.lstrip().startswith(b"<"):
        return data if len(data) < MAX_PROGRAM_SIZE else None
    if zipfile.is_zipfile(io.BytesIO(data)):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                project = next((n for n in names if n.lower().endswith("sps.loxone")), None)
                if project:
                    info = zf.getinfo(project)
                    if info.file_size > MAX_PROGRAM_SIZE:
                        return None
                    return decode_program(zf.read(project))
                parts = [n for n in names if re.search(r"sps\d*\.loxcc$", n, re.I)]
                if len(parts) == 1 and zf.getinfo(parts[0]).file_size <= MAX_PROGRAM_SIZE:
                    return decode_program(zf.read(parts[0]))
        except (OSError, ValueError, zipfile.BadZipFile):
            return None
        return None
    return _decode_loxcc(data)


def battery_supply_from_attribute(value: str | None) -> bool | None:
    """Read the persisted Loxone Air pairing power mode, not live voltage.

    "Battery=true" in a LoxAIRDevice denotes battery operation.
    Missing or unknown values cannot safely be interpreted as external power.
    """
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    return None


def _device_serial(value: str | None) -> str | None:
    """Normalise verified 6-byte or 8-byte hex Air identifiers for Loxone APIs."""
    if not value:
        return None
    serial = value.replace(":", "").replace("-", "").upper()
    return serial if re.fullmatch(r"(?:[0-9A-F]{12}|[0-9A-F]{16})", serial) else None


# Diagnostics reported by ShellSysFuncs/GetLinkQuality (if accessible through API).
_LINK_FIELDS = {
    "AvgRssiRx": "rssi_rx",
    "AvgRssiTx": "rssi_tx",
    "AvgLqiRx": "lqi_rx",
    "AvgLqiTx": "lqi_tx",
    "ErrorRate": "error_rate",
    "Packets": "packets",
}


def parse_link_quality_report(report: str) -> dict[str, float]:
    """Parse actual GetLinkQuality monitor output without inventing metrics.

    The command is confirmed in Loxone Config, but no official HTTP/WS
    endpoint for its structured return value is verified yet.
    """
    found: dict[str, float] = {}
    for match in re.finditer(
        r"(?:^|[;\r\n])\s*(AvgRssiRx|AvgRssiTx|AvgLqiRx|AvgLqiTx|ErrorRate|Packets)\s*%?\s*:\s*(-?\d+(?:[.,]\d+)?)",
        report,
        re.IGNORECASE,
    ):
        key = next((dest for src, dest in _LINK_FIELDS.items() if src.casefold() == match.group(1).casefold()), None)
        if key is not None:
            found[key] = float(match.group(2).replace(",", "."))
    return found


def _direct_target_input(root: ET.Element, device_name: str) -> str | None:
    """Find a dedicated Loxone VirtualIn for the valve setpoint.

    A physical output cannot be written through documented SPS I/O services.
    The virtual analog input must be connected to the actuator in Loxone
    Config. A unique exact match is required for safe auto-discovery.
    """
    expected = f"HA Valve | {device_name} | Value".casefold()
    matches = []
    for control in root.iter("C"):
        if control.get("Type") != "VirtualIn":
            continue
        title = (control.get("Title") or "").strip().casefold()
        uuid = control.get("U")
        analog = (control.get("Analog") or "false").lower() in ("true", "1")
        if uuid and title == expected and analog:
            matches.append(uuid)
    return matches[0] if len(matches) == 1 else None


def _name_contains(name: str, words: tuple[str, ...]) -> bool:
    low = name.casefold()
    return any(word in low for word in words)


def _display_unit(el: ET.Element) -> str | None:
    display = el.find("Display")
    raw = (display.get("Unit", "") if display is not None else "").strip()
    match = re.fullmatch(r"<v(?:\.\d+)?>(.*)", raw)
    if not match:
        return None
    value = match.group(1).strip()
    if value in ("°", "°C"):
        return "°C"
    return value or None


def extract_air_valves(xml: bytes) -> list[AirValveTerminal]:
    """Discover physical Air valves and their read-only terminals.

    Never identify a valve from a UUID, guessed AirType enum or room name
    alone. A valve-like device title, or a valve-like analog actuator
    inside an Air device, must identify it.
    """
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, UnicodeDecodeError) as err:
        _LOGGER.debug("Unable to parse Loxone project XML: %s", err)
        return []

    found: list[AirValveTerminal] = []
    seen: set[str] = set()
    for device in root.iter("C"):
        if device.get("Type") != "LoxAIRDevice":
            continue
        uuid = device.get("U")
        title = (device.get("Title") or "").strip()
        if not uuid:
            continue
        battery_powered = battery_supply_from_attribute(device.get("Battery"))
        serial = _device_serial(device.get("Serial"))
        manual_value_uuid = _direct_target_input(root, title)
        children = list(device.iter("C"))[1:]
        analog_outputs = [el for el in children if el.get("Type") == "LoxAIRAactor"]
        is_valve = _name_contains(title, _VALVE_NAMES) or any(
            _name_contains((el.get("Title") or "") + " " + (el.get("IName") or ""), _POSITION_NAMES)
            for el in analog_outputs
        )
        if not is_valve:
            continue

        for el in children:
            kind = el.get("Type", "")
            if kind not in _ANALOG_TYPES | _DIGITAL_TYPES:
                continue
            term_uuid = el.get("U")
            if not term_uuid or term_uuid.lower() in seen:
                continue
            label = (el.get("Title") or el.get("IName") or "Wejście").strip()
            if kind == "LoxAIRAactor":
                name = "Wysterowanie zaworu"
                category = "position"
            elif kind in _ANALOG_TYPES and _name_contains(label, _TEMPERATURE_NAMES):
                name = "Temperatura"
                category = "temperature"
            elif kind in _ANALOG_TYPES and _name_contains(label, _BATTERY_NAMES):
                name = "Poziom baterii"
                category = "battery"
            elif kind == "LoxAIRAsensor" and _name_contains(label, _RSSI_NAMES):
                name = "RSSI"
                category = "rssi"
            elif kind == "LoxAIRAsensor" and _name_contains(label, _LINK_NAMES):
                name = "Link Quality"
                category = "link_quality"
            elif kind == "Online":
                name = "Online"
                category = "diagnostic"
            else:
                name = label
                category = "diagnostic" if _name_contains(label, _BATTERY_NAMES) else None

            iod = el.find("IoData")
            room = iod.get("Pr") if iod is not None else None
            unit = _display_unit(el)
            if category == "temperature" and unit is None:
                unit = "°C"
            if category == "battery" and unit is None:
                unit = "%"
            if category == "rssi":
                unit = unit or "dB"
            found.append(
                AirValveTerminal(
                    uuid=term_uuid, device_uuid=uuid, device_name=title or "Valve Actuator Air",
                    name=name, kind="analog" if kind in _ANALOG_TYPES else "digital",
                    unit=unit, category=category, room_uuid=room,
                    battery_powered=battery_powered,
                    serial=serial,
                    manual_value_uuid=manual_value_uuid,
                )
            )
            seen.add(term_uuid.lower())
    return found


def _endpoint(config_entry) -> tuple[str, BasicAuth] | None:
    """Resolve configured Miniserver host and credentials without logging them."""
    opts = config_entry.options
    host = (opts.get(CONF_HOST) or "").strip()
    username, password = opts.get(CONF_USERNAME), opts.get(CONF_PASSWORD)
    if not host or not username or not password:
        return None
    parts = urlsplit(host if "://" in host else "http://" + host)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    port = parts.port or int(opts.get(CONF_PORT) or (443 if parts.scheme == "https" else 80))
    authority = "[" + parts.hostname + "]" if ":" in parts.hostname else parts.hostname
    base = f"{parts.scheme}://{authority}:{port}"
    return base, BasicAuth(str(username), str(password))


async def _fetch_project(session: ClientSession, config_entry) -> bytes | None:
    endpoint = _endpoint(config_entry)
    if endpoint is None:
        return None
    base, auth = endpoint
    timeout = ClientTimeout(total=15)
    try:
        async with session.get(f"{base}/dev/fslist/prog", auth=auth, timeout=timeout) as r:
            if r.status != 200:
                _LOGGER.debug("Air valve discovery: program listing HTTP %s", r.status)
                return None
            listing = await r.text()
        candidates = re.findall(r"\b(sps_\d+_(\d+)\.(LoxCC|zip))\b", listing, re.I)
        if not candidates:
            _LOGGER.debug("Air valve discovery: no LoxCC/ZIP program in listing")
            return None
        chosen = max(candidates, key=lambda part: (int(part[1]), part[2].lower() == "loxcc"))[0]
        async with session.get(f"{base}/dev/fsget/prog/{chosen}", auth=auth, timeout=timeout) as r:
            if r.status != 200:
                _LOGGER.debug("Air valve discovery: program download HTTP %s", r.status)
                return None
            if r.content_length is not None and r.content_length > MAX_PROGRAM_SIZE:
                return None
            raw = await r.read()
            if len(raw) > MAX_PROGRAM_SIZE:
                return None
        return raw
    except Exception as err:  # noqa: BLE001 - best-effort optional feature
        _LOGGER.debug("Air valve discovery unavailable: %s", type(err).__name__)
        return None


async def async_restart_valve(session: ClientSession, config_entry, serial: str) -> None:
    """Request a documented Loxone device restart (NOT a calibration command).

    Loxone says Gen1 performs a calibration run after a restart. Newer
    generations may differ. Never call this method automatically.
    """
    from homeassistant.exceptions import HomeAssistantError

    if not _device_serial(serial):
        raise HomeAssistantError("Invalid Loxone Air serial; restart aborted")
    endpoint = _endpoint(config_entry)
    if endpoint is None:
        raise HomeAssistantError("Miniserver credentials are missing")
    base, auth = endpoint
    try:
        async with session.get(
            f"{base}/dev/sys/wsdevice/{serial}/Reboot",
            auth=auth,
            timeout=ClientTimeout(total=10),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise HomeAssistantError(f"Loxone device restart HTTP {response.status}")
            content = (await response.text()).strip()
            if content.startswith("{"):
                payload = json.loads(content)
                ll = payload.get("LL", {})
                code = ll.get("Code")
            elif content.startswith("<"):
                root = ET.fromstring(content)
                result = root if root.tag == "LL" else root.find(".//LL")
                code = result.get("Code") if result is not None else None
            else:
                raise HomeAssistantError("Unrecognized Loxone device restart response")
            if str(code) != "200":
                raise HomeAssistantError(
                    f"Loxone device restart response code {code}"
                )
    except HomeAssistantError:
        raise
    except Exception as err:
        raise HomeAssistantError("Cannot send restart command to Loxone Air") from err


async def async_get_air_valves(hass, miniserver, config_entry) -> list[AirValveTerminal]:
    """Discover automatically once per Miniserver, shared by HA platforms."""
    task = getattr(miniserver, "_air_valve_task", None)
    if task is None:
        async def load() -> list[AirValveTerminal]:
            session = async_get_clientsession(hass)
            raw = await _fetch_project(session, config_entry)
            if raw is None:
                return []
            try:
                xml = await hass.async_add_executor_job(decode_program, raw)
                if xml is None:
                    return []
                terminals = await hass.async_add_executor_job(extract_air_valves, xml)
                _LOGGER.info(
                    "Physical Air valve discovery found %s devices, %s terminals",
                    len({t.device_uuid for t in terminals}), len(terminals),
                )
                return terminals
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Cannot read valve actuator topology: %s", type(err).__name__)
                return []

        task = hass.async_create_task(load(), name="loxone air valve discovery")
        miniserver._air_valve_task = task
    return await task


def linked_terminal_ids(config: dict) -> set[str]:
    """Flatten controls/subcontrols so existing visualized entities are not duplicated."""
    known: set[str] = set()

    def walk(controls: dict) -> None:
        for key, control in controls.items():
            known.add(str(key).lower())
            if isinstance(control, dict):
                if control.get("uuidAction"):
                    known.add(str(control["uuidAction"]).lower())
                if isinstance(control.get("subControls"), dict):
                    walk(control["subControls"])

    walk(config.get("controls") or {})
    return known


async def async_read_terminal(session: ClientSession, config_entry, uuid: str) -> float | None:
    """Read a physical terminal safely; never write a valve output."""
    endpoint = _endpoint(config_entry)
    if endpoint is None:
        return None
    base, auth = endpoint
    from urllib.parse import quote

    try:
        async with session.get(
            f"{base}/jdev/sps/io/{quote(uuid, safe='-')}",
            auth=auth,
            timeout=ClientTimeout(total=6),
        ) as response:
            if response.status != 200:
                return None
            payload = await response.json(content_type=None)
        ll = payload.get("LL", {})
        if str(ll.get("Code", "200")) != "200":
            return None
        value = ll.get("value")
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            return float(value)
        match = re.match(r"\s*([-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+))", str(value or ""))
        return float(match.group(1).replace(",", ".")) if match else None
    except Exception:  # noqa: BLE001 - best effort
        return None
