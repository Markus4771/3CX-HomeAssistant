"""3CX V20 API client for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import urljoin, urlparse

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from .const import TOKEN_PATH, XAPI_DEFS_PATH, XAPI_USERS_PATH


class ThreeCXApiError(Exception):
    """Base exception for 3CX API errors."""


class ThreeCXAuthenticationError(ThreeCXApiError):
    """Raised when authentication fails."""


class ThreeCXConnectionError(ThreeCXApiError):
    """Raised when the PBX cannot be reached."""


@dataclass(frozen=True, slots=True)
class ThreeCXExtension:
    """Normalized 3CX V20 user/extension record."""

    extension_id: str
    number: str
    first_name: str = ""
    last_name: str = ""

    @property
    def name(self) -> str:
        """Return the best available display name."""
        full_name = " ".join(part for part in (self.first_name, self.last_name) if part).strip()
        return full_name or self.number or f"Extension {self.extension_id}"


@dataclass(slots=True)
class ThreeCXSnapshot:
    """Normalized state returned to Home Assistant."""

    connected: bool
    extensions: int = 0
    active_calls: int = 0
    api_mode: str = "v20"
    system_version: str | None = None
    extension_records: tuple[ThreeCXExtension, ...] = ()
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

    def _request_url(self, path_or_url: str) -> str:
        """Build a request URL and reject links to another host."""
        url = urljoin(f"{self.base_url}/", path_or_url)
        base = urlparse(self.base_url)
        target = urlparse(url)
        if (target.scheme, target.netloc) != (base.scheme, base.netloc):
            raise ThreeCXApiError("3CX returned a URL for another host")
        return url

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

    async def _async_get(
        self, path_or_url: str, retry_auth: bool = True
    ) -> tuple[dict[str, Any], ClientResponse]:
        token = await self.async_authenticate()
        try:
            async with self._session.get(
                self._request_url(path_or_url),
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                ssl=self._verify_ssl,
                timeout=ClientTimeout(total=20),
            ) as response:
                if response.status == 401 and retry_auth:
                    await self.async_authenticate(force=True)
                    return await self._async_get(path_or_url, retry_auth=False)
                await self._raise_for_status(response)
                payload = await response.json(content_type=None)
                return payload, response
        except ThreeCXApiError:
            raise
        except (ClientError, TimeoutError, ValueError) as err:
            raise ThreeCXConnectionError(str(err)) from err

    async def _async_get_all_odata(self, path: str) -> list[Any]:
        """Read every page of an OData collection."""
        values: list[Any] = []
        next_link: str | None = path
        visited: set[str] = set()

        while next_link:
            url = self._request_url(next_link)
            if url in visited:
                raise ThreeCXApiError("3CX pagination loop detected")
            if len(visited) >= 100:
                raise ThreeCXApiError("3CX pagination exceeded 100 pages")
            visited.add(url)

            payload, _ = await self._async_get(next_link)
            if not isinstance(payload, dict):
                break
            page_values = payload.get("value", [])
            if isinstance(page_values, list):
                values.extend(page_values)
            next_link = payload.get("@odata.nextLink") or payload.get("odata.nextLink")
            if next_link is not None:
                next_link = str(next_link)

        return values

    @staticmethod
    def _normalize_extensions(values: list[Any]) -> tuple[ThreeCXExtension, ...]:
        """Normalize V20 Users results and discard unusable records."""
        records: list[ThreeCXExtension] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            extension_id = str(item.get("Id", "")).strip()
            number = str(item.get("Number", "")).strip()
            if not extension_id:
                continue
            records.append(
                ThreeCXExtension(
                    extension_id=extension_id,
                    number=number,
                    first_name=str(item.get("FirstName", "") or "").strip(),
                    last_name=str(item.get("LastName", "") or "").strip(),
                )
            )
        return tuple(sorted(records, key=lambda record: (record.number, record.name)))

    async def async_test_connection(self) -> bool:
        """Authenticate and validate access using the official quick-test endpoint."""
        await self._async_get(XAPI_DEFS_PATH)
        return True

    async def async_get_snapshot(self) -> ThreeCXSnapshot:
        """Fetch a normalized V20 Configuration API snapshot."""
        defs, defs_response = await self._async_get(XAPI_DEFS_PATH)
        user_values = await self._async_get_all_odata(XAPI_USERS_PATH)
        extension_records = self._normalize_extensions(user_values)
        version = (
            defs_response.headers.get("X-3CX-Version")
            or defs_response.headers.get("3CX-Version")
            or defs_response.headers.get("Server-Version")
        )
        return ThreeCXSnapshot(
            connected=True,
            extensions=len(extension_records),
            active_calls=0,
            api_mode=self._api_mode,
            system_version=version,
            extension_records=extension_records,
            raw={
                "endpoint": self.base_url,
                "defs_count": len(defs.get("value", [])) if isinstance(defs, dict) else 0,
                "users": user_values,
                "active_calls_supported": False,
            },
        )
