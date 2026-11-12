"""Regression tests for authentication recovery after Miniserver restarts.

These tests use no live Miniserver: they exercise a silent websocket
handshake and non-200 cached-token login responses.
"""

from __future__ import annotations

import asyncio

import pytest

from custom_components.loxone.pyloxone_api import connection as conn_mod
from custom_components.loxone.pyloxone_api.connection import LoxoneConnection
from custom_components.loxone.pyloxone_api.exceptions import (
    LoxoneConnectionError,
    LoxoneUnauthorisedError,
)
from custom_components.loxone.pyloxone_api.message import TextMessage


def _connection() -> LoxoneConnection:
    return LoxoneConnection(
        host="192.0.2.1", username="user", password="password", port=8080
    )


def _token_response(code: int) -> TextMessage:
    return TextMessage(
        '{"LL":{"control":"jdev/sys/authwithtoken",'
        '"value":"test","code":"' + str(code) + '"}}'
    )


@pytest.mark.asyncio
async def test_silent_handshake_times_out_and_reconnects(monkeypatch):
    """A websocket that opens but never authenticates must not hang forever."""

    class SilentWebsocket:
        async def send(self, command):
            pass

    async def silent_listener(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(conn_mod, "AUTHENTICATION_TIMEOUT_SECONDS", 0.01)
    conn = _connection()
    conn.connection = SilentWebsocket()
    conn._session_key = b"test-session-key"
    monkeypatch.setattr(conn, "_do_start_listening", silent_listener)

    with pytest.raises(LoxoneConnectionError, match="did not authenticate"):
        await asyncio.wait_for(conn.start_listening(), timeout=1.0)
    assert not conn._authenticated_event.is_set()


@pytest.mark.asyncio
async def test_transient_token_auth_failure_reconnects_without_erasing_token():
    """503 during a restart should restart the session, not enable updates."""
    conn = _connection()
    conn._token.token = "cached-token"

    await conn._websocket_event(_token_response(503))

    assert conn._token.token == "cached-token"
    assert conn._reconnect_event.is_set()
    assert not conn._authenticated_event.is_set()
    assert conn._message_queue.empty()


@pytest.mark.asyncio
async def test_unknown_token_auth_failure_falls_back_to_password():
    """A non-success response must not mark a cached-token session online."""
    conn = _connection()
    conn._token.token = "rejected-token"

    await conn._websocket_event(_token_response(400))

    assert not conn._token.token
    assert conn._reconnect_event.is_set()
    assert not conn._authenticated_event.is_set()
    assert conn._message_queue.empty()


@pytest.mark.asyncio
async def test_server_error_500_preserves_cached_token():
    conn = _connection()
    conn._token.token = "cached-token"

    await conn._websocket_event(_token_response(500))

    assert conn._token.token == "cached-token"
    assert conn._reconnect_event.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [403, 423])
async def test_denied_or_disabled_user_is_not_retried(code):
    conn = _connection()
    with pytest.raises(LoxoneUnauthorisedError):
        await conn._websocket_event(_token_response(code))
    assert not conn._reconnect_event.is_set()


@pytest.mark.asyncio
async def test_successful_token_auth_still_enables_updates():
    conn = _connection()

    await conn._websocket_event(_token_response(200))

    assert conn._authenticated_event.is_set()
    assert not conn._reconnect_event.is_set()
    assert not conn._message_queue.empty()


@pytest.mark.asyncio
async def test_blocked_user_is_fatal_not_retried():
    """4003 means user blocked; never send another login attempt."""
    conn = _connection()

    with pytest.raises(LoxoneUnauthorisedError):
        await conn._websocket_event(_token_response(4003))
    assert not conn._reconnect_event.is_set()
