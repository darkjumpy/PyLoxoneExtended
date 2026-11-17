"""Keep optional Audio and Air valve features working after HTTPS upgrades.

The main Loxone WebSocket connection can be healthy while separate
HTTP clients still use the original HTTP:80 options and silently lose
authorization after a 308. These tests pin their address selection.
"""

from types import SimpleNamespace

import pytest

from custom_components.loxone.audio_client import AudioStatusClient
from custom_components.loxone.valve_air import _endpoint
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD


def _entry(host="192.168.2.253", port=80, runtime=None):
    return SimpleNamespace(
        options={
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "test-password",
        },
        runtime_data=runtime,
    )


def test_air_valve_discovery_uses_verified_https_after_308():
    """The options stay LAN HTTP:80, but Air API must use HTTPS:443."""
    connection = SimpleNamespace(
        scheme="https",
        url="192-168-2-253.504f94d17bce.dyndns.loxonecloud.com",
    )
    base, auth = _endpoint(_entry(runtime=connection))
    assert base == "https://192-168-2-253.504f94d17bce.dyndns.loxonecloud.com:443"
    assert auth.login == "admin"
    assert auth.password == "test-password"


def test_air_valve_auth_uses_utf8_and_normal_port():
    entry = _entry(host="https://loxone.example", port=443)
    base, auth = _endpoint(entry)
    assert base == "https://loxone.example:443"
    assert auth.encoding.lower() == "utf-8"


def test_air_valve_http_only_legacy_unchanged():
    base, _auth = _endpoint(_entry(host="192.168.2.253", port=80))
    assert base == "http://192.168.2.253:80"


def test_air_valve_nondefault_port_unchanged():
    base, _auth = _endpoint(_entry(host="192.168.2.253", port=8080))
    assert base == "http://192.168.2.253:8080"


def test_air_valve_live_transport_updates_on_reconnect():
    connection = SimpleNamespace(scheme="http", url="192.168.2.253")
    entry = _entry(runtime=connection)
    assert _endpoint(entry)[0] == "http://192.168.2.253:80"
    connection.scheme = "https"
    connection.url = "192-168-2-253.504f94d17bce.dyndns.loxonecloud.com"
    assert _endpoint(entry)[0].startswith("https://")


@pytest.mark.parametrize(("configured_host", "expected_host"), [
    ("192.168.2.253", "192.168.2.253"),
    ("https://192-168-2-253.504f94d17bce.dyndns.loxonecloud.com",
     "192-168-2-253.504f94d17bce.dyndns.loxonecloud.com"),
    ("http://192.168.2.253:80", "192.168.2.253"),
])
def test_compact_audio_parses_full_url(configured_host, expected_host):
    client = AudioStatusClient(None, configured_host)
    assert client._base_url == f"http://{expected_host}:7091"


@pytest.mark.parametrize("bad_host", [
    "https://admin:pwd@loxone.example",
    "https://loxone.example/path",
    "https://loxone.example/?key=secret",
    "https://loxone.example/#fragment",
    "",
])
def test_compact_audio_rejects_unsafe_or_invalid_hosts(bad_host):
    with pytest.raises(ValueError):
        AudioStatusClient(None, bad_host)
