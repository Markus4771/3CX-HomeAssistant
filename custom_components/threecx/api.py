"""3CX V20 API client for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from time import monotonic
from typing import Any
from urllib.parse import urljoin, urlparse

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from .const import (
    TOKEN_PATH,
    XAPI_DEFS_PATH,
    XAPI_GROUP_PATHS,
    XAPI_QUEUES_PATH,
    XAPI_USERS_PATH,
)

_LOGGER = logging.getLogger(__name__)


class ThreeCXApiError(Exception):
    """Base exception for 3CX API errors."""


class ThreeCXAuthenticationError(ThreeCXApiError):
    """Raised when authentication fails."""


class ThreeCXConnectionError(ThreeCXApiError):
    """Raised when the PBX cannot be reached."""


_STATUS_KEY_PARTS = (
    "status", "presence", "profile", "dnd", "donotdisturb", "route",
    "available", "away", "registered", "officehours", "queue", "agent",
)
_STATUS_PRIORITY = (
    "CurrentProfile", "PresenceStatus", "CurrentStatus", "UserStatus",
    "RouteStatus", "Status", "Profile",
)
_REGISTRATION_KEYS = (
    "IsRegistered", "Registered", "IsExtensionRegistered", "ExtensionRegistered",
    "RegistrationStatus", "DeviceRegistered", "IsOnline", "Online",
)
_ROLE_KEYS = ("Role", "RoleName", "UserRole", "Rights", "SystemRole", "GroupRole")
_DEPARTMENT_KEYS = ("Department", "DepartmentName", "Group", "GroupName", "Groups")
_ACTIVE_KEYS = ("IsActive", "Active", "Enabled", "IsEnabled", "Disabled")
_EMAIL_KEYS = ("EmailAddress", "Email", "Mail")
_MOBILE_KEYS = ("Mobile", "MobileNumber", "CellPhone", "Cell")
_QUEUE_LIST_KEYS = ("Agents", "Members", "QueueAgents", "Users", "Extensions")
_QUEUE_LOGIN_KEYS = (
    "IsLoggedIn", "LoggedIn", "QueueLoggedIn", "IsQueueLoggedIn",
    "AgentLoggedIn", "LoginStatus", "QueueStatus", "Status",
)
_GROUP_MEMBER_KEYS = (
    "Members", "Users", "GroupMembers", "Participants", "Extensions", "Agents",
)


def _normalized_key(value: str) -> str:
    return value.lower().replace("_", "").replace("-", "").replace(" ", "")


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {
            "true", "yes", "1", "on", "online", "registered", "available",
            "loggedin", "logged in", "active", "enabled",
        }:
            return True
        if normalized in {
            "false", "no", "0", "off", "offline", "unregistered",
            "notregistered", "not registered", "loggedout", "logged out",
            "inactive", "disabled",
        }:
            return False
    return None


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    lookup = {_normalized_key(key): value for key, value in item.items()}
    for key in keys:
        value = lookup.get(_normalized_key(key))
        if value not in (None, ""):
            return value
    return None


def _string_value(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    value = _first_value(item, keys)
    if isinstance(value, dict):
        value = _first_value(value, ("Name", "DisplayName", "Title", "RoleName"))
    if isinstance(value, list):
        values: list[str] = []
        for entry in value:
            if isinstance(entry, dict):
                name = _first_value(entry, ("Name", "DisplayName", "Title"))
                if name:
                    values.append(str(name))
            elif entry not in (None, ""):
                values.append(str(entry))
        return ", ".join(values)
    return str(value or "").strip()


def _simple_attributes(item: dict[str, Any], parts: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in item.items():
        normalized = _normalized_key(key)
        if any(part in normalized for part in parts) and (
            value is None or isinstance(value, (str, int, float, bool))
        ):
            result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class ThreeCXQueue:
    """Normalized 3CX queue record."""

    queue_id: str
    number: str
    name: str
    members: tuple[str, ...] = ()
    logged_in_members: tuple[str, ...] = ()
    raw_fields: tuple[tuple[str, Any], ...] = ()

    @property
    def display_name(self) -> str:
        return self.name or self.number or f"Queue {self.queue_id}"


@dataclass(frozen=True, slots=True)
class ThreeCXExtension:
    """Normalized 3CX V20 user/extension record."""

    extension_id: str
    number: str
    first_name: str = ""
    last_name: str = ""
    role: str = ""
    department: str = ""
    email: str = ""
    mobile: str = ""
    active: bool | None = None
    source: str = "Users"
    presence_status: str = "unknown"
    registered: bool | None = None
    queue_names: tuple[str, ...] = ()
    queue_logged_in_names: tuple[str, ...] = ()
    status_fields: tuple[tuple[str, Any], ...] = ()

    @property
    def name(self) -> str:
        full_name = " ".join(
            part for part in (self.first_name, self.last_name) if part
        ).strip()
        return full_name or self.number or f"Extension {self.extension_id}"

    @property
    def status_attributes(self) -> dict[str, Any]:
        return dict(self.status_fields)

    @property
    def queue_logged_in(self) -> bool:
        return bool(self.queue_logged_in_names)


@dataclass(slots=True)
class ThreeCXSnapshot:
    """Normalized state returned to Home Assistant."""

    connected: bool
    extensions: int = 0
    active_calls: int = 0
    api_mode: str = "v20"
    system_version: str | None = None
    extension_records: tuple[ThreeCXExtension, ...] = ()
    queue_records: tuple[ThreeCXQueue, ...] = ()
    api_users_received: int = 0
    api_users_imported: int = 0
    api_users_skipped: int = 0
    api_pages: int = 0
    group_pages: int = 0
    group_users_found: int = 0
    user_sources: tuple[tuple[str, int], ...] = ()
    group_endpoint: str | None = None
    group_error: str | None = None
    queue_pages: int = 0
    queues_available: bool = False
    queue_error: str | None = None
    skipped_records: tuple[str, ...] = ()
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
        if self._host.startswith(("http://", "https://")):
            return self._host
        return f"https://{self._host}:{self._port}"

    def _request_url(self, path_or_url: str) -> str:
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
            raise ThreeCXApiError(
                f"3CX returned HTTP {response.status}: {text[:300]}"
            )

    async def async_authenticate(self, force: bool = False) -> str:
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
            raise ThreeCXAuthenticationError(
                "3CX token response contained no access_token"
            )
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
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
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

    async def _async_get_all_odata(self, path: str) -> tuple[list[Any], int]:
        values: list[Any] = []
        next_link: str | None = path
        visited: set[str] = set()
        pages = 0
        while next_link:
            url = self._request_url(next_link)
            if url in visited:
                raise ThreeCXApiError("3CX pagination loop detected")
            if len(visited) >= 100:
                raise ThreeCXApiError("3CX pagination exceeded 100 pages")
            visited.add(url)
            payload, _ = await self._async_get(next_link)
            pages += 1
            if not isinstance(payload, dict):
                break
            page_values = payload.get("value", [])
            if isinstance(page_values, list):
                values.extend(page_values)
            next_link = payload.get("@odata.nextLink") or payload.get(
                "odata.nextLink"
            )
            if next_link is not None:
                next_link = str(next_link)
        return values, pages

    @staticmethod
    def _status_fields(item: dict[str, Any]) -> dict[str, Any]:
        return _simple_attributes(item, _STATUS_KEY_PARTS)

    @staticmethod
    def _primary_status(item: dict[str, Any], fields: dict[str, Any]) -> str:
        for key in _STATUS_PRIORITY:
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        for value in fields.values():
            if value not in (None, "") and not isinstance(value, bool):
                return str(value)
        for key, value in fields.items():
            normalized = _normalized_key(key)
            if isinstance(value, bool) and value and (
                "dnd" in normalized or "donotdisturb" in normalized
            ):
                return "DND"
        return "unknown"

    @staticmethod
    def _registration_status(item: dict[str, Any]) -> bool | None:
        status = _as_bool(_first_value(item, _REGISTRATION_KEYS))
        if status is not None:
            return status
        for key, candidate in item.items():
            normalized = _normalized_key(key)
            if "register" in normalized or normalized in {"isonline", "online"}:
                status = _as_bool(candidate)
                if status is not None:
                    return status
        return None

    @staticmethod
    def _active_status(item: dict[str, Any]) -> bool | None:
        value = _first_value(item, _ACTIVE_KEYS)
        if value is None:
            return None
        if "Disabled" in item and value is item.get("Disabled"):
            disabled = _as_bool(value)
            return None if disabled is None else not disabled
        return _as_bool(value)

    @staticmethod
    def _looks_like_user(item: dict[str, Any]) -> bool:
        number = _string_value(item, ("Number", "ExtensionNumber", "DnNumber"))
        identifier = _string_value(item, ("Id", "UserId", "ExtensionId", "DnId"))
        has_user_fields = any(
            key in item
            for key in (
                "FirstName", "LastName", "EmailAddress", "Email",
                "Role", "RoleName", "UserRole",
            )
        )
        return bool(number or identifier) and has_user_fields

    @classmethod
    def _extract_group_users(
        cls, groups: list[Any]
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        found: list[dict[str, Any]] = []
        source_counts: dict[str, int] = {}

        def visit(value: Any, department: str, source: str, depth: int = 0) -> None:
            if depth > 6:
                return
            if isinstance(value, list):
                for entry in value:
                    visit(entry, department, source, depth + 1)
                return
            if not isinstance(value, dict):
                return
            if cls._looks_like_user(value):
                copied = dict(value)
                if department and not _string_value(copied, _DEPARTMENT_KEYS):
                    copied["DepartmentName"] = department
                copied["_import_source"] = source
                found.append(copied)
                source_counts[source] = source_counts.get(source, 0) + 1
                return
            nested_department = (
                _string_value(value, ("Name", "DisplayName", "GroupName"))
                or department
            )
            for key in _GROUP_MEMBER_KEYS:
                if key in value:
                    visit(value[key], nested_department, source, depth + 1)

        for group in groups:
            if not isinstance(group, dict):
                continue
            department = _string_value(
                group, ("Name", "DisplayName", "GroupName")
            )
            for key in _GROUP_MEMBER_KEYS:
                if key in group:
                    visit(group[key], department, f"Groups.{key}")
        return found, source_counts

    @staticmethod
    def _merge_user_values(
        primary: list[Any], fallback: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        merged: dict[str, dict[str, Any]] = {}
        number_index: dict[str, str] = {}
        source_counts: dict[str, int] = {"Users": 0}

        def add(item: dict[str, Any], source: str) -> None:
            identifier = _string_value(
                item, ("Id", "UserId", "ExtensionId", "DnId")
            )
            number = _string_value(
                item, ("Number", "ExtensionNumber", "DnNumber")
            )
            if not identifier and number:
                identifier = number_index.get(number, f"number:{number}")
            if not identifier:
                return
            existing = merged.get(identifier)
            if existing is None and number and number in number_index:
                identifier = number_index[number]
                existing = merged.get(identifier)
            if existing is None:
                copied = dict(item)
                copied["_import_source"] = source
                merged[identifier] = copied
                if number:
                    number_index[number] = identifier
                source_counts[source] = source_counts.get(source, 0) + 1
                return
            for key, value in item.items():
                if key.startswith("_"):
                    continue
                if existing.get(key) in (None, "", [], {}) and value not in (
                    None, "", [], {}
                ):
                    existing[key] = value
            prior_source = str(existing.get("_import_source", "Users"))
            if source not in prior_source.split(","):
                existing["_import_source"] = f"{prior_source},{source}"

        for item in primary:
            if isinstance(item, dict):
                add(item, "Users")
        for item in fallback:
            add(item, str(item.get("_import_source", "Groups")))
        return list(merged.values()), source_counts

    @classmethod
    def _normalize_extensions(
        cls, values: list[Any]
    ) -> tuple[tuple[ThreeCXExtension, ...], tuple[str, ...]]:
        records: list[ThreeCXExtension] = []
        skipped: list[str] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(values, start=1):
            if not isinstance(item, dict):
                skipped.append(f"Datensatz {index}: kein Objekt")
                continue
            extension_id = _string_value(
                item, ("Id", "UserId", "ExtensionId", "DnId")
            )
            number = _string_value(
                item, ("Number", "ExtensionNumber", "DnNumber")
            )
            first_name = _string_value(item, ("FirstName", "GivenName"))
            last_name = _string_value(
                item, ("LastName", "Surname", "FamilyName")
            )
            label = " ".join(
                part for part in (number, first_name, last_name) if part
            )
            if not extension_id:
                if number:
                    extension_id = f"number:{number}"
                else:
                    skipped.append(
                        f"Datensatz {index} ({label or 'ohne Namen'}): "
                        "Id und Nummer fehlen"
                    )
                    continue
            if extension_id in seen_ids:
                skipped.append(
                    f"Datensatz {index} ({label or extension_id}): "
                    f"doppelte Id {extension_id}"
                )
                continue
            seen_ids.add(extension_id)
            status_fields = cls._status_fields(item)
            records.append(
                ThreeCXExtension(
                    extension_id=extension_id,
                    number=number,
                    first_name=first_name,
                    last_name=last_name,
                    role=_string_value(item, _ROLE_KEYS),
                    department=_string_value(item, _DEPARTMENT_KEYS),
                    email=_string_value(item, _EMAIL_KEYS),
                    mobile=_string_value(item, _MOBILE_KEYS),
                    active=cls._active_status(item),
                    source=str(item.get("_import_source", "Users")),
                    presence_status=cls._primary_status(item, status_fields),
                    registered=cls._registration_status(item),
                    status_fields=tuple(sorted(status_fields.items())),
                )
            )
        return (
            tuple(sorted(records, key=lambda record: (record.number, record.name))),
            tuple(skipped),
        )

    @staticmethod
    def _agent_identity(agent: Any) -> tuple[str, str]:
        if isinstance(agent, (str, int)):
            value = str(agent).strip()
            return value, value
        if not isinstance(agent, dict):
            return "", ""
        number = _string_value(
            agent,
            ("Number", "Extension", "ExtensionNumber", "UserNumber", "DnNumber"),
        )
        identifier = _string_value(
            agent, ("Id", "UserId", "ExtensionId", "DnId")
        )
        nested = agent.get("User") or agent.get("Extension") or agent.get("Dn")
        if isinstance(nested, dict):
            number = number or _string_value(
                nested, ("Number", "ExtensionNumber", "DnNumber")
            )
            identifier = identifier or _string_value(
                nested, ("Id", "UserId")
            )
        return number, identifier

    @staticmethod
    def _agent_logged_in(agent: Any) -> bool | None:
        if not isinstance(agent, dict):
            return None
        return _as_bool(_first_value(agent, _QUEUE_LOGIN_KEYS))

    @classmethod
    def _normalize_queues(cls, values: list[Any]) -> tuple[ThreeCXQueue, ...]:
        queues: list[ThreeCXQueue] = []
        for index, item in enumerate(values, start=1):
            if not isinstance(item, dict):
                continue
            queue_id = str(item.get("Id", "") or f"queue-{index}").strip()
            number = str(item.get("Number", "") or "").strip()
            name = _string_value(item, ("Name", "DisplayName", "QueueName"))
            agents: list[Any] = []
            for key in _QUEUE_LIST_KEYS:
                value = item.get(key)
                if isinstance(value, list):
                    agents.extend(value)
            members: set[str] = set()
            logged_in: set[str] = set()
            for agent in agents:
                agent_number, agent_id = cls._agent_identity(agent)
                identity = agent_number or agent_id
                if not identity:
                    continue
                members.add(identity)
                if cls._agent_logged_in(agent) is True:
                    logged_in.add(identity)
            raw_fields = _simple_attributes(
                item, ("status", "active", "enabled", "logged")
            )
            queues.append(
                ThreeCXQueue(
                    queue_id=queue_id,
                    number=number,
                    name=name,
                    members=tuple(sorted(members)),
                    logged_in_members=tuple(sorted(logged_in)),
                    raw_fields=tuple(sorted(raw_fields.items())),
                )
            )
        return tuple(
            sorted(queues, key=lambda queue: (queue.number, queue.display_name))
        )

    @staticmethod
    def _enrich_extensions_with_queues(
        extensions: tuple[ThreeCXExtension, ...],
        queues: tuple[ThreeCXQueue, ...],
    ) -> tuple[ThreeCXExtension, ...]:
        enriched: list[ThreeCXExtension] = []
        for extension in extensions:
            identities = {extension.extension_id, extension.number}
            member_of: list[str] = []
            logged_into: list[str] = []
            for queue in queues:
                if identities.intersection(queue.members):
                    member_of.append(queue.display_name)
                if identities.intersection(queue.logged_in_members):
                    logged_into.append(queue.display_name)
            enriched.append(
                replace(
                    extension,
                    queue_names=tuple(sorted(set(member_of))),
                    queue_logged_in_names=tuple(sorted(set(logged_into))),
                )
            )
        return tuple(enriched)

    async def _async_get_group_users(
        self,
    ) -> tuple[
        list[dict[str, Any]], int, str | None, str | None, dict[str, int]
    ]:
        errors: list[str] = []
        for path in XAPI_GROUP_PATHS:
            try:
                group_values, pages = await self._async_get_all_odata(path)
                users, source_counts = self._extract_group_users(group_values)
                return users, pages, path, None, source_counts
            except ThreeCXApiError as err:
                errors.append(f"{path}: {err}")
        return [], 0, None, " | ".join(errors) if errors else None, {}

    async def async_test_connection(self) -> bool:
        await self._async_get(XAPI_DEFS_PATH)
        return True

    async def async_get_snapshot(self) -> ThreeCXSnapshot:
        defs, defs_response = await self._async_get(XAPI_DEFS_PATH)
        user_values, pages = await self._async_get_all_odata(XAPI_USERS_PATH)
        (
            group_users,
            group_pages,
            group_endpoint,
            group_error,
            group_sources,
        ) = await self._async_get_group_users()
        merged_users, merge_sources = self._merge_user_values(
            user_values, group_users
        )
        for source, count in group_sources.items():
            merge_sources[source] = max(merge_sources.get(source, 0), count)
        extension_records, skipped_records = self._normalize_extensions(
            merged_users
        )

        queue_records: tuple[ThreeCXQueue, ...] = ()
        queue_pages = 0
        queue_error: str | None = None
        try:
            queue_values, queue_pages = await self._async_get_all_odata(
                XAPI_QUEUES_PATH
            )
            queue_records = self._normalize_queues(queue_values)
            extension_records = self._enrich_extensions_with_queues(
                extension_records, queue_records
            )
        except ThreeCXApiError as err:
            queue_error = str(err)
            _LOGGER.warning("3CX queue data unavailable: %s", err)

        version = (
            defs_response.headers.get("X-3CX-Version")
            or defs_response.headers.get("3CX-Version")
            or defs_response.headers.get("Server-Version")
        )
        _LOGGER.info(
            "3CX import: users=%s group_users=%s merged=%s imported=%s queues=%s",
            len(user_values),
            len(group_users),
            len(merged_users),
            len(extension_records),
            len(queue_records),
        )
        if group_error:
            _LOGGER.info("3CX group fallback unavailable: %s", group_error)
        for reason in skipped_records:
            _LOGGER.warning("3CX user skipped: %s", reason)

        return ThreeCXSnapshot(
            connected=True,
            extensions=len(extension_records),
            active_calls=0,
            api_mode=self._api_mode,
            system_version=version,
            extension_records=extension_records,
            queue_records=queue_records,
            api_users_received=len(user_values),
            api_users_imported=len(extension_records),
            api_users_skipped=len(skipped_records),
            api_pages=pages,
            group_pages=group_pages,
            group_users_found=len(group_users),
            user_sources=tuple(sorted(merge_sources.items())),
            group_endpoint=group_endpoint,
            group_error=group_error,
            queue_pages=queue_pages,
            queues_available=queue_error is None,
            queue_error=queue_error,
            skipped_records=skipped_records,
            raw={
                "endpoint": self.base_url,
                "defs_count": (
                    len(defs.get("value", [])) if isinstance(defs, dict) else 0
                ),
                "active_calls_supported": False,
            },
        )
