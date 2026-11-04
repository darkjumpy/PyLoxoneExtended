"""Physical Valve Actuator Air discovery and experimental direct control.

LoxAPP3 describes UI controls, not the physical Air/Tree device hierarchy.
We inspect the installed Loxone program and enumerate physical valve heads.
Discovery is read-only; a separate, explicit HA slider may issue DriveTo
device-shell commands. Discovery failures must never prevent normal startup.

Protocol investigation inspired by the Apache-2.0 licensed Loxone-Integration
project by smartmacherei (https://github.com/smartmacherei/Loxone-Integration).
This implementation is intentionally limited to Loxone Valve Actuator Air.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from urllib.parse import urlsplit
from time import monotonic

from aiohttp import BasicAuth, ClientSession, ClientTimeout
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import (
    CONF_AIR_TERMINAL_POLL_INTERVAL,
    DEFAULT_AIR_TERMINAL_POLL_INTERVAL,
    MIN_AIR_TERMINAL_POLL_INTERVAL,
    MAX_AIR_TERMINAL_POLL_INTERVAL,
)

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


def _air_device_serial(device: ET.Element) -> str | None:
    """Resolve an Air head's serial from Loxone's Serial or IPv6 EUI-64 IP.

    Real Loxone project Air devices often have no Serial attribute. Instead
    they store an uncompressed IPv6 address in IP, e.g.
    FE80000000000000524F94FFFEC51C12 maps to 504F94FFFEC51C12.
    IPv6 flips the universal/local bit (0x02) in the first IID octet.
    Reject malformed, non-link-local, non-EUI-64 and conflicting values.
    """
    from ipaddress import IPv6Address, AddressValueError

    direct = _device_serial(device.get("Serial"))
    raw = (device.get("IP") or "").strip()
    derived = None
    if raw:
        try:
            ip = IPv6Address(int(raw, 16)) if re.fullmatch(r"[0-9a-fA-F]{32}", raw) else IPv6Address(raw)
        except (AddressValueError, ValueError):
            pass
        else:
            iid = ip.packed[8:]
            if ip.is_link_local and iid[3:5] == b"\xFF\xFE":
                derived = f"{iid[0] ^ 0x02:02X}" + iid[1:].hex().upper()
                derived = _device_serial(derived)
    if direct and derived and direct != derived:
        _LOGGER.warning("Air head serial/IP mismatch; refusing to send device commands")
        return None
    return direct or derived


def air_terminal_poll_interval(options: dict) -> int:
    """Return a validated effective Air I/O polling interval in seconds."""
    raw = options.get(
        CONF_AIR_TERMINAL_POLL_INTERVAL, DEFAULT_AIR_TERMINAL_POLL_INTERVAL
    )
    try:
        interval = int(raw)
        if float(raw) != interval:
            raise ValueError("fractional interval")
    except (ValueError, TypeError, OverflowError):
        return DEFAULT_AIR_TERMINAL_POLL_INTERVAL
    if not MIN_AIR_TERMINAL_POLL_INTERVAL <= interval <= MAX_AIR_TERMINAL_POLL_INTERVAL:
        return DEFAULT_AIR_TERMINAL_POLL_INTERVAL
    return interval


def valve_output_to_percent(raw: float | None) -> int | None:
    """Convert the documented raw Loxone Valve Actuator actor 0..10 to 0..100%.

    This is the normal Loxone actor output/demand, NOT measured motor travel.
    Never guess if a firmware returns a value outside the documented range.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0 or value > 10:
        return None
    return min(100, max(0, int(value * 10 + 0.5)))


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


_TECH_FIELDS = {
    "RequestedPercent": "requested_percent",
    "DestinationPercent": "destination_percent",
    "Pos": "motor_steps",
    "StartPos": "start_steps",
    "MaxPos": "max_steps",
    "BatteryPowered": "battery_powered",
    "ValveBusy": "valve_busy",
    "DrvBusy": "drive_busy",
    "Error[#]": "error_count",
    "Batt[%]": "battery_level",
    "WkupCnt[]": "wake_count",
}


def parse_valve_tech_report(report: str) -> dict[str, int | bool | str]:
    """Parse reported valve status, not an inferred step-to-percent mapping.

    TechReport is confirmed over Miniserver HTTP to return an XML LL value.
    That does not imply the device's shell accepts writes through this API.
    """
    found: dict[str, int | bool | str] = {}
    for token in re.split(r"[;\r\n]+", report):
        if ":" not in token:
            continue
        name, raw = (part.strip() for part in token.split(":", 1))
        if name == "RefDriveState":
            if raw:
                found["reference_state"] = raw
            continue
        field = _TECH_FIELDS.get(name)
        if field is None:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if field in ("requested_percent", "destination_percent", "battery_level"):
            # For externally powered heads, Batt[%]:127 is not a real 127%.
            if not 0 <= value <= 100:
                continue
        if field in ("battery_powered", "valve_busy", "drive_busy"):
            if value in (0, 1):
                found[field] = bool(value)
        elif field in ("motor_steps", "start_steps", "max_steps", "error_count", "wake_count"):
            if value >= 0:
                found[field] = value
        else:
            found[field] = value
    return found


def estimated_valve_opening(values: dict) -> int | None:
    """Heuristic motor position in %, ONLY for a calibrated idle actuator.

    Real tests of DriveTo/0 and /100 showed Pos near MaxPos (closed)
    and near zero (open), respectively. It is NOT a verified position
    sensor and must never be used to send an automatic DriveTo.
    """
    position = values.get("motor_steps")
    limit = values.get("max_steps")
    if (
        not isinstance(position, int) or isinstance(position, bool)
        or not isinstance(limit, int) or isinstance(limit, bool)
        or limit < 100 or limit > 50000 or position < 0
        or position > limit * 1.1
        or str(values.get("reference_state", "")).lower() != "successful"
        or values.get("valve_busy") is not False
        or values.get("drive_busy") is not False
        or values.get("error_count", 0) != 0
    ):
        return None
    return max(0, min(100, int(100 * (limit - position) / limit + 0.5)))


def _serial_from_visualization_title(name: str | None, ending: str) -> str | None:
    """Find exactly one Air serial in an HA Valve visualization title.

    Loxone Config may replace special separators (|) with escaped
    underscores/backslashes in its visualization. Do not rely on exact
    punctuation: require a strict HA Valve prefix, suffix and an exact
    12/16-hex Air serial. Reject ambiguous titles.
    """
    if not isinstance(name, str):
        return None
    if not re.match(r"^\\s*HA\\s+Valve\\b", name, re.IGNORECASE):
        return None
    if not re.search(rf"\\b{re.escape(ending)}\\s*$", name, re.IGNORECASE):
        return None
    matches = re.findall(
        r"(?<![0-9A-F])(?:[0-9A-F]{16}|[0-9A-F]{12}|(?:[0-9A-F]{2}:){7}[0-9A-F]{2})(?![0-9A-F])",
        name, re.IGNORECASE,
    )
    if len(matches) != 1:
        return None
    return _device_serial(matches[0])


def serial_from_ha_valve_slider(name: str | None) -> str | None:
    """Match ordinary HA Valve UI sliders despite rewritten separators."""
    return _serial_from_visualization_title(name, "Value")


def serial_from_ha_valve_button(name: str | None) -> str | None:
    """Match a visualized physical button mirrored as an info-only control."""
    return _serial_from_visualization_title(name, "Button")


def expected_valve_input_name(serial: str | None, device_name: str) -> str:
    """Stable operator-facing name for the per-head analog Virtual Input."""
    normalized = _device_serial(serial)
    return f"HA Valve | {normalized or device_name} | Value"


def _direct_target_input(
    root: ET.Element, device_name: str, serial: str | None = None,
) -> str | None:
    """Discover one verified analog VirtualIn, tolerating escaped names.

    Prefer exact serial-based matches; Loxone sometimes rewrites the pipe
    separators in the installed project. Do not mistake generic sliders
    for physical device inputs, or accept duplicate candidate names.
    """
    expected = expected_valve_input_name(serial, device_name).casefold()
    legacy = f"HA Valve | {device_name} | Value".casefold()
    serial = _device_serial(serial)
    matches = []
    for control in root.iter("C"):
        if control.get("Type") != "VirtualIn":
            continue
        title = (control.get("Title") or "").strip()
        if not title:
            continue
        title_serial = serial_from_ha_valve_slider(title)
        if not (
            (serial and title_serial == serial)
            or title.casefold() in (expected, legacy)
        ):
            continue
        uuid = control.get("U")
        if not uuid:
            continue
        if (control.get("Analog") or "").strip().lower() in ("false", "0"):
            continue
        if any(
            (control.get(key) or "").strip().lower() in ("true", "1")
            for key in ("Digital", "UseAsDigital", "IsDigital")
        ):
            continue
        matches.append(uuid)
    if len(matches) > 1:
        _LOGGER.warning(
            "Multiple HA Valve analog Virtual Inputs match one valve; skipping %s",
            device_name,
        )
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
        serial = _air_device_serial(device)
        manual_value_uuid = _direct_target_input(root, title, serial)
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


def tech_report_confirms_awake(report: str | None, serial: str) -> bool:
    """Require a fresh successful device report for the exact Air serial.

    A matching TechReport is only a momentary readiness check, not a
    general-purpose wake-up. Reports from other Air heads are rejected.
    """
    normalized = _device_serial(serial)
    if normalized is None or not report:
        return False
    match = re.search(
        r"(?:^|[;\r\n])\s*Serial\s*:\s*([0-9a-fA-F:-]+)",
        report,
    )
    return match is not None and _device_serial(match.group(1)) == normalized


def parse_drive_to_response(payload: bytes, serial: str, position: int) -> str:
    """Parse a write command without treating generic LL Code=200 as success.

    Miniserver returns Code=200 with value='timeout' for sleeping heads.
    Only a matching control and explicit 'ok' confirm acknowledgement.
    """
    from homeassistant.exceptions import HomeAssistantError

    serial = _device_serial(serial)
    if serial is None:
        raise HomeAssistantError("Nieprawidłowy identyfikator głowicy Air")
    expected = f"dev/sys/wsdevice/{serial}/DriveTo/{position}".lower()
    if len(payload) > 4096:
        raise HomeAssistantError("Odpowiedź głowicy jest za długa")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise HomeAssistantError("Niepoprawna odpowiedź XML z Miniservera") from exc
    ll = root if root.tag == "LL" else root.find(".//LL")
    if ll is None:
        raise HomeAssistantError("Brak odpowiedzi LL z Miniservera")
    control = ll.get("control", "").lower()
    if control != expected:
        raise HomeAssistantError("Odpowiedź dotyczy innej komendy niż DriveTo")
    if ll.get("Code") != "200":
        raise HomeAssistantError("Miniserver odrzucił polecenie DriveTo")
    result = (ll.get("value") or "").strip().lower()
    if result == "ok":
        return "ok"
    if result == "timeout":
        raise HomeAssistantError(
            "Brak potwierdzenia DriveTo — głowica może spać. "
            "Polecenie mogło zostać przyjęte; nie ponawiaj automatycznie."
        )
    raise HomeAssistantError(
        "Niepotwierdzona odpowiedź na DriveTo; sprawdź pozycję głowicy"
    )


async def async_drive_to(
    session: ClientSession, config_entry, serial: str, position: float,
    battery_powered: bool | None = None,
    *, already_probed: bool = False,
) -> None:
    """Send one direct 0–100% Air actuator command (experimental).

    Three real-device test values (0, 1 and 100) suggest percent semantics,
    but no intermediate value or long-term behaviour has been verified.
    """
    from homeassistant.exceptions import HomeAssistantError

    normalized = _device_serial(serial)
    if normalized is None:
        raise HomeAssistantError("Nieprawidłowy identyfikator głowicy Air")
    if (
        isinstance(position, bool) or not isinstance(position, (int, float))
        or not math.isfinite(position)
        or position != int(position) or not 0 <= position <= 100
    ):
        raise HomeAssistantError("DriveTo przyjmuje tylko całkowite procenty 0–100")
    endpoint = _endpoint(config_entry)
    if endpoint is None:
        raise HomeAssistantError("Brak danych logowania do Miniservera")
    base, auth = endpoint
    value = int(position)

    # Air heads paired for battery operation cannot always receive direct
    # shell commands: do NOT interpret "online" as "awake". Probe the
    # very same head and only send DriveTo if it answers TechReport.
    # Keep-device-awake in Loxone Config waits for the next natural wake;
    # its HTTP equivalent is not verified, so we deliberately do not
    # guess a wake command or promise that this probe can wake the device.
    if battery_powered is not False and not already_probed:
        tech = await _read_device_shell_report(
            session, config_entry, normalized, "TechReport"
        )
        if not tech:
            raise HomeAssistantError(
                "Głowica Air nie odpowiada (prawdopodobnie śpi). "
                "Wybudź ją lub wybierz 'Obudź urządzenie' w Loxone Config "
                "i poczekaj na jej następny cykl. DriveTo NIE zostało wysłane."
            )
        if not tech_report_confirms_awake(tech, normalized):
            raise HomeAssistantError(
                "Nie udało się potwierdzić tożsamości wybudzonej głowicy Air. "
                "DriveTo nie zostało wysłane."
            )
    try:
        async with session.get(
            f"{base}/dev/sys/wsdevice/{normalized}/DriveTo/{value}",
            auth=auth,
            timeout=ClientTimeout(total=15),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise HomeAssistantError(
                    f"Polecenie DriveTo zwróciło błąd HTTP {response.status}"
                )
            raw = await response.content.read(4097)
            parse_drive_to_response(raw, normalized, value)
    except HomeAssistantError:
        raise
    except Exception as exc:
        raise HomeAssistantError(
            "Nie udało się potwierdzić DriveTo; nie ponawiaj automatycznie"
        ) from exc


def parse_device_report_response(payload: bytes) -> str | None:
    """Return the content of a successful Loxone LL XML diagnostic response."""
    if len(payload) > 65536:
        return None
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    ll = root if root.tag == "LL" else root.find(".//LL")
    if ll is None or str(ll.get("Code")) != "200":
        return None
    return ll.get("value")


async def _read_device_shell_report(
    session: ClientSession, config_entry, serial: str, command: str,
    *, request_timeout: float = 12,
) -> str | None:
    """Read a validated diagnostic shell command from the Miniserver.

    The user confirmed this path and an XML LL response for TechReport.
    GetLinkQuality is a best-effort read-only probe: availability depends
    on firmware and Miniserver permissions.
    """
    if command not in ("TechReport", "GetLinkQuality"):
        return None
    serial = _device_serial(serial)
    endpoint = _endpoint(config_entry)
    if serial is None or endpoint is None:
        return None
    base, auth = endpoint
    try:
        async with session.get(
            f"{base}/dev/sys/wsdevice/{serial}/{command}",
            auth=auth,
            timeout=ClientTimeout(total=request_timeout),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                _LOGGER.debug("Valve %s HTTP %s", command, response.status)
                return None
            # Never store or emit full device logs or private serial numbers.
            payload = await response.content.read(65537)
            if len(payload) > 65536:
                return None
        return parse_device_report_response(payload)
    except (OSError, ValueError, ET.ParseError) as err:
        _LOGGER.debug("Valve diagnostic %s unavailable: %s", command, type(err).__name__)
        return None
    except Exception as err:  # noqa: BLE001 - optional diagnostics
        _LOGGER.debug("Valve diagnostic %s unavailable: %s", command, type(err).__name__)
        return None


async def async_get_valve_diagnostics(
    hass, miniserver, config_entry, serial: str | None, battery_powered: bool | None,
) -> dict[str, int | float | bool | str]:
    """Share one poll among all sensors on one physical Air valve.

    The Miniserver, not the Air head, is polled. Battery-operated devices
    are polled less often to avoid unnecessary traffic and wake-ups.
    """
    normalized = _device_serial(serial)
    if normalized is None:
        return {}
    cache = getattr(miniserver, "_air_valve_diagnostic_cache", None)
    if cache is None:
        cache = {}
        miniserver._air_valve_diagnostic_cache = cache
    now = monotonic()
    interval = 900 if battery_powered is True else 180
    cached = cache.get(normalized)
    # The pairing project's Battery flag may be stale after external 24 V
    # is connected. Once TechReport confirms physical power, let the live
    # BatteryPowered value determine subsequent diagnostic polling.
    if cached is not None and cached[1].done() and not cached[1].cancelled():
        try:
            measured_battery = cached[1].result().get("battery_powered")
        except Exception:  # noqa: BLE001 - a failed optional poll should recover
            measured_battery = None
        if isinstance(measured_battery, bool):
            interval = 900 if measured_battery else 180
    if cached is not None and now - cached[0] < interval:
        return await cached[1]

    async def load() -> dict[str, int | float | bool | str]:
        session = async_get_clientsession(hass)
        tech, link = await asyncio.gather(
            _read_device_shell_report(session, config_entry, normalized, "TechReport"),
            _read_device_shell_report(session, config_entry, normalized, "GetLinkQuality"),
        )
        if tech:
            reported = re.search(r"(?:^|[;\r\n])\s*Serial:\s*([0-9a-fA-F:-]+)", tech)
            if reported and _device_serial(reported.group(1)) != normalized:
                _LOGGER.debug("Valve TechReport serial differs from device; skipping")
                tech = None
        values: dict[str, int | float | bool | str] = (
            parse_valve_tech_report(tech) if tech else {}
        )
        estimate = estimated_valve_opening(values)
        if estimate is not None:
            values["estimated_opening"] = estimate
        if link:
            # Avoid attributing Air Base RSSI/LQI to a specific head.
            match = re.search(r"(?:^|[;\r\n])\s*Target\s*:\s*([0-9a-fA-F:-]+)", link)
            if match is None or _device_serial(match.group(1)) == normalized:
                values.update(parse_link_quality_report(link))
            else:
                _LOGGER.debug("Valve link report target differs from device; skipping")
        return values

    task = hass.async_create_task(load(), name="loxone air valve diagnostics")
    cache[normalized] = (now, task)
    return await task


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


async def async_set_valve_virtual_input(
    session: ClientSession, config_entry, uuid: str, raw_value: float,
) -> float:
    """Set an existing, explicitly configured analog Virtual Input (0..10).

    Never send a command to the hardware Air device. Only the specified
    VirtualIn UUID in the installed Loxone program is written.
    """
    from homeassistant.exceptions import HomeAssistantError
    from urllib.parse import quote

    if (
        not uuid or isinstance(raw_value, bool)
        or not isinstance(raw_value, (float, int))
        or not math.isfinite(raw_value)
        or raw_value < 0 or raw_value > 10
    ):
        raise HomeAssistantError("Nieprawidłowa wartość wejścia wirtualnego 0–10")
    endpoint = _endpoint(config_entry)
    if endpoint is None:
        raise HomeAssistantError("Brak poprawnych danych połączenia do Miniservera")
    base, auth = endpoint
    # One decimal place, so 65% -> 6.5 and the exact output is predictable.
    command = f"{float(raw_value):.1f}"
    try:
        async with session.get(
            f"{base}/jdev/sps/io/{quote(uuid, safe='-')}/{command}",
            auth=auth, timeout=ClientTimeout(total=9),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise HomeAssistantError(f"Miniserver zwrócił HTTP {response.status}")
            reply = await response.json(content_type=None)
        ll = reply.get("LL") if isinstance(reply, dict) else None
        if not isinstance(ll, dict) or str(ll.get("Code")) != "200":
            raise HomeAssistantError("Miniserver nie potwierdził wartości wejścia wirtualnego")
        return float(raw_value)
    except HomeAssistantError:
        raise
    except Exception as exc:
        raise HomeAssistantError(
            "Nie udało się potwierdzić ustawienia wirtualnego wejścia Loxone"
        ) from exc


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
