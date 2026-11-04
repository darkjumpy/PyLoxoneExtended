# PyLoxone Extended 2026.11.0b5 — pre-release

> ⚠️ **RELEASE BLOCKER: Automatic wake/keep-awake was explicitly required by the
> user and is NOT implemented. Do not publish b5 as an operational valve
> controller yet.** The present TechReport check only tests whether a
> device responds; it does not initiate wake-up. Before release,
> determine and verify the Loxone Config Keep device awake API, or agree
> on an explicitly delayed, queued delivery mechanism. Neither can be
> represented as immediate wake-up for sleeping battery Air devices.
>

**Status:** Experimental beta for manual tests on a real Home Assistant + Loxone Miniserver.
**Source branch:** `feature/air-valve-manual-controls`.
**Release tag:** `2026.11.0b5`.
**HA integration manifest:** `2026.11.0b5`.

## Added

- Direct manual 0–100% Valve Actuator Air position slider **Bezpośrednie otwarcie** in HA.
- Direct Miniserver device command `/dev/sys/wsdevice/<serial>/DriveTo/<percentage>` with no Loxone Config Virtual Input.
- Strict reply checking: only LL `Code=200`, matching `control`, and `value=ok` acknowledge a request.
- Battery/unknown-powered actuator readiness check via `TechReport` before attempting `DriveTo`. It verifies a report for the correct serial, **not** that the head is awake.
- Read-only per-head `TechReport` diagnostics: reported percentages, calibration state, motor errors and power mode.
- Best-effort RSSI/LQI access with device serial validation. Unsupported fields remain unavailable.
- Shared cached diagnostic requests and read/command parsing tests.

## Breaking/changed behavior

- The b4 manual valve slider used an analog Virtual Input in Loxone Config.
  In b5 the slider sends **direct device-shell commands**. An existing
  Virtual Input is no longer used by this entity.
- The slider shows the *last acknowledged command in the current HA session*,
  not a measured valve opening; it starts in an unknown state.

## Known limitations — please read before installing

- **No automatic wake-up of battery Air heads.** Loxone may reply
  `value=timeout` to DriveTo while the head is sleeping. A successful
  `TechReport` can be served by the Miniserver and **does not ensure**
  that the device is awake. Use Loxone Config's `Keep device awake`
  (then wait until the next Air wake) or the head's physical wake button.
- No blind retry on `timeout`. The command may be pending or unconfirmed.
- Acknowledgement `ok` confirms receipt only; the final motor position
  has not been independently verified.
- Manual hardware tests of `DriveTo/0`, `/1`, and `/100` indicate
  percent-like semantics; intermediate setpoints and long-term persistence
  are not yet verified.
- `RequestedPercent` and `DestinationPercent` from TechReport may remain
  zero even while direct DriveTo moves the motor.
- `GetLinkQuality` via HTTP is not yet confirmed for all firmware/heads.
- Do not use this beta as the sole method of heating or frost protection.

## Deployment

1. Back up HA and any existing `custom_components/loxone` files.
2. Publish the release from the current head of `feature/air-valve-manual-controls`
   with **tag `2026.11.0b5`**, set **pre-release**, and do not mark latest.
3. In HACS choose PyLoxoneExtended `2026.11.0b5`, restart HA or reload
   integration, then find the physical Valve Actuator Air in Devices.
4. Begin tests on one head with no competing room-controller output.
5. Roll back to `2026.11.0b4` if direct control is unsuitable.

## Release validation

- Source syntax and parser smoke checks: .github/workflows/validate-air-diagnostics.yml
- Home Assistant integration validation: .github/workflows/hassfest.yaml
- HACS repository checks: .github/workflows/validate.yaml
  (at the time of preparation, HACS reports repository settings problems:
   missing accepted topics and disabled GitHub Issues; these are not Python
   parser errors).

Full documentation: [Valve manual controls](valve-manual-controls.md).
