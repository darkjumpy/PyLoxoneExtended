# Valve Actuator Air — direct setpoint and link diagnostics (experimental)

## Why just one Loxone Virtual Input?

This integration controls **unregulated Air valve heads** that are paired
with the Miniserver but not connected to an Intelligent Room Controller.
The Loxone Miniserver's physical Air actor output is not an authenticated
writeable HA/visualisation control. Create **one analog Virtual Input** for
each actuator, and wire it directly to the physical Air valve output in
Loxone Config.

Example for a device whose physical \`Title\` is \`Siłownik Air Salon\`:

| Virtual Input (exact Title) | Type | Value |
| --- | --- | --- |
| \`HA Valve | Siłownik Air Salon | Value\` | Analog | 0–10 |

Connect the Virtual Input's analog output to the \`Valve Actuator Air\`
analog actor in Loxone Config. Do not add a second manual/auto input;
none is required in this unregulated setup.

\`\`\`text
Home Assistant number (0–100%)
       |
       v  (value divided by 10)
HA Valve | Siłownik Air Salon | Value (0–10)
       |
       v
Loxone physical Air actuator analog output
\`\`\`

Once the program is saved to the Miniserver, restart or reload the
PyLoxone Extended integration.

When the exactly named Virtual Input is found, a \`number.Otwarcie zadane\`
entity appears under the physical actuator in Home Assistant.
The 0–100% input sends 0–10 to that Virtual Input.

The read-only \`Wysterowanie zaworu\` sensor reports the command visible
on the physical actor, which is not necessarily the measured motor
position. In battery mode a new position can take until the next Air
communication cycle to be applied.

This **does not** keep rooms above a minimum temperature or provide
frost protection. For unattended operation, connect a suitable protection
strategy / fallback in Loxone Config.

## RSSI and Link Quality

Loxone Config's Air Diagnostics can report RSSI in dB. This branch
creates an RSSI or Link Quality diagnostic sensor only when Loxone's
downloaded project contains a corresponding readable analog Air terminal
(RSSI / Link Quality / LQI). It never guesses 100% from online state,
and it does not invent a source for internal Air Diagnostics.

**Limitation:** Real Miniserver Compact firmware may not expose RSSI
as an analog terminal. In this case, no signal-quality entity appears
until the private diagnostics interface is investigated and confirmed.
To verify RSSI manually, use Loxone Config > Air Diagnostics.

## Real device TechReport and Link Quality

The physical Miniserver has been confirmed to accept this **read-only**
service command via HTTP Basic Authentication:

\`\`\`
GET /dev/sys/wsdevice/<16-hex-digit-Air-serial>/TechReport
\`\`\`

The returned XML contains \`<LL value="Serial:...;RequestedPercent:...;" Code="200"/>\`.
The integration parses these semicolon-separated fields and exposes
\`Zadane otwarcie\`, \`Otwarcie docelowe\`,
\`Stan kalibracji\`, \`Błędy napędu\`, and
\`Tryb zasilania\` as diagnostic sensors under the physical actuator.

For link quality the integration *also attempts* the read-only command:

\`\`\`
GET /dev/sys/wsdevice/<16-hex-digit-Air-serial>/GetLinkQuality
\`\`\`

**The HTTP form of GetLinkQuality has not yet been confirmed on the user's
Miniserver**, even though it is available in Loxone Config's device shell.
If no valid XML response or no data for this exact device is returned,
signal-quality sensors show unavailable. Results whose \`Target\` does
not match the actuator serial are discarded, to avoid assigning Air Base
quality to the valve.

Poll results are shared across all sensor entities for each valve:
180 seconds for an external/unknown supply mode, and 900 seconds for
heads paired in battery mode. Commands are never sent to drive the motor
on a timer.

For externally-powered heads, \`Batt[%]:127\` is an invalid sentinel,
not 127% battery. The actual valve opening is **not** inferred from
raw motor steps \`Pos\`, \`StartPos\`, or \`MaxPos\`: use the reported
\`RequestedPercent\` and \`DestinationPercent\` as distinct targets.

Eight-byte serials (16 hex digits) are supported.

The \`DriveTo\` device shell command is visible in the real device's
\`call\` list, but its argument schema, units, and remote invocation
semantics are **unverified**. Do not send guessed percentages or
raw step counts to this service command.

## Restart vs calibration

A button named **Uruchom ponownie głowicę** is offered if the project
contains a valid device serial. It invokes the documented
\`/dev/sys/wsdevice/<serial>/Reboot\` endpoint, which requires an account
with enough privileges. Restart is **not** an explicit recalibration API
command; Gen1 may recalibrate on restart, other generations need
verification. The button **Rekalibruj urządzenie** is intentionally
not added without an independently verified calibration endpoint.

## Diagnostics & safety

- The link/temperature/position values are read-only and polled.
- Position commands require a purpose-made analog Loxone Virtual Input.
- Audio functionality and previous entity identifiers are preserved.
- Program fetching uses stored Miniserver HTTP authentication; on
  non-TLS connections credentials can be visible on the LAN.
- This is an experimental branch pending real Compact hardware tests.
- Do not post exported programs, passwords, Air serials or tokens publicly.

Sources:
- https://www.loxone.com/plpl/kb/air/
- https://www.loxone.com/plpl/kb/web-services/
