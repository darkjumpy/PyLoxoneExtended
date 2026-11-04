# PyLoxone Extended 2026.11.0b8 — physical Air valve UI and button push beta

**Experimental Pre-release.** Addresses two user-observed issues on b7:
(1) four independently registered Slider devices for HA Valve inputs,
while the physical actuator's own slider remained unavailable;
(2) short physical push-button presses not reaching Home Assistant.

## Main fix: physical valve entity rather than separate Slider devices

- Recognize existing Loxone `Slider` visualization controls named like
  `HA Valve | <SERIAL> | Value`, even when Loxone rewrites the pipe
  separators to backslashes/underscores in its control display name.
- Reassign the **existing visualized Slider entity** (same unique ID) to
  its matching **physical Valve Actuator Air device**, instead of exposing
  it under an extra generic Slider device. No duplicate physical slider
  is created for a matched head.
- Keep ordinary `Slider`/other devices unchanged if their name does not
  match a verified Air head.
- The mapped Slider reads real Loxone setpoint 0–10 but displays HA
  **0–100%** and writes back 0–10. For example 65% sends 6.5.
- The sensor **Wysterowanie zaworu** now also scales normal Loxone analog
  output 0–10 into **0–100%** (previously 5 could be incorrectly shown
  as 5% instead of 50%).
- If no matching visualized Slider exists, the old physical-device
  entity still tries to locate a matching VirtualIn in the installed
  project; serial-based discovery also tolerates Loxone's rewritten
  separators.
- Existing HA entity-registry records belonging to the old b7
  duplicate slider **are not silently deleted**. If HA retains a
  stale unavailable entry, remove only that obsolete entity manually
  after confirming the correct slider works inside the actuator.

## Button push handling — requires Loxone publishing

- A **momentary** push-button is not reliably detected via HTTP polling.
  The Miniserver must publish its digital state UUID through its LoxAPP3
  visualization and WebSocket event stream.
- The integration now recognizes an `InfoOnlyDigital` control explicitly
  named `HA Valve | <SERIAL> | Button` and associates that control's
  UUID / `states` UUIDs with the **existing physical Wejście 1 entity**
  of the correct head, instead of creating an unrelated device.
  An already-published physical UUID is also supported.
- Each observed rising edge updates the binary sensor immediately,
  increments `press_count_since_reload`, stores `last_press`, and
  emits the HA event **`loxone_air_button_pressed`**, including the
  physical Air serial and terminal UUID.
- **If Loxone does not publish an event, the integration cannot invent
  one.** In Loxone Config try enabling the visualization of the
  physical `Wejście 1`, assigning room/category and saving the
  Miniserver program. If unsupported, expose it through an
  info-only/status object with the explicit serial/button name above.
- In HA Developer Tools → Events listen for
  `loxone_air_button_pressed` and press the actuator button to verify.
  The new behavior has **not yet been validated live** on every head.

## Existing b7 behavior

- Control from a normal analog Virtual Input connected in Loxone Config
  to each physical Air actuator. HA setpoints 0–100% become Loxone 0–10.
- No `DriveTo` commands and no active legacy command queue on the valve.
- Configurable backup physical terminal polling 5–600 seconds, default
  120 seconds. Short presses may be missed without visualized events.
- Actual power source from `TechReport` when available.
- Heuristic estimated physical opening is explicitly not measured output.

## Install / migrate

1. Back up Home Assistant and the Loxone Config program.
2. Ensure each physical Air head has an analog Virtual Input connected
   to it. The existing four `HA Valve` inputs can be reused as-is.
3. Update the **darkjumpy/PyLoxoneExtended** HACS custom integration to
   `2026.11.0b8` and restart Home Assistant.
4. Verify one **Otwarcie zadane (HA)** slider inside each physical head.
   Move one slider to 50% and confirm the corresponding Miniserver
   VirtualIn reads 5.0 and the head's ordinary output shows 50%.
5. If any old **unavailable** entity remains in the physical head after
   the upgrade, remove only that stale registry entry through HA's
   entity management, not a currently working valve control.
6. Publish `Wejście 1` through Loxone visualization and confirm a
   button event in HA Developer Tools. No short-press detection
   guarantee is made when the physical input is not published.

**Safety:** HA-only valve control needs suitable local default/fallback
for Home Assistant outages and frost protection. Do not use the beta
as the sole safety-critical protection.

[Full setup and button-event instructions](valve-virtual-input-control.md).
