# PyLoxoneExtended 2026.11.0b12 — Audio & Valve Air endpoint recovery

**Beta, based on b11.** Keeps all b9/b10/b11 functionality, including the secure HTTP 308 → HTTPS 443/WSS automatic redirect.

## Why features disappeared after enabling HTTPS

Git tree comparisons confirm **AudioZoneV2 metadata, Air valve entity discovery, Virtual Input setpoints, diagnostics, and button events were never removed** from b11. However, optional subsystems use separate HTTP clients and relied on the *original* configured host/port rather than the verified destination of the main Miniserver transport:

- Valve Air program discovery (`/dev/fslist/prog`, `/dev/fsget/prog/...`), diagnostics, Virtual Input commands and fallback polling could still call LAN HTTP:80. On Miniserver Compact that redirects to the Cloud-DNS HTTPS hostname; aiohttp drops Basic credentials when the host changes and topology discovery silently returns no devices.
- AudioStatusClient previously passed the complete configured host directly into `yarl.URL.build(host=...)`. With user-configured `https://...`, this is not a valid bare hostname and may stop audio platform setup.

## Changes

- Preserve the *live resolved connection* in the Home Assistant ConfigEntry runtime data after the main API bootstraps. Valve Air helper uses the active HTTP/HTTPS scheme and host, including post-reconnect updates, without rewriting user settings or persisting tokens.
- Use the effective HTTPS:443 target rather than stale HTTP:80 for separate valve API requests; keep existing legacy HTTP and custom ports working.
- Parse bare IP, hostname and explicit `https://` URLs into a valid hostname for the audio status service on port 7091. Reject URLs containing embedded credentials, non-root paths, queries and fragments.
- Clear the transient resolved endpoint on entry unload.
- Add regression tests for automatic upgrades, live transport switching, HTTP-only setups, and audio URL normalization.

## Verification

GitHub Actions runs focused tests of the new endpoints plus existing connection, redirect, audio, Air valve, migration and integration tests. This does **not** substitute for a live-Miniserver test.

## Installation / troubleshooting

Select `2026.11.0b12` as a pre-release in HACS, restart Home Assistant, then reload the **existing** working Miniserver config entry. Back up HA first and do not delete/recreate the entry: doing so may change entity IDs and automation references.

Keep HTTPS host / 443 if it currently works, or use LAN IP / 80 to exercise the automatic redirect. If Valve Air entities are still missing, collect the logs for `custom_components.loxone.valve_air` and check that the Loxone project exposes physical Air devices; also check disabled/hidden entities in the Entity Registry. The AudioServer 7091 metadata protocol is still experimental and may depend on firmware/audio endpoint support. Existing b11 remains available for rollback.

This fixes endpoint routing; it does not add new features beyond b11.
