# Valve Actuator Air — experimental physical devices

This is the next feature of PyLoxoneExtended, extending **existing Loxone
integration**. It is intentionally read-only and **automatically enabled on integration startup**.

## Why a separate discovery step?

The UI structure /data/LoxAPP3.json contains visualized function blocks and
controls, but does not contain the complete relationship between physical Air
devices and their individual terminals. The Loxone Config project stored on
the Miniserver does.

We read the latest installed sps_*.LoxCC or sps_*.zip **only once per
integration load** (from /dev/fslist/prog, /dev/fsget/prog/...) and look for
LoxAIRDevice containers with recognizable Valve Actuator names. Terminal
values are read from /jdev/sps/io/<terminal-uuid>, no write endpoints.

## What it creates

* One device in HA per recognized physical Valve Actuator Air, using the
  device UUID and configured name, with read-only entities for:
  * Temperature (when present).
  * Analog actuator output / valve demand (raw 0–10 unless the device's
    Display unit explicitly says otherwise).
  * Battery level (when exposed).
  * **Power source / Tryb zasilania**: Baterie / Zasilanie zewnętrzne / Nieznane, based on the **Battery** attribute stored on the physical LoxAIRDevice in the pairing program. The value is static until the program changes; it is **NOT** a real-time measurement of supply voltage or proof that the power cable is currently connected.
  * Online and physical digital input states (when present).
* Where an already-visualized InfoOnlyAnalog / InfoOnlyDigital control has the
  same physical terminal UUID, it is **reused and grouped** instead of
  duplicated. Normal Intelligent Room Controller entities remain unchanged.

This DOES NOT create a thermostat climate entity on the head: the head is an
actuator with a sensor; the room control logic is still in Loxone.
**Do not command the actuator directly** while Loxone's room controller is
driving it. The measured physical valve position is also not guaranteed to be
available; the analog output reports the commanded position.

## Manual position and device restart

An additional experimental implementation for manual 0–100% position control
and an explicit actuator restart button is described in
[Valve manual controls](valve-manual-controls.md). It requires two Loxone
Virtual Inputs and safe AUTO/manual selection logic; a verified dedicated
calibration API command is not yet available.

## Automatic discovery

Back up HA and install a test release from the feature branch. Physical Air
valve discovery starts **automatically** when the Loxone integration loads.
No checkbox or additional HA configuration is needed.
Re-pair devices and reload the integration if a power mode is changed. If the
`Battery` attribute is missing or unrecognized, the sensor reports
`Nieznane` rather than assuming that the actuator uses external power.

**Security notice:** program download and physical terminal reads use HTTP
Basic Authentication with the Miniserver login already configured in HA.
On an HTTP (non-TLS) Loxone connection, credentials travel over the local
network in clear text. This feature now runs automatically, so only install this experimental
branch on a trusted LAN; prefer TLS where supported.
Do not expose the Miniserver endpoints to the Internet.

This experimental version uses 120-second polling for new physical terminal
entities. Battery-powered Air devices generally update at their own configured
interval (5–20 minutes). Changes may not appear immediately.

## How to verify

After updating and restarting the integration, check Home Assistant's device
list for Valve Actuator Air entries. An Loxone device named "Siłownik Air
Salon" should create a corresponding device with entities such as
"Temperatura", "Wysterowanie zaworu", and the diagnostic sensor
"Tryb zasilania".

If no device appears, first confirm that the physical device in Loxone
Config is named "Siłownik Air..." or "Valve Actuator...", and that the
Miniserver credentials can read the installed program (HTTP endpoints listed
above).

Enable diagnostics temporarily:

\`\`\`yaml
logger:
  logs:
    custom_components.loxone.valve_air: debug
\`\`\`

The detected project hierarchy is firmware-dependent, and is **not yet
validated against a real installation with Valve Actuator Air**. Please send
redacted diagnostics or a sanitized relevant excerpt from a local Loxone
project rather than full credentials or a public copy of your project.

Implementation approach was independently developed using public protocol
documentation and the concept of physical device grouping described by the
Apache-2.0 project [smartmacherei/Loxone-Integration]
(https://github.com/smartmacherei/Loxone-Integration).
