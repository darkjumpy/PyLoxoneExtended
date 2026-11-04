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

The entity's state represents the **latest requested target**,
whether queued or acknowledged, **not** the measured or independently
verified opening of the valve. Its state is unknown until a request is
submitted or an acknowledged target is restored. The separately exposed
binary pending sensor distinguishes queue state from acknowledgment.

## Battery Air: latest-value queue and natural wake detection

The `Bezpośrednie otwarcie` slider **enqueues** a target, rather than
sending an immediate `DriveTo`. The slider displays the **new requested
target** (assumed state), which must not be confused with physical opening.

The linked `binary_sensor.Polecenie oczekuje na wysłanie` reports:

- **on** — a not-yet-acknowledged target remains in the HA queue;
- **off** — no target pending (acknowledged, expired or never submitted).

Entity attributes show `pending_value`, `last_acknowledged_value`,
`queued_at` and `last_error`. A successful `DriveTo` `ok` response
clears that specific pending command. A `timeout` keeps it queued.

**The key of every queue is (physical Air serial, entity parameter).**
When 30% is pending and the setpoint changes to 50%, the **single**
`valve_position` queue contains 50% only. An acknowledgement of a
30% command that was already in flight cannot delete the newer 50% request.
A future device with both `brightness` and `colour_temperature`
will use two independently coalescing queues; **b5 implements an actual
sender only for Valve Actuator Air** and does not claim new light controls.

### Natural wake algorithm (experimental; needs live hardware validation)

When the Air head is paired for battery power (or mode is unknown):

1. The integration stores the newest target in Home Assistant storage.
2. Only while a command is pending, it probes the Miniserver's
   `TechReport` endpoint approximately every **10 seconds**.
3. It checks that the returned report belongs to the correct Air serial
   and reads `WkupCnt[]`, a counter reported by the head.
4. The first successful report from the correct head is treated as
   a **candidate wake** and the latest queued value is sent once.
   On subsequent reports, a **change** in `WkupCnt[]` indicates the
   next candidate natural wake.
5. If Loxone responds `ok`, that command is acknowledged and removed.
   On `timeout`, its setpoint remains pending. It is **not** blindly
   retried in the same observed wake; only a subsequent counter change
   permits a further attempt.

The real head reported `WkupCnt[]`, but **we have not yet proven that
every natural five-minute polling cycle increments it**, or that a
10-second report probe will catch the short wake window. Therefore,
the queue and sensor logic can be tested now, but unattended
delivery is not yet guaranteed. If `WkupCnt[]` does not change after
the initial candidate wake, a timed-out command stays pending.

The documentation from Loxone states that the Miniserver cannot
immediately wake a sleeping battery device. The `Keep device awake`
menu action holds it awake for five minutes **following its next wake**.
This queue instead waits for a new observed wake; it does not claim
to have discovered a remote radio wake command.

With the 5-minute Air polling interval used during testing, the
expected delivery after a detected wake would be approximately one Air
cycle, but this latency is **not verified** and can be longer.

To prevent a very old request from actuating a valve many hours later,
a command is retained in the queue for up to **60 minutes** and then
expires with a diagnostic error. Pending commands are stored locally
in Home Assistant and restored after HA/reload only while still fresh.
They are never replayed as a stale full-device event log.

Heads explicitly paired as externally powered skip wake detection and
send immediately. Any unsuccessful attempt remains pending; on those
heads, submitting a newer setpoint is the explicit way to request
another attempt.

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
