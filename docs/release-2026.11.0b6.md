# PyLoxone Extended 2026.11.0b6 — Air device serial and power-source fixes

**Pre-release**, intended for testing on a real Home Assistant / Loxone installation.

## Fixed since 2026.11.0b5

- **Missing Air valve slider and pending queue indicator:** real Loxone
  programs can store physical Air device identifiers as the `IP`
  attribute rather than `Serial`. Parse the Air device's link-local
  IPv6 EUI-64 identifier, reverse the universal/local bit, and recover
  the exact 8-byte device serial used by
  `/dev/sys/wsdevice/<serial>/DriveTo/<position>`.
- **Power reporting:** the Loxone project's `Battery` flag is only
  pairing/configuration information; it may be stale after attaching
  permanent power. The **Tryb zasilania** sensor now reports the
  **actual** `BatteryPowered` value from this specific head's
  `TechReport`. Before a valid live report, it shows **Nieznane**
  instead of incorrectly claiming `Baterie`.
- The Air command queue now also determines battery vs externally
  powered delivery from **live TechReport**, rather than trusting
  the project flag. Verified externally powered devices can be sent
  commands immediately after a fresh report; battery heads wait for a
  candidate natural wake.
- Diagnostics use the live power mode to select their polling interval,
  avoiding unnecessarily slow polling when the stored project flag
  incorrectly says battery powered.
- Regression tests cover real `IP` EUI-64 conversion, malformed and
  conflicting serial identifiers, and live `BatteryPowered:0` versus
  `BatteryPowered:1`.

## Existing features from b5

- Experimental HA number entity **Bezpośrednie otwarcie**, 0–100%.
- Binary entity **Polecenie oczekuje na wysłanie** per physical head.
- Persistent latest-value-wins queue keyed by *(physical serial, parameter)*.
  E.g. 30% followed by 50% before delivery sends only the newest target.
- For battery devices, checks `TechReport` approximately every five
  seconds **only while commands are pending**, identifies a candidate
  new wake via `WkupCnt[]`, and sends one command per observed wake.
- Strict `DriveTo` `value=ok` acknowledgement; `timeout` leaves
  the command pending until another detected wake. Pending requests
  expire after 60 minutes.
- Read-only physical Air diagnostics and optional signal quality.

## Known experimental limitations

- **Natural wake delivery is still not verified on a complete real
  five-minute Air polling cycle.** A successful diagnostic report or
  an incremented `WkupCnt[]` is not a guaranteed awake window.
- The slider shows **requested target**, not measured mechanical opening.
  `DriveTo` `ok` confirms acknowledgement, not the final position.
- `BatteryPowered` will be **Nieznane** when the physical device's
  `TechReport` cannot be read.
- This beta does **not** actively wake battery devices via radio.
- Keep independent heating/frost protection while testing.
- The repository's HACS publishing checker may report GitHub metadata
  settings (topics, Issues) unrelated to the integration's Python tests.

## Upgrade test

1. Back up HA and upgrade the *darkjumpy/PyLoxoneExtended* custom HACS
   repository to `2026.11.0b6`. Restart Home Assistant.
2. Open the Valve Actuator Air device. Verify that
   **Bezpośrednie otwarcie** and
   **Polecenie oczekuje na wysłanie** appear.
3. For a device confirmed to be externally powered, `TechReport`
   should contain `BatteryPowered:0` and the HA power sensor should
   show **Zasilanie zewnętrzne**. In the battery-powered WC head,
   `BatteryPowered:1` should show **Baterie**.
4. For a sleeping battery head queue 30%, then 50% before its next
   five-minute wake; the queue should hold only 50% and clear after
   acknowledged delivery. Do not rely on this until confirmed on hardware.
5. If any serial remains missing, share a **redacted excerpt of the
   specific device's `LoxAIRDevice` XML attributes** (no full project,
   credentials, or unrelated device identifiers) to diagnose the
   firmware-specific project format.

[Air control and queue diagnostics](valve-manual-controls.md).
