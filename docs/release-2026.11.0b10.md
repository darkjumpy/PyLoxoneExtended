# PyLoxone Extended 2026.11.0b10 — Miniserver restart recovery

**Experimental pre-release.** Built from `master` after merging PRs #1, #2 and #3. All previous 2026.11.0b9 valve and audio functionality is preserved, along with new WebSocket authentication recovery changes.

## Restart and authentication stability

- A new WebSocket connection must complete token/password authentication within 30 seconds. A stalled exchange triggers a reconnect with a fresh HTTP bootstrap, including a new public key.
- State updates are enabled only following a confirmed `authwithtoken` success (code 200), not after any non-401 response.
- A temporary authentication response (500 / 503 / 901) keeps the cached token and reconnects. Other non-success responses follow existing invalid-token recovery, while 401 cached-token responses fall back to password authentication.
- Blocked or disabled users / forbidden responses (4003, 403, 423) are treated as fatal, avoiding repeated login attempts.
- Reconnect retry backoff resets after a successful authenticated session, and the previous session's authenticated flag is cleared at teardown.
- Added isolated regression tests covering a silent handshake, 500/503 responses, invalid tokens, and blocked users.

## Features retained from earlier beta releases

- AudioZoneV2 now-playing metadata on existing media players (experimental endpoint/format).
- Discovery, monitoring, diagnostics and event association for physical Valve Actuator Air devices.
- `Otwarcie zadane (HA)` via normal analog Loxone Virtual Inputs 0–10 rather than direct `DriveTo` commands.
- Previously added valve slider mapping, user-provided button event UUID bindings and `loxone_air_button_pressed` events.

## Validation and rollout

Publishing is gated by automated Python syntax and focused connection/auth regression tests. These tests simulate a Miniserver and **do not prove operation on a live Miniserver Compact**. Before using b10 as your main heating control, back up Home Assistant and your Loxone configuration, install the pre-release via HACS, restart HA, and test Miniserver reboot/reconnection with entities becoming available again. In particular check Air head setpoints and local fallback/frost protection. Keep the current b9 as a rollback option.

**Important:** Publishing this tag does not merge the experimental reconnect fix into `master`. PR #4 tracks the fix separately until validated.
