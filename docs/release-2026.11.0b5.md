# PyLoxone Extended 2026.11.0b5 — experimental prerelease

> **Pending hardware validation:** the natural Air wake detector is implemented,
> but has NOT been validated through a complete 5-minute sleep/wake cycle on
> the user's real Valve Actuator Air. Do not publish b5 as reliable unattended
> room heating control until that test passes.
>
> This feature cannot remotely force an Air head out of deep battery sleep.
> It waits for the next observed natural wake, then attempts delivery.

**Version:** `2026.11.0b5`  
**Branch:** `feature/air-valve-manual-controls`  
**Planned tag:** `2026.11.0b5` (not created)  
**Mark as:** Pre-release

## Main changes

- Automatically discover physical Valve Actuator Air units and their terminals
  from the Miniserver program, without creating Loxone Config Virtual Inputs.
- New `number.Bezpośrednie otwarcie`: queue the latest requested
  `DriveTo/<integer 0–100>` setpoint, rather than sending immediately.
- New `binary_sensor.Polecenie oczekuje na wysłanie`: on when a
  setpoint is waiting for confirmed command acknowledgement; attributes
  include `pending_value`, `queued_at`, last acknowledged value, and
  last error.
- **Latest-value-wins per physical head AND per parameter.**
  If 30% is still pending and 50% is requested, only 50% remains pending.
  Future controls such as brightness and colour temperature are isolated
  by their own parameter keys. This beta implements the actual sender only
  for `valve_position` on Air valve heads.
- Pending setpoints survive Home Assistant reload/restart in local storage.
  Safety expiry: 60 minutes, to prevent very stale heating requests.
- For battery-operated (or unknown-supply) heads, read
  `TechReport` approximately every 5 seconds (5-second HTTP timeout) while pending, verify
  the Air serial, detect candidate new wakes via `WkupCnt[]`, and send
  once during that candidate wake. On unconfirmed `timeout`, leave
  the setpoint pending and await the next distinct wake count.
- An explicit external power mode skips wake detection and sends immediately.
- Reported `TechReport` diagnostic entities and best-effort Air RSSI/LQI.

## Limitations and safety

- **Natural wake timing is not yet confirmed on hardware.** We know a
  real battery Air head returns `WkupCnt[]`, and the device-shell
  `DriveTo/0`, `/1` and `/100` produced motion in manual tests.
  We do **not** yet know if every natural 5-minute polling cycle causes
  `WkupCnt[]` to change or whether a 5-second HTTP probe catches it.
- A matching `TechReport` and changed wake count are *candidate* wake
  evidence, not an absolute guarantee that `DriveTo` will respond before
  the head goes back to sleep.
- A `DriveTo` `value=ok` response acknowledges receipt, not the final
  physical motor position. The number slider shows the **requested target**,
  not measured opening. The pending sensor shows delivery status.
- `value=timeout` is **not** success; unconfirmed commands are not
  blindly repeated within the same observed wake.
- No arbitrary new RGB lighting control is exposed yet. The generic
  per-parameter queue is infrastructure for other Air devices in future.
- Do not use this experimental pre-release as sole frost/heating protection;
  validate the real actuator path first.
- Basic Auth over HTTP exposes credentials on the trusted LAN.
  Never expose Miniserver device-shell commands to the public internet.

## Validation and deployment

- GitHub Actions: Python syntax, pure queue tests, XML and DriveTo parser
  tests, and hassfest.
- GitHub HACS validation currently fails repository metadata checks
  (missing accepted topics and GitHub Issues disabled), not Python checks.
- Before publishing, conduct a real **battery sleep test**: queue 30%, change
  to 50% before the 5-minute wake, observe the pending binary sensor
  stay on, then confirm that only 50% is attempted and `value=ok`
  clears the pending item. Test a timeout and a reload as well.
- Back up HA, install beta from
  `darkjumpy/PyLoxoneExtended` in HACS after a pre-release tag is
  published, and restart Home Assistant.
- Rollback: reinstall `2026.11.0b4`, restart HA, and restore prior
  configuration as needed.

See [full behaviour and diagnostics](valve-manual-controls.md).
