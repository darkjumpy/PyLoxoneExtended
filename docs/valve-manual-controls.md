# Valve Actuator Air — manual opening and restart (experimental)

This branch extends PyLoxoneExtended's existing physical Air actuator entities,
and **does not take direct control over a physical actuator terminal**.
It deliberately requires an explicit manual override in **Loxone Config**,
because otherwise the Intelligent Room Controller can overwrite every value
that Home Assistant sends.

## Home Assistant entities

Each physical actuator receives:
- Read-only **Wysterowanie zaworu**: command currently reported by Loxone;
  it is not proof of the instantaneous mechanical position.
- **Ręczne otwarcie** (number, 0–100%) and **Tryb ręczny** (switch) ONLY
  when the two companion Virtual Inputs are available in the Miniserver
  program, as described below.
- **Uruchom ponownie głowicę** (button) ONLY when the installed Loxone
  program exposes a valid 12-hex-digit device serial number.

**Reboot is NOT the same thing as a dedicated recalibration command.**
Official Loxone web services document
\`/dev/sys/wsdevice/<serial>/Reboot\`. Loxone's Gen1 valve diagnostics
state that a calibration run occurs on restart. This is NOT proven to be
equivalent for every Gen2 actuator; the control is therefore named
"Uruchom ponownie głowicę" instead of "Rekalibruj".

**No generic calibration button is included in this release** because
the specific "Recalibrate Device" protocol command has not been verified.
Use Loxone Config > right click actuator > Recalibrate Device until the
correct command can be confirmed on a physical Miniserver.

## Setup in Loxone Config (required to write a valve setpoint)

For each physical Air actuator named, for example, \`Siłownik Air Jakub\`,
create **two Virtual Inputs** in the Loxone program:

| Exact name (Title) | Type | Values | Purpose |
| --- | --- | --- | --- |
| \`HA Valve | Siłownik Air Jakub | Value\` | Analog | 0–10 | Manual valve request |
| \`HA Valve | Siłownik Air Jakub | Manual\` | Digital | 0/1 | AUTO/manual selection |

Replace the device name with the **exact physical Air device Title**
from Loxone Config. These are *Virtual Inputs*, not physical Air actor
terminals. The integration discovers them from the installed project,
and sends updates to the **virtual input UUIDs only**, using PyLoxone's
authenticated existing command bus.

Wire a selection in Loxone Config with the following logic:

\`\`\`text
Intelligent Room Controller H output (0..10) --.
                                               \
                                                [analog selector] --> Valve Actuator Air output
                                               /
HA Valve | <device> | Value (0..10) -----------'
                            ^
HA Valve | <device> | Manual (0/1) selects input

Manual=0: automatic IRC demand controls the head
Manual=1: chosen value from HA controls the head
\`\`\`

**Keep frost protection and boiler/zone safety logic inside Loxone.**
Add a Loxone-side manual override timeout, e.g. automatically revert
Manual=0 after a limited session (such as 60 minutes), to protect against
HA restarts or long-forgotten overrides. The HA integration does NOT
create this failsafe.

The HA number reports 0–100%; it sends 0–10 to the Loxone virtual
analog input (e.g. 40% -> 4.0). The valve need not move immediately:
battery-powered units may wait for their configured 5–20 minute cycle.

Save the new Loxone Config program to the Miniserver. Then reload
PyLoxoneExtended to discover the two input UUIDs.

## Important limitations

- This code does not modify the physical Air actor directly.
- It does not create a working manual override automatically without
  the two inputs and the selection logic in Loxone Config.
- Matching needs exactly one VirtualIn of each type with the specified
  names; duplicates or incorrect Analog flag disable the HA controls.
- The restart button requires sufficient Miniserver user permissions,
  a reachable/awake Air device, and may not trigger calibration on Gen2.
  Device restart is always triggered only by a user pressing the button.
- A successful response from the Miniserver means the command was
  accepted, **not** that the valve has moved to the target.
- If existing controls are already visualized in LoxAPP3, the integration
  tries to associate them with the physical valve instead of creating
  duplicate HA entities.

Official Loxone references:
- https://www.loxone.com/plpl/kb/silownik-air/
- https://www.loxone.com/plpl/kb/web-services/
- https://www.loxone.com/plpl/kb/inteligentny-regulator-pomieszczeniowy-2/

No credentials, private device serials or downloaded Miniserver programs
should be committed to GitHub.
