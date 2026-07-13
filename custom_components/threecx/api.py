"""API abstraction for the 3CX integration.

The adapter is intentionally conservative: endpoint paths and authentication vary
between 3CX releases and installations. Productive API calls will be implemented
once the target 3CX version and supported authentication method are known.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout


class ThreeCXApiError(Exception):
    """Base exception for 3CX API errors."""


class ThreeCXAuthenticationError(ThreeCXApiError):
    """Raised when authentication fails."""


class ThreeCXConnectionError(ThreeCXApiError):
    """Raised when the PBX cannot be reached."""


@dataclass(slots=True)
class ThreeCXSnapshot:
    """Normalized state returned to Home Assistant."""

    connected: bool
    extensions: int = 0
    active_calls: int = 0
    api_mode: str = "unconfigured"
    raw: dict[str, Any] | None = None


class ThreeCXApiClient:
    """Client used by the integration coordinator."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        verify_ssl: bool,
        api_mode: str,
    ) -> None:
        self._session = session
        self._host = host.rstrip("/")
        self._port = port
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._api_mode = api_mode

    @property
    def base_url(self) -> str:
        """Return normalized PBX base URL."""
        if self._host.startswith(("http://", "https://")):
            return self._host
        return f"https://{self._host}:{self._port}"

    async def async_test_connection(self) -> bool:
        """Check whether the configured PBX web service is reachable."""
        try:
            async with self._session.get(
                self.base_url,
                ssl=self._verify_ssl,
                timeout=ClientTimeout(total=10),
                allow_redirects=True,
            ) as response:
                return response.status < 500
        except (ClientError, TimeoutError, ValueError) as err:
            raise ThreeCXConnectionError(str(err)) from err

    async def async_get_snapshot(self) -> ThreeCXSnapshot:
        """Return normalized PBX state.

        Version 0.1.0 performs a safe reachability check only. No undocumented
        3CX endpoint is called. Productive data retrieval is added in the next
        milestone after API compatibility has been established.
        """
        connected = await self.async_test_connection()
        return ThreeCXSnapshot(
            connected=connected,
            api_mode=self._api_mode,
            raw={"endpoint": self.base_url},
        )
