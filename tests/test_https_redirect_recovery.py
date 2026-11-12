"""Secure HTTP → HTTPS upgrade tests for Loxone Compact Cloud-DNS redirects."""

import json

import pytest

from custom_components.loxone.pyloxone_api.const import CMD_GET_API_KEY
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection
from custom_components.loxone.pyloxone_api.exceptions import LoxoneUnauthorisedError
from custom_components.loxone.pyloxone_api.loxone_http_client import (
    LoxoneAsyncHttpClient,
    _local_http_bootstrap,
    _validated_loxone_https_redirect,
)

IP = "192.168.2.253"
FQDN = "192-168-2-253.504f94d17bce.dyndns.loxonecloud.com"
HTTP_URL = f"http://{IP}{CMD_GET_API_KEY}"
HTTPS_URL = f"https://{FQDN}{CMD_GET_API_KEY}"


class _Response:
    def __init__(self, status, url, *, location=None, body=b"ok"):
        self.status = status
        self.url = url
        self.headers = {"Location": location} if location is not None else {}
        self.content = self
        self.body = body

    async def read(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Session:
    closed = False

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def _client(session, *, verify_ssl=False):
    return LoxoneAsyncHttpClient(
        url=IP, username="admin", password="test-password",
        scheme="http", verify_ssl=verify_ssl, session=session,
    )


@pytest.mark.asyncio
async def test_secure_upgrade_never_sends_credentials_in_http_probe():
    session = _Session([
        _Response(308, HTTP_URL, location=HTTPS_URL),
        _Response(200, HTTPS_URL),
        _Response(200, f"https://{FQDN}/data/LoxAPP3.json"),
    ])
    client = _client(session, verify_ssl=False)

    async with client.get(CMD_GET_API_KEY) as response:
        assert await response.content.read() == b"ok"

    assert len(session.calls) == 2
    first_url, first_options = session.calls[0]
    assert first_url == HTTP_URL
    assert "auth" not in first_options
    assert first_options["allow_redirects"] is False

    second_url, second_options = session.calls[1]
    assert second_url == HTTPS_URL
    assert second_options["auth"].login == "admin"
    assert second_options["auth"].password == "test-password"
    assert second_options["ssl"] is True
    assert second_options["allow_redirects"] is False

    assert client.scheme == "https"
    assert client.base_url == f"https://{FQDN}"
    assert client.upgraded_https
    assert client.verify_ssl  # automatic upgrades enforce TLS verification
    async with client.get("/data/LoxAPP3.json") as response:
        assert response.status == 200
    assert session.calls[2][0] == f"https://{FQDN}/data/LoxAPP3.json"
    assert session.calls[2][1]["ssl"] is True


@pytest.mark.parametrize("location", [
    f"https://attacker.example{CMD_GET_API_KEY}",
    f"http://{FQDN}{CMD_GET_API_KEY}",
    f"https://10-0-0-9.504F94D17BCE.dyndns.loxonecloud.com{CMD_GET_API_KEY}",
    f"https://{FQDN}:8443{CMD_GET_API_KEY}",
    f"https://admin:secret@{FQDN}{CMD_GET_API_KEY}",
    f"https://{FQDN}/data/LoxAPP3.json",
    f"https://{FQDN}{CMD_GET_API_KEY}?secret=1",
    f"https://{FQDN}{CMD_GET_API_KEY}#fragment",
    f"//{FQDN}{CMD_GET_API_KEY}",
])
@pytest.mark.asyncio
async def test_redirect_rejects_untrusted_host_path_or_scheme(location):
    session = _Session([_Response(308, HTTP_URL, location=location)])
    client = _client(session)
    with pytest.raises(RuntimeError, match="Untrusted Miniserver redirect"):
        async with client.get(CMD_GET_API_KEY):
            pass
    assert len(session.calls) == 1
    assert "auth" not in session.calls[0][1]
    assert not client.upgraded_https


@pytest.mark.asyncio
async def test_redirect_after_authenticated_401_challenge_still_safe():
    session = _Session([
        _Response(401, HTTP_URL),
        _Response(308, HTTP_URL, location=HTTPS_URL),
        _Response(200, HTTPS_URL),
    ])
    client = _client(session)
    async with client.get(CMD_GET_API_KEY) as response:
        assert response.status == 200
    assert len(session.calls) == 3
    assert "auth" not in session.calls[0][1]
    assert session.calls[1][1]["auth"].login == "admin"
    assert session.calls[2][1]["auth"].login == "admin"
    assert session.calls[2][1]["ssl"] is True


@pytest.mark.asyncio
async def test_legacy_http_stays_supported_without_redirect():
    session = _Session([
        _Response(401, HTTP_URL),  # unauthenticated probe
        _Response(200, HTTP_URL),  # authenticated fallback
    ])
    client = _client(session)
    async with client.get(CMD_GET_API_KEY) as response:
        assert response.status == 200
    assert client.scheme == "http"
    assert not client.upgraded_https
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_direct_https_unchanged():
    session = _Session([_Response(200, HTTPS_URL)])
    client = LoxoneAsyncHttpClient(
        url=FQDN, username="admin", password="pwd", scheme="https",
        verify_ssl=True, session=session,
    )
    async with client.get(CMD_GET_API_KEY) as response:
        assert response.status == 200
    assert len(session.calls) == 1
    assert session.calls[0][1]["ssl"] is True


@pytest.mark.asyncio
async def test_https_401_is_real_auth_failure_not_ignored():
    session = _Session([
        _Response(308, HTTP_URL, location=HTTPS_URL),
        _Response(401, HTTPS_URL, body=b"bad credentials"),
    ])
    client = _client(session)
    with pytest.raises(LoxoneUnauthorisedError):
        async with client.get(CMD_GET_API_KEY):
            pass
    assert not client.upgraded_https
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_chained_redirect_is_rejected():
    session = _Session([
        _Response(308, HTTP_URL, location=HTTPS_URL),
        _Response(308, HTTPS_URL, location=HTTPS_URL),
    ])
    client = _client(session)
    with pytest.raises(RuntimeError, match="Refusing chained"):
        async with client.get(CMD_GET_API_KEY):
            pass
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_redirect_from_non_api_endpoint_never_replays_credentials():
    session = _Session([
        _Response(308, f"http://{IP}/data/LoxAPP3.json", location=HTTPS_URL),
    ])
    client = _client(session)
    with pytest.raises(RuntimeError, match="Cannot trust an HTTPS redirect"):
        async with client.get("/data/LoxAPP3.json"):
            pass
    assert len(session.calls) == 1


def test_local_http_guard_only_accepts_rfc1918_ipv4_port_80():
    assert _local_http_bootstrap(f"http://{IP}", CMD_GET_API_KEY)
    assert not _local_http_bootstrap("http://192.0.2.1", CMD_GET_API_KEY)
    assert not _local_http_bootstrap(f"http://{IP}:8080", CMD_GET_API_KEY)
    assert not _local_http_bootstrap(f"https://{IP}", CMD_GET_API_KEY)
    assert not _local_http_bootstrap(f"http://{IP}", "/other")


def test_redirect_guard_accepts_only_matching_local_loxone_domain():
    assert _validated_loxone_https_redirect(f"http://{IP}", CMD_GET_API_KEY, HTTPS_URL) == (
        f"https://{FQDN.lower()}"
    )


def _ll_response(value):
    return json.dumps({
        "LL": {"code": 200, "control": "dev/cfg/apiKey", "value": value}
    }).encode()


@pytest.mark.asyncio
async def test_connection_open_switches_subsequent_http_and_ws_to_tls():
    """Regression: apiKey responds local=True, yet requires an HTTPS upgrade."""
    from Crypto.PublicKey import RSA

    rsa_key = RSA.generate(2048)
    cert = rsa_key.publickey().export_key().decode().replace(
        "BEGIN PUBLIC KEY", "BEGIN CERTIFICATE"
    ).replace("END PUBLIC KEY", "END CERTIFICATE")
    api_payload = _ll_response(json.dumps({
        "version": "17.3.11.10", "snr": "504F94D17BCE",
        "local": True, "address": IP,
    }))
    session = _Session([
        _Response(308, HTTP_URL, location=HTTPS_URL),
        _Response(200, HTTPS_URL, body=api_payload),
        _Response(200, f"https://{FQDN}/data/LoxAPP3.json", body=b"{}"),
        _Response(200, f"https://{FQDN}/jdev/sys/getPublicKey", body=_ll_response(cert)),
    ])
    connection = LoxoneConnection(
        host=IP, username="admin", password="pwd", port=80, verify_ssl=False
    )
    await connection.open(session=session, connect_websocket=False)
    assert connection.scheme == "https"
    assert connection.url == FQDN.lower()
    assert connection.verify_ssl
    assert connection._websocket_deferred
    assert [item[0] for item in session.calls] == [
        HTTP_URL, HTTPS_URL,
        f"https://{FQDN}/data/LoxAPP3.json",
        f"https://{FQDN}/jdev/sys/getPublicKey",
    ]
    assert all(item[1].get("ssl") is True for item in session.calls[1:])
