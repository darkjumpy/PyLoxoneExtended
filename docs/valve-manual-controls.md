# Valve Actuator Air — direct DriveTo control (2026.11.0b5 beta)

> **Experimental.** Tested through a real Loxone Miniserver Compact and
> a battery-powered Valve Actuator Air. Not suitable as the only
> heating/frost protection control without a separately proven fallback.

## What changed in b5

PyLoxone Extended automatically discovers physical Valve Actuator Air
devices from the installed Loxone program and exposes a Home Assistant
`number` entity named **Bezpośrednie otwarcie** for heads with a valid
Air serial number. No new Loxone Config Virtual Input is required.

Changing the slider to an integer 0–100 sends one device-shell command
to the specific physical head:

```text
GET /dev/sys/wsdevice/<HEAD_SERIAL>/DriveTo/<PERCENT>
```

On the test head, manual commands `DriveTo/1`, `DriveTo/100`, and
`DriveTo/0` produced actuator movement consistent with 1%, 100%, and
0% commands. The final settled motor position after each command was
not yet independently verified. Intermediate percentages and long-term
persistence also need hardware testing.

The entity's state represents the **last acknowledged request sent by
Home Assistant during the current session**, not the measured or
independently verified opening of the valve. Its state is unknown
until an acknowledged command is sent. This is deliberate.

## Air battery sleep — critical limitation

Battery-powered Air heads sleep between communication windows.
Loxone may respond to an HTTP request with `<LL Code="200"
value="timeout"/>`. This is **not** a successful movement.

Before sending `DriveTo` to a head paired in battery mode or whose
power mode is unknown, b5 sends `TechReport` and checks that a valid
response contains the **same physical serial**. If that check fails,
it blocks `DriveTo`. The check is a *best-effort contact check*:
Miniserver may serve diagnostic information when a head is sleeping,
so a successful `TechReport` **does not prove the head is awake**.
A following `DriveTo` may still time out.

**b5 does not automatically wake up heads.** No verified HTTP equivalent
for Loxone Config's `Keep device awake` action is known. On battery
heads, choose that action in Loxone Config and wait for the next Air
communication cycle, or use the physical wake button. After the device
is awake, try the slider. No blind retries are performed on a timeout,
because it is not known whether the actuator will still process the
command.

The slider rate-limits attempts to at least 15 seconds apart and
accepts only whole numbers in the range 0–100.

## How replies are handled

- HTTP 200 with LL `Code="200"`, matching `control`, and
  `value="ok"`: the device has acknowledged the command. This
  **does not** verify the final motor position.
- `value="timeout"`: unconfirmed; Home Assistant shows an error and
  does **not** automatically resend.
- Invalid command, mismatched device, malformed XML, or HTTP error:
  no successful update is shown.

## Diagnostics

Each discovered head also exposes diagnostic sensors derived from
`TechReport` where values are available, including:

- **Zadane otwarcie** (`RequestedPercent`)
- **Otwarcie docelowe** (`DestinationPercent`)
- **Stan kalibracji** (`RefDriveState`)
- **Błędy napędu** (`Error[#]`)
- **Tryb zasilania** (`BatteryPowered`)

**Important:** `RequestedPercent` and `DestinationPercent` may remain
at 0 even while `DriveTo` moves the motor. They belong to the
ordinary control path and must not be used as verification of direct
shell movement. Values of `Pos`, `StartPos`, and `MaxPos` are raw
motor data; do not automatically convert them to percentages.

The integration also attempts
`/dev/sys/wsdevice/<serial>/GetLinkQuality`. Its HTTP availability
has **not been verified** on the tested Miniserver. If the command does
not return usable data for the particular head, the RSSI/LQI entities
are unavailable. Data labeled for the Air Base must not be treated as
measurements of an individual valve.

Diagnostic polls are cached per head: 180 seconds for externally
powered/unknown heads, 900 seconds for paired battery heads.

## Restart and safety

The separately exposed **Uruchom ponownie głowicę** button uses
`Reboot`; **it is not a dedicated recalibration command**. The
`ForceRefDrive` shell command has not been validated for remote use.

Keep the existing reliable room control and heating protection
strategy while testing b5. Do not connect two competing Loxone/HA
controllers to the same actuator without deciding who owns the valve.
The new direct control is experimental; a sleeping or disconnected
head may fail to accept a position request.

All device commands use Miniserver credentials saved in the
integration. Plain HTTP Basic Authentication exposes credentials on
the LAN; restrict access to a trusted network and prefer HTTPS when
supported. Do not expose these device-shell URLs to the internet.

## Installation / rollback

Install the b5 **pre-release** in HACS from the fork
`darkjumpy/PyLoxoneExtended`, then reload the Loxone integration
or restart Home Assistant. The b5 tag must include the
`manifest.json` version `2026.11.0b5`.

For rollback, install the earlier `2026.11.0b4` tag or restore your
previous custom integration files and restart HA. The b4 feature used
a dedicated Virtual Input and does **not** contain direct DriveTo
control.

## Related docs

- [Physical Air valve discovery](valve-actuator-air.md)
- [Loxone Air documentation](https://www.loxone.com/plpl/kb/air/)
- [Loxone web services](https://www.loxone.com/plpl/kb/web-services/)
