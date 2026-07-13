"""3CX V20 API client for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from .const import TOKEN_PATH, XAPI_DEFS_PATH, XAPI_USERS_PATH


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
    api_mode: str = "v20"
    system_version: str | None = None
    raw: dict[str, Any] | None = None


class ThreeCXApiClient:
    """Client for the official 3CX V20 Configuration API."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        port: int,
        client_id: str,
        client_secret: str,
        verify_ssl: bool,
        api_mode: str,
    ) -> None:
        self._session = session
        self._host = host.rstrip("/")
        self._port = port
        self._client_id = client_id
        self._client_secret = client_secret
        self._verify_ssl = verify_ssl
        self._api_mode = "v20" if api_mode == "auto" else api_mode
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    @property
    def base_url(self) -> str:
        """Return normalized PBX base URL."""
        if self._host.startswith(("http://", "https://")):
            return self._host
        return f"https://{self._host}:{self._port}"

    async def _raise_for_status(self, response: ClientResponse) -> None:
        if response.status in (401, 403):
            text = await response.text()
            raise ThreeCXAuthenticationError(text or "3CX authentication failed")
        if response.status >= 400:
            text = await response.text()
            raise ThreeCXApiError(f"3CX returned HTTP {response.status}: {text[:300]}")

    async def async_authenticate(self, force: bool = False) -> str:
        """Obtain and cache a V20 client-credentials access token."""
        if not force and self._access_token and monotonic() < self._token_expires_at:
            return self._access_token
        if not self._client_id or not self._client_secret:
            raise ThreeCXAuthenticationError("Client ID and API key are required")

        try:
            async with self._session.post(
                f"{self.base_url}{TOKEN_PATH}",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                ssl=self._verify_ssl,
                timeout=ClientTimeout(total=15),
            ) as response:
                await self._raise_for_status(response)
                payload = await response.json(content_type=None)
        except ThreeCXApiError:
            raise
        except (ClientError, TimeoutError, ValueError) as err:
            raise ThreeCXConnectionError(str(err)) from err

        token = payload.get("access_token")
        if not token:
            raise ThreeCXAuthenticationError("3CX token response contained no access_token")
        expires_in = int(payload.get("expires_in", 3600))
        self._access_token = str(token)
        self._token_expires_at = monotonic() + max(60, expires_in - 60)
        return self._access_token

    async def _async_get(self, path: str) -> tuple[dict[str, Any], ClientResponse]:
        token = await self.async_authenticate()
        try:
            async with self._session.get(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                ssl=self._verify_ssl,
                timeout=ClientTimeout(total=20),
            ) as response:
                if response.status == 401:
                    token = await self.async_authenticate(force=True)
                    return await self._async_get(path)
                await self._raise_for_status(response)
                payload = await response.json(content_type=None)
                return payload, response
        except ThreeCXApiError:
            raise
        except (ClientError, TimeoutError, ValueError) as err:
            raise ThreeCXConnectionError(str(err)) from err

    async def async_test_connection(self) -> bool:
        """Authenticate and validate access using the official quick-test endpoint."""
        await self._async_get(XAPI_DEFS_PATH)
        return True

    async def async_get_snapshot(self) -> ThreeCXSnapshot:
        """Fetch a normalized V20 Configuration API snapshot."""
        defs, defs_response = await self._async_get(XAPI_DEFS_PATH)
        users, _ = await self._async_get(XAPI_USERS_PATH)
        user_values = users.get("value", []) if isinstance(users, dict) else []
        version = (
            defs_response.headers.get("X-3CX-Version")
            or defs_response.headers.get("3CX-Version")
            or defs_response.headers.get("Server-Version")
        )
        return ThreeCXSnapshot(
            connected=True,
            extensions=len(user_values),
            active_calls=0,
            api_mode=self._api_mode,
            system_version=version,
            raw={
                "endpoint": self.base_url,
                "defs_count": len(defs.get("value", [])) if isinstance(defs, dict) else 0,
                "users": user_values,
                "active_calls_supported": False,
            },
        )
