# PyLoxone Extended 2026.11.0b9 — Air Input 1 WebSocket event mapping

**Experimental pre-release.** This is a targeted fix for a user-confirmed
physical Air head button press that arrived in HA as a `loxone_event`
but was not applied to the **Wejście 1** binary sensor.

## What is fixed

- The integration now discovers possible published event UUIDs from
  the physical Loxone project output connectors (`Co K="Q"`, etc.) rather
  than listening only to the physical terminal's own `U` identifier.
- Visualized `InfoOnlyDigital` UUIDs and the physical terminal's output
  connector UUIDs are matched to the **same physical Valve Actuator Air
  / Wejście 1** entity. No separate valve device or new Slider is created.
- Added optional multiline integration setting **Mapowanie przycisków Air**.
  Specify one *verified* `SERIAL=UUID` binding per line. For example:

  ```text
  504F94FFFE000001=11111111-2222-3333-4444-555555555555
  ```

  These are illustrative, non-device-specific identifiers. Actual serial
  comes from the physical head's `TechReport`; event UUID comes from
  HA Developer Tools → Events → `loxone_event` when pressing the head.
- Mappings are validated, reject duplicate assignments of one event UUID
  to two heads, and cannot be guessed from unrelated event traffic.
- On each received 0→1 transition, **Wejście 1** updates immediately,
  its `last_press` and `press_count_since_reload` attributes update,
  and HA emits `loxone_air_button_pressed`.
- **Wejście 1** now also reports `event_uuids` and `last_event_uuid`
  attributes for troubleshooting.
- An occasional failed HTTP poll from a sleeping Air head no longer marks
  an otherwise event-backed physical button unavailable.
- Pure regression tests cover configured binding validation, extracted
  program output connector UUIDs and event handling.

## What has NOT changed

- Existing **Otwarcie zadane (HA)** controls stay associated with physical
  Air heads. No changes to working analog Virtual Input 0–10 control.
- No direct `DriveTo` for normal heating control.
- Other Air sensors and configurable HTTP polling remain as in b8.
- No changes to Loxone Config project files, and no automatic modification
  of Miniserver programming.

## User test after installing b9

1. Update the HACS custom integration to `2026.11.0b9`, restart HA.
2. In the PyLoxone Extended integration options, paste
   `HEAD_SERIAL=CONFIRMED_BUTTON_EVENT_UUID` into the new mapping field.
   A button press may already work without an override when its connector
   UUID is readable from the saved Loxone program.
3. Save options. The integration reloads.
4. Open **Wejście 1** for the matching head: confirm the UUID is in
   `event_uuids`, press the button, and verify `last_event_uuid`,
   `last_press`, and `press_count_since_reload`.
5. To use the trigger in an automation, listen for event
   `loxone_air_button_pressed`.

The user has already confirmed `loxone_event` changes on button presses,
but the new UUID association is **not yet tested on their live system**.
The integration cannot detect an event that the Miniserver does not
actually publish. Short button presses should not be polled blindly.
Keep the separate heating/frost protection strategy.

The repository's independent HACS publishing validator continues to
report pre-existing GitHub metadata failures: no accepted repository
topics, and Issues disabled. Python parser tests and hassfest are
separate from those repository settings.

[Detailed event setup](valve-virtual-input-control.md).
