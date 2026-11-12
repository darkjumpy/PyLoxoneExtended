# PyLoxone Extended 2026.11.0b11 — secure local HTTP → HTTPS upgrade

**Experimental pre-release, based on b10.** All existing b10 audio, Air valve, diagnostics, button events and Miniserver restart/reconnect features are retained.

## Fixed: HTTP 308 followed by 401 Unauthorized

Some Loxone Compact devices advertise an HTTP port 80 but respond to `/jdev/cfg/apiKey` with a **308 redirect** to their HTTPS Loxone Cloud-DNS hostname (for example, `https://192-168-2-253.<12-digit-device-serial>.dyndns.loxonecloud.com`). The normal HTTP client follows this cross-host redirect and loses HTTP Basic authentication, so a valid username and password may appear to be rejected.

On a configured RFC1918 LAN IPv4 + port 80, the integration now:

1. Probes `/jdev/cfg/apiKey` without credentials, with automatic redirects disabled.
2. Accepts **only** HTTPS port 443 on a `<same-ip-hyphenated>.<12-hex-serial>.dyndns.loxonecloud.com` hostname, with an unchanged API path and no query, fragment or embedded user information. No arbitrary redirects are followed.
3. Uses authenticated HTTPS after the target is validated and strictly verifies the TLS certificate, even if the previous integration setting disabled verification for a manually configured HTTPS address.
4. Switches all subsequent bootstrap requests to HTTPS and the Miniserver WebSocket to WSS. It does so even when the device's API response contains `local: true`.
5. Supports legacy HTTP-only Miniservers: if the unauthenticated probe does not redirect, it retries the original HTTP URL with Basic authentication. Redirects only offered after authentication are also validated.
6. Rejects chained or untrusted redirects without sending credentials to their destinations. Non-200 HTTPS authentication errors are still reported as errors.

## Regression coverage

An isolated test suite checks valid redirects, rejected foreign/wrong-IP/downgraded/rewritten-path redirects, absent credentials on the probe, valid legacy HTTP, HTTPS auth errors, chained redirects, and propagation to WebSocket URL and TLS verification. It runs together with the existing b10 connection tests before publishing.

## Installation

Select **2026.11.0b11** as the beta release in HACS, then restart Home Assistant. Existing installations already configured to a working HTTPS Cloud-DNS host on port 443 do not need to change their settings. To test the new automatic path, use the original **LAN IPv4 address**, port **80**, and your normal Miniserver credentials; take an HA backup first. Do not disable certificate verification to get around TLS errors.

**Limits:** The CI tests simulate network responses; a live Miniserver reboot/upgrade should still be verified on the owner's installation. The integration does not modify the Miniserver's network configuration. Previous b10 is retained for rollback.
