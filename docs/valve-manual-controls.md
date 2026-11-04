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

## Real device TechReport

The actuator's Loxone Config "Device details" report exposes
\`RequestedPercent\`, \`DestinationPercent\`, \`Pos\`, \`StartPos\`, \`MaxPos\`,
\`BatteryPowered\`, \`RefDriveState\`, \`ValveBusy\`, and \`DrvBusy\`.
The integration now contains a pure text parser
(\`parse_valve_tech_report\`) for this report, with tests. It is **not
automatically polled yet**, because the HTTP/WebSocket path to retrieve
the report has not been verified against the user's Miniserver.

For externally-powered heads, diagnostic \`Batt[%]:127\` is not a
battery percentage and must not become a 127% HA sensor. A successful
\`RefDriveState\` indicates successful reference calibration, while
\`RequestedPercent\` and \`DestinationPercent\` are reported percentages
and must not be inferred from the motor's raw step position.

Eight-byte serials (16 hex digits), e.g. \`0123456789ABCDEF\`, are
accepted in addition to older 6-byte serials.

The \`DriveTo\` device shell command has been confirmed in a real device's
\`call\` list, but its argument schema, units, and remote invocation
semantics remain **unverified**. Do not send guessed percentages or
raw motor step values to this command.

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
