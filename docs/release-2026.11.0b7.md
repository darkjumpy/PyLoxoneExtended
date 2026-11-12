# PyLoxone Extended 2026.11.0b7 — Valve Actuator Air via analog Virtual Inputs

**Pre-release / experimental.** Supersedes b5/b6's direct device-shell
`DriveTo` control, which was observed to move a head temporarily and then
allow normal Loxone control to restore its previous position.

## New valve control

- The physical Valve Actuator Air device now exposes **Otwarcie zadane (HA)**
  as a 0–100% number slider, assigned to that physical device.
- The slider sends values to an **existing Loxone analog Virtual Input**
  (0–10). For example, 30% writes 3.0 and 65% writes 6.5.
- Each head must have one uniquely named, **manually created and physically
  wired** Virtual Input in the Loxone Config project:
  `HA Valve | <FULL_AIR_SERIAL> | Value`
  (legacy exact-name format `HA Valve | <LOXONE_HEAD_TITLE> | Value` is
  also supported).
- Home Assistant discovers the input from the **installed** Loxone program,
  matches it to the physical head and writes through
  `/jdev/sps/io/<VirtualInputUUID>/<value>`. It reads back the value
  directly from the Miniserver to synchronize the slider.
- The existing slider's unique ID is reused for entity registry continuity,
  although its visible name and control mechanism have changed.
- If the matching input does not exist, is explicitly digital, or has an
  ambiguous name, the slider is **unavailable**; the integration does
  **not** issue a fallback `DriveTo`.
- Former **Polecenie oczekuje na wysłanie** binary entities are not created
  any more in this mode. Previously saved DriveTo commands are not re-sent.
  Loxone controls Air delivery on its own normal radio cycles.

## Other improvements

- Adjustable **Air physical terminal fallback polling** in integration
  settings, 5–600 seconds, default 120, for sensor/physical button entities.
  Uses Miniserver websocket state events immediately where exposed;
  some physical inputs may not be included in those events, and short
  button presses may not be seen by HTTP polling.
- Power-source reporting uses live `TechReport BatteryPowered` when
  readable; head power remains **Nieznane** until a confirmed report.
- Additional **read-only, estimated** physical opening sensor from
  calibrated/idle `TechReport.Pos` and `MaxPos`; it is heuristic,
  not verified position feedback and is not used for control.
- Direct `DriveTo` is no longer called by valve entities, eliminating
  actuator fighting caused by repeated device-shell commands.

## Manual setup required before enabling valve control

1. In **Loxone Config** add a **Virtual Input** for **each** head and
   configure it as an **analog input** with minimum 0, maximum 10,
   and step/minimum change 0.1.
2. Name it precisely `HA Valve | <FULL_AIR_SERIAL> | Value`.
3. Wire the Virtual Input's output to the **analog input of that head's
   physical Valve Actuator Air** on the programming page.
4. Pick safe startup/failover settings; because HA is the sole controller,
   loss of HA, Miniserver reset, or communication loss must **not**
   eliminate needed heat or freeze protection.
5. Save the program to the Miniserver. Then **reload this integration**
   so it fetches the new project topology.

**No automatic Loxone Config project modification** is done by
PyLoxoneExtended. Successful API writes confirm a Miniserver input update,
NOT actual Air motor travel. The head still follows its configured Air
communication interval.

## Validation and limitations

- Python compile checks and standalone command-queue unit tests are included
  in release CI; parser smoke tests cover Virtual Input discovery, naming,
  mapping and guard against direct DriveTo calls.
- HA entity-to-Virtual Input behavior and sustained motor travel have
  **not yet been independently tested on the owner's live Loxone system**.
  Test with one head first, with a safe heating fallback.
- HACS repository validation has pre-existing **repository settings**
  failures (missing acceptable GitHub topics and disabled Issues).
  These are separate from Python integration validation.
- Do not use the release as the sole safety-critical frost-protection layer.

[Step-by-step configuration guide](valve-virtual-input-control.md).
