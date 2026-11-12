"""
Component to create an interface to the Loxone Miniserver.

For more details about this component, please refer to the documentation at
https://github.com/JoDehli/pyloxone-api
"""

import asyncio
import ipaddress
import logging
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urlsplit

import aiohttp

from .const import CMD_GET_API_KEY, TIMEOUT
from .exceptions import (LoxoneMaxNumOfConnectionsError,
                         LoxoneServiceUnAvailableError,
                         LoxoneUnauthorisedError,
                         LoxoneUnrecognizedCommandError)

_LOGGER = logging.getLogger(__name__)

_REDIRECT_CODES = frozenset({301, 302, 307, 308})
_PRIVATE_LANS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"
))
_LOXONE_DYNDNS_RE = re.compile(
    r"^(?P<ip>\d{1,3}(?:-\d{1,3}){3})\."
    r"(?P<serial>[0-9a-f]{12})\.dyndns\.loxonecloud\.com$",
    re.IGNORECASE,
)


def _local_http_bootstrap(base_url: str, endpoint: str) -> bool:
    """Only probe RFC1918 IPv4 Miniserver addresses on their HTTP port."""
    if endpoint != CMD_GET_API_KEY:
        return False
    try:
        parsed = urlsplit(base_url)
        address = ipaddress.ip_address(parsed.hostname or "")
        return (
            parsed.scheme == "http"
            and parsed.port in (None, 80)
            and isinstance(address, ipaddress.IPv4Address)
            and any(address in network for network in _PRIVATE_LANS)
        )
    except ValueError:
        return False


def _validated_loxone_https_redirect(
    base_url: str, endpoint: str, location: str
) -> str:
    """Accept only a secure Loxone Cloud-DNS name encoding our *own* LAN IP.

    Never copy Basic credentials to a generic redirect target. HTTPS certificate
    verification is mandatory on this upgrade, even if the integration has
    manually disabled TLS verification for other direct connections.
    """
    if not _local_http_bootstrap(base_url, endpoint):
        raise ValueError("Cannot trust an HTTPS redirect from this origin")

    source = urlsplit(base_url)
    destination = urlsplit(location)
    hostname = destination.hostname or ""
    match = _LOXONE_DYNDNS_RE.fullmatch(hostname)
    if (
        destination.scheme != "https"
        or destination.port not in (None, 443)
        or destination.username is not None
        or destination.password is not None
        or destination.path != endpoint
        or destination.query
        or destination.fragment
        or not match
        or match.group("ip").replace("-", ".") != source.hostname
    ):
        raise ValueError(
            "Untrusted Miniserver redirect; expected this LAN IP on a "
            "verified Loxone Cloud-DNS HTTPS hostname and the same API path"
        )
    return f"https://{hostname}"


class LoxoneAsyncHttpClient:
    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        scheme: str = "http",
        verify_ssl: bool = True,
        session: aiohttp.ClientSession = None,
    ):
        # Validate input parameters
        if not url:
            raise ValueError("URL cannot be empty")
        if not username:
            raise ValueError("Username cannot be empty")
        if not password:
            raise ValueError("Password cannot be empty")
        if scheme not in ("http", "https"):
            raise ValueError(f"Invalid scheme '{scheme}'. Must be 'http' or 'https'")
        if not isinstance(verify_ssl, bool):
            raise ValueError("verify_ssl must be a boolean")

        # super().__init__()
        if session is None:
            self.session = aiohttp.ClientSession()
            self._own_session = True
        # session.auth = aiohttp.BasicAuth(username, password)
        else:
            if session.closed:
                raise ValueError("Provided session is already closed")
            self.session = session
            self._own_session = False

        self.timeout = TIMEOUT
        self.base_url = f"{scheme}://{url}"
        self.scheme = scheme
        self.verify_ssl = verify_ssl
        self.username = username
        self.password = password
        self._closed = False
        # Exposed to connection.open() so WebSocket reconnects use WSS too.
        self.upgraded_https = False

    @asynccontextmanager
    async def get(
        self, endpoint: str
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """Yield the ``aiohttp.ClientResponse`` for ``endpoint``.

        The response is yielded inside an ``async with`` block that
        wraps the underlying ``self.session.get(...)`` call, so the
        aiohttp response and its connection-pool slot are released
        as soon as the caller exits the block — no leaks, no manual
        ``response.close()`` calls. Non-200 statuses raise before the
        yield (see :meth:`_handle_error`); the caller's body-read
        inside the ``async with`` is therefore guaranteed to be on a
        200-status response.
        """
        if self._closed:
            raise RuntimeError("HTTP client has been closed")

        if not endpoint:
            raise ValueError("Endpoint cannot be empty")

        if not endpoint.startswith("/"):
            _LOGGER.warning(f"Endpoint '{endpoint}' should start with '/'")
            endpoint = f"/{endpoint}"

        url = f"{self.base_url}{endpoint}"
        _LOGGER.debug("Making GET request to: %s", url)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        request_kwargs = {
            "auth": aiohttp.BasicAuth(self.username, self.password, encoding="utf-8"),
            "timeout": timeout,
            # Never let aiohttp follow a cross-origin redirect on our behalf.
            "allow_redirects": False,
        }
        if self.scheme == "https":
            request_kwargs["ssl"] = self.verify_ssl

        try:
            new_base_url = None
            request_url = url
            if _local_http_bootstrap(self.base_url, endpoint):
                # Probe without credentials, so a 308 HTTP→HTTPS upgrade
                # does not expose the password over plaintext HTTP.
                async with self.session.get(
                    url, timeout=timeout, allow_redirects=False
                ) as probe:
                    if probe.status in _REDIRECT_CODES:
                        new_base_url = _validated_loxone_https_redirect(
                            self.base_url, endpoint, probe.headers.get("Location", "")
                        )
                        request_url = f"{new_base_url}{endpoint}"
                        request_kwargs["ssl"] = True

            # Response contexts stay open while callers read the body.
            async with self.session.get(request_url, **request_kwargs) as response:
                if response.status in _REDIRECT_CODES:
                    if new_base_url is not None:
                        raise ValueError("Refusing chained HTTPS redirects")
                    # Some Miniservers only redirect a request with Basic
                    # credentials. Check the target before *any* second GET.
                    new_base_url = _validated_loxone_https_redirect(
                        self.base_url, endpoint, response.headers.get("Location", "")
                    )
                    request_kwargs["ssl"] = True
                    async with self.session.get(
                        f"{new_base_url}{endpoint}", **request_kwargs
                    ) as upgraded_response:
                        if upgraded_response.status != 200:
                            await self._handle_error(upgraded_response)
                        self._remember_verified_https(new_base_url)
                        yield upgraded_response
                else:
                    if response.status != 200:
                        await self._handle_error(response)
                    if new_base_url is not None:
                        self._remember_verified_https(new_base_url)
                    yield response

        except asyncio.TimeoutError as err:
            # Transient: the caller retries and reports it once.
            _LOGGER.debug("Timeout error for %s", url)
            raise TimeoutError(
                f"Request to {url} timed out after {self.timeout} seconds"
            ) from err

        # The previous implementation listed seven specific
        # ``aiohttp.ClientError`` subclasses (ClientConnectionError,
        # ClientConnectorError, ClientSSLError, ClientProxyConnectionError,
        # ServerDisconnectedError, ClientPayloadError,
        # ClientResponseError) — all of which are subclasses of
        # ``ClientError`` and produced the same ERROR-log + re-raise
        # pattern. They are subsumed by the single ``ClientError``
        # handler below.
        except aiohttp.ClientError as err:
            # Transient: the caller retries and reports it once.
            _LOGGER.debug("HTTP client error for %s: %s", url, err)
            raise ConnectionError(
                f"Failed to connect to Loxone Miniserver at {url}: {err}"
            ) from err

        except (
            LoxoneUnauthorisedError,
            LoxoneUnrecognizedCommandError,
            LoxoneServiceUnAvailableError,
            LoxoneMaxNumOfConnectionsError,
        ):
            # Re-raise Loxone-specific errors without wrapping — these
            # are raised inside ``_handle_error`` and are meaningful
            # to the caller (e.g. supervisor reload on
            # ``LoxoneUnauthorisedError``).
            raise

        except Exception as err:
            _LOGGER.exception(f"Unexpected error during GET request to {url}")
            raise RuntimeError(
                f"Unexpected error during HTTP request: {err}"
            ) from err

    def _remember_verified_https(self, https_base_url: str) -> None:
        """Persist verified HTTPS for subsequent bootstrap GETs."""
        self.base_url = https_base_url
        self.scheme = "https"
        # Automatic redirects always use verified TLS, including later
        # LoxAPP3.json and getPublicKey bootstrap requests.
        self.verify_ssl = True
        self.upgraded_https = True
        _LOGGER.info("Verified Loxone HTTPS redirect; using %s", https_base_url)

    async def close(self):
        if self._closed:
            _LOGGER.warning("HTTP client is already closed")
            return

        try:
            if self._own_session and not self.session.closed:
                await self.session.close()
            self._closed = True
            _LOGGER.debug("HTTP client closed successfully")
        except Exception as err:
            _LOGGER.error(f"Error closing HTTP client: {err}")
            self._closed = True
            raise RuntimeError(f"Failed to close HTTP session: {err}") from err

    @staticmethod
    async def _handle_error(response):
        content = None

        try:
            # Try to read content with timeout protection
            try:
                content = await asyncio.wait_for(response.content.read(), timeout=5.0)
                content = content.decode("utf-8", errors="replace")
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout reading error response content")
                content = "<timeout reading response>"
            except UnicodeDecodeError as err:
                _LOGGER.warning(f"Failed to decode response content: {err}")
                content = "<binary content>"
            except Exception as err:
                _LOGGER.warning(f"Error reading response content: {err}")
                content = f"<error reading content: {err}>"

        except Exception as err:
            _LOGGER.error(f"Critical error handling response: {err}")
            content = "<unavailable>"

        # Handle specific HTTP status codes
        if response.status == 400:
            _LOGGER.error(f"Bad Request (400): {content}")
            raise ValueError(f"Bad request to Loxone Miniserver: {content}")

        elif response.status == 401:
            _LOGGER.error(f"Unauthorized (401): {content}")
            err = LoxoneUnauthorisedError(f"Unauthorized: {content}")
            err.response = response
            raise err

        elif response.status == 403:
            _LOGGER.error(f"Forbidden (403): {content}")
            raise PermissionError(f"Access forbidden: {content}")

        elif response.status == 404:
            _LOGGER.error(f"Not Found (404): {content}")
            err = LoxoneUnrecognizedCommandError(f"Unrecognized command: {content}")
            err.response = response
            raise err

        elif response.status == 408:
            _LOGGER.error(f"Request Timeout (408): {content}")
            raise TimeoutError(f"Request timeout: {content}")

        elif response.status == 429:
            _LOGGER.error(f"Too Many Requests (429): {content}")
            raise RuntimeError(f"Rate limit exceeded: {content}")

        elif response.status == 500:
            _LOGGER.error(f"Internal Server Error (500): {content}")
            raise RuntimeError(f"Miniserver internal error: {content}")

        elif response.status == 502:
            _LOGGER.error(f"Bad Gateway (502): {content}")
            raise ConnectionError(f"Bad gateway: {content}")

        elif response.status == 503:
            _LOGGER.error(f"Service Unavailable (503): {content}")
            err = LoxoneServiceUnAvailableError(
                f"Service Unavailable; The Miniserver is restarting and not ready for requests: {content}"
            )
            err.response = response
            raise err

        elif response.status == 504:
            _LOGGER.error(f"Gateway Timeout (504): {content}")
            raise TimeoutError(f"Gateway timeout: {content}")

        elif response.status == 901:
            _LOGGER.error(f"Max Connections (901): {content}")
            err = LoxoneMaxNumOfConnectionsError(
                f"Maximum number of allowed concurrent connections reached: {content}"
            )
            err.response = response
            raise err

        else:
            # Generic error for any other status code
            _LOGGER.error(f"HTTP Error {response.status}: {content}")
            raise RuntimeError(f"HTTP error {response.status}: {content}")

    # def __enter__(self):
    #     raise RuntimeError("Use 'async with' to create an AsyncHttpClient instance")
    #
    # def __exit__(self, exc_type, exc_value, traceback):
    #     pass
