# Valve Actuator Air — normal 0–10 control via analog Virtual Input

> **Development branch only (not included in published b6).**
> The firmware/device-shell `DriveTo` is a temporary test command: it can
> move the actuator and then the ordinary Loxone program restores its prior
> position. This implementation **never calls DriveTo**.

## One-time setup in Loxone Config

For every Valve Actuator Air you wish to control solely from Home Assistant:

1. In **Periphery → Virtual Inputs**, add a **Virtual Input**.
2. Configure it as **analog**, not digital, with min **0**, max **10**,
   one decimal place, and an appropriate **default/start value** for the
   building if Home Assistant is offline or the Miniserver restarts.
3. Give it a stable name, preferably using the head's full Air serial:

   ```text
   HA Valve | 504F94FFFEC51C12 | Value
   ```

   For the WC-góra test actuator (serial `504F94FFFEC51C12`).

   General convention: `HA Valve | <SERIAL> | Value`.

   For compatibility, `HA Valve | <exact Loxone device title> | Value`
   is also recognized. Serial naming survives device renames.

4. On a Loxone programming page, connect the **analog output of that
   Virtual Input directly to the analog input of that physical Valve
   Actuator Air**. No `Intelligent Room Controller` and no AUTO/manual
   selector are required in an HA-only configuration.
5. Save the program to the **Miniserver** and reload the PyLoxone Extended
   integration in Home Assistant, so it re-reads the installed program.

**Creating or wiring a Virtual Input remotely into a live Miniserver
project is NOT performed by this HA integration.** There is no verified
safe supported Miniserver API for editing and deploying Loxone Config
project wiring. The initial Config change is manual.

## Entities and readings

The integrated physical head exposes a HA `number` entity
**Otwarcie zadane (HA)** with a 0–100% range in steps of 1%.
The entity is attached to the **physical Valve Actuator Air device**, not
a new duplicate virtual device. The integration reuses the older
`<device UUID>_air_direct_drive_to` unique ID for the slider so an
existing HA entity_id can stay unchanged across the migration.
Its implementation no longer sends any `DriveTo`.

| Home Assistant | Sent to Virtual Input |
| --- | --- |
| 0% | 0.0 |
| 30% | 3.0 |
| 50% | 5.0 |
| 65% | 6.5 |
| 100% | 10.0 |

Write: `GET /jdev/sps/io/<virtual-input-uuid>/<value 0..10>`.
On success, the integration updates the slider; subsequent reads of
`/jdev/sps/io/<virtual-input-uuid>` synchronize the setpoint with
the Miniserver. Readback uses the Air terminal polling option
(5–600 seconds, default 120 seconds), not the head's own Air cycle.

**Setpoint is not the measured motor position.** The physical
valve moves when Loxone delivers its normal control command, in the
Air polling cycle (5–20 minutes as configured). Home Assistant sends
the virtual input immediately and need not wake battery heads.

If no matching Virtual Input exists, the slider is unavailable with
an `expected_virtual_input_name` attribute. It never falls back to
`DriveTo` on missing configuration. Duplicate or explicitly digital
inputs are rejected to avoid controlling an incorrect destination.

The b5/b6 binary entity **Polecenie oczekuje na wysłanie** is not
created in this new mode: HA sends to the Miniserver immediately, and
Loxone is responsible for subsequent physical Air delivery. An old
orphaned HA entity-registry entry may need removing manually.
Old experimental queue files are not loaded as active device senders.

## Button presses on the physical head

The Valve Actuator Air's **Input 1** is its pairing/push button.
The Miniserver can show button changes live in Loxone Config even if no
matching UUID is distributed to Home Assistant's WebSocket event stream.
Polling a momentary input every 5, 30 or 120 seconds is **not reliable**.

To make HA receive presses promptly, in **Loxone Config**:

1. Select the physical **Wejście 1 / Input 1** on the desired Air head.
2. Enable **Zastosuj w wizualizacji** if available, give it a room/category,
   and save the program to Miniserver.
3. If the physical terminal cannot be published on its own, connect Input 1
   to a digital visualization-capable info/status object, expose that
   object in the visualization, and name its displayed control exactly:

   ```
   HA Valve | 504F94FFFEC51C12 | Button
   ```

   Replace the serial per head. The physical Input 1 may retain its
   original name; the `Button` name is the **published info/status
   object's visualization title**.
4. Reload PyLoxone Extended, then briefly press the button. If the
   published state's UUID is present in LoxAPP3 / websocket updates,
   the existing **Wejście 1** entity on that valve will update
   immediately, with attributes `last_press`,
   `press_count_since_reload` and `event_uuids`. HA also emits
   event type `loxone_air_button_pressed` with the specific Air serial.

A published Loxone InfoOnlyDigital control titled
`HA Valve | <SERIAL> | Button` is matched to the physical head.
The integration does not create a second device for that mirror.
Loxone may escape/replace special separators (like `|`) in the
visualization: matching is based on the exact serial plus
`HA Valve` / `Button` words, not the punctuation.

**Verification:** In Home Assistant go to Developer Tools → Events,
listen for `loxone_air_button_pressed` and press the head's button.
If no event arrives, inspect `event_uuids` and confirm the physical
button's event UUID is published in LoxAPP3. When the Miniserver does
not expose an event, no HA-side polling setting can guarantee catching
the short electrical pulse. Do not use a polling-only binary entity as
a safety-critical press trigger.

## Confirmed WebSocket button event mapping

An actual press of the WC upstairs Air button was confirmed to produce
`loxone_event` with a digital value `1`, but the key was different
from the physical input entity's UUID. When this happens, the
integration can bind one **verified** event UUID to one physical head,
without touching Loxone Config or affecting the valve setpoint.

In **Home Assistant → Settings → Devices & services → PyLoxone Extended
→ Configure**, locate **Mapowanie przycisków Air** and enter one pair
per line:

```text
<FULL_12_OR_16_HEX_AIR_SERIAL>=<LOXONE_EVENT_UUID>
```

The serial belongs to the physical Air head (for example, its
`TechReport Serial`). The UUID is the event key captured at
**Developer Tools → Events → loxone_event** when pressing that
head's button. Only use an event you verified by pressing that exact
device. Save options; the integration reloads and attaches this UUID
to the physical **Wejście 1** entity.

No guessing based on timing: the mapping rejects invalid or
ambiguous serials/UUIDs and never assigns one event UUID to multiple
physical heads. The mapping is optional for devices whose physical
input connector UUID is already exposed in the Loxone project.

After reloading, check the **Wejście 1** attributes
`event_uuids`, `last_event_uuid`, `press_count_since_reload`,
and `last_press`. Listen for `loxone_air_button_pressed` to trigger
automations on each detected rising edge.

## Safety

A direct HA-only heater controller needs a carefully chosen default
when the Miniserver reboots or HA is unavailable. The Virtual Input
must have a safe starting value or a separate local safety provision
for freeze protection. Do not configure a silent default 0 if this
could allow the rooms/pipes to freeze. A successful write of 5.0
acknowledges the Miniserver input value, **not** physical valve travel.

## Reference

- https://www.loxone.com/plpl/kb/wirtualne-wejscia-i-wyjscia/
- https://www.loxone.com/plpl/kb/web-services/
- https://www.loxone.com/plpl/kb/silownik-air/
