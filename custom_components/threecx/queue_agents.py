"""Retrieve queue agents and active login state from 3CX V20 endpoints."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any
from urllib.parse import quote

from .api import ThreeCXApiClient, ThreeCXApiError, ThreeCXQueue, ThreeCXSnapshot

_LOGGER = logging.getLogger(__name__)

_LOGIN_KEYS = (
    "IsLoggedIn",
    "LoggedIn",
    "QueueLoggedIn",
    "IsQueueLoggedIn",
    "AgentLoggedIn",
    "LoginStatus",
    "QueueStatus",
    "Status",
    "Logged",
    "IsActive",
)


def _normalized(value: str) -> str:
    return value.lower().replace("_", "").replace("-", "").replace(" ", "")


def _first(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    lookup = {_normalized(str(key)): value for key, value in item.items()}
    for key in keys:
        value = lookup.get(_normalized(key))
        if value not in (None, ""):
            return value
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower().replace("_", " ").replace("-", " ")
        if text in {
            "true", "yes", "1", "on", "active", "online", "available",
            "logged in", "login", "signed in", "enabled", "ready",
        }:
            return True
        if text in {
            "false", "no", "0", "off", "inactive", "offline", "unavailable",
            "logged out", "logout", "signed out", "disabled", "not ready",
        }:
            return False
    return None


def _identity(agent: Any) -> tuple[str, str]:
    if isinstance(agent, (str, int)):
        value = str(agent).strip()
        return value, value
    if not isinstance(agent, dict):
        return "", ""

    number = str(
        _first(agent, ("Number", "Extension", "ExtensionNumber", "UserNumber", "DnNumber"))
        or ""
    ).strip()
    identifier = str(
        _first(agent, ("Id", "UserId", "ExtensionId", "DnId")) or ""
    ).strip()

    nested = (
        agent.get("User")
        or agent.get("Extension")
        or agent.get("Dn")
        or agent.get("Agent")
        or agent.get("Member")
    )
    if isinstance(nested, dict):
        number = number or str(
            _first(nested, ("Number", "ExtensionNumber", "DnNumber")) or ""
        ).strip()
        identifier = identifier or str(
            _first(nested, ("Id", "UserId", "ExtensionId", "DnId")) or ""
        ).strip()
    return number, identifier


def _logged_in(agent: Any) -> bool | None:
    if not isinstance(agent, dict):
        return None
    value = _first(agent, _LOGIN_KEYS)
    parsed = _as_bool(value)
    if parsed is not None:
        return parsed
    nested = (
        agent.get("User")
        or agent.get("Extension")
        or agent.get("Agent")
        or agent.get("Member")
    )
    if isinstance(nested, dict):
        return _as_bool(_first(nested, _LOGIN_KEYS))
    return None


def _field_names(value: Any, prefix: str = "") -> set[str]:
    result: set[str] = set()
    if not isinstance(value, dict):
        return result
    for key, child in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        result.add(name)
        if isinstance(child, dict):
            result.update(_field_names(child, name))
    return result


def _paths(queue: ThreeCXQueue) -> tuple[str, ...]:
    """Return endpoints that expose configured queue membership."""
    identifiers = [value for value in (queue.queue_id, queue.number) if value]
    paths: list[str] = []
    for identifier in identifiers:
        encoded = quote(str(identifier), safe="")
        escaped = str(identifier).replace("'", "''")
        paths.extend(
            (
                f"/xapi/v1/Queues({encoded})/Agents?$expand=User",
                f"/xapi/v1/Queues({encoded})/Agents",
                f"/xapi/v1/Queues('{escaped}')/Agents?$expand=User",
                f"/xapi/v1/Queues('{escaped}')/Agents",
                f"/xapi/v1/Queues/{encoded}/Agents",
                f"/xapi/v1/QueueAgents?$filter=QueueId eq {encoded}&$expand=User",
                f"/xapi/v1/QueueAgents?$filter=QueueNumber eq '{escaped}'&$expand=User",
            )
        )
    return tuple(dict.fromkeys(paths))


def _active_paths(queue: ThreeCXQueue) -> tuple[str, ...]:
    """Return read-only endpoint variants for currently logged-in agents."""
    identifiers = [value for value in (queue.queue_id, queue.number) if value]
    paths: list[str] = []
    for identifier in identifiers:
        encoded = quote(str(identifier), safe="")
        escaped = str(identifier).replace("'", "''")
        paths.extend(
            (
                f"/xapi/v1/Queues({encoded})/LoggedInAgents?$expand=User",
                f"/xapi/v1/Queues({encoded})/LoggedInAgents",
                f"/xapi/v1/Queues({encoded})/ActiveAgents?$expand=User",
                f"/xapi/v1/Queues({encoded})/ActiveAgents",
                f"/xapi/v1/Queues('{escaped}')/LoggedInAgents?$expand=User",
                f"/xapi/v1/Queues('{escaped}')/ActiveAgents?$expand=User",
                f"/xapi/v1/QueueAgents?$filter=QueueId eq {encoded} and IsLoggedIn eq true&$expand=User",
                f"/xapi/v1/QueueAgents?$filter=QueueNumber eq '{escaped}' and IsLoggedIn eq true&$expand=User",
                f"/xapi/v1/QueueAgents?$filter=QueueId eq {encoded} and LoggedIn eq true&$expand=User",
                f"/xapi/v1/QueueAgents?$filter=QueueNumber eq '{escaped}' and LoggedIn eq true&$expand=User",
            )
        )
    return tuple(dict.fromkeys(paths))


async def _first_working_endpoint(
    client: ThreeCXApiClient, paths: tuple[str, ...]
) -> tuple[str | None, list[Any], list[str]]:
    errors: list[str] = []
    for path in paths:
        try:
            values, _pages = await client._async_get_all_odata(path)  # noqa: SLF001
        except ThreeCXApiError as err:
            errors.append(f"{path}: {err}")
            continue
        return path, values, errors
    return None, [], errors


async def async_enrich_queue_agents(
    client: ThreeCXApiClient,
    snapshot: ThreeCXSnapshot,
) -> tuple[ThreeCXSnapshot, dict[str, Any]]:
    """Poll membership and current queue-login state and enrich queue records."""
    diagnostics: dict[str, Any] = {}
    updated: list[ThreeCXQueue] = []

    for queue in snapshot.queue_records:
        selected, agents, member_errors = await _first_working_endpoint(
            client, _paths(queue)
        )
        active_selected, active_agents, active_errors = await _first_working_endpoint(
            client, _active_paths(queue)
        )

        members = set(queue.members)
        logged = set(queue.logged_in_members)
        fields: set[str] = set()
        unknown_login = 0
        explicit_login_values = 0
        explicit_logout_values = 0

        for agent in agents:
            fields.update(_field_names(agent))
            number, identifier = _identity(agent)
            identity = number or identifier
            if not identity:
                continue
            members.add(identity)
            state = _logged_in(agent)
            if state is True:
                logged.add(identity)
                explicit_login_values += 1
            elif state is False:
                # Important: an explicit logout must remove stale prior state.
                logged.discard(identity)
                explicit_logout_values += 1
            else:
                unknown_login += 1

        # A successful active-agent endpoint is authoritative, including an empty list.
        # This avoids retaining stale logins after an agent logs out.
        if active_selected is not None:
            active_identities: set[str] = set()
            for agent in active_agents:
                fields.update(_field_names(agent))
                number, identifier = _identity(agent)
                identity = number or identifier
                if identity:
                    active_identities.add(identity)
                    members.add(identity)
            logged = active_identities

        updated.append(
            replace(
                queue,
                members=tuple(sorted(members)),
                logged_in_members=tuple(sorted(logged)),
                raw_fields=tuple(sorted({
                    **dict(queue.raw_fields),
                    "active_agent_poll_authoritative": active_selected is not None,
                    "active_agent_endpoint": active_selected,
                    "active_agent_count": len(active_agents),
                    "explicit_login_values": explicit_login_values,
                    "explicit_logout_values": explicit_logout_values,
                }.items())),
            )
        )
        diagnostics[queue.display_name] = {
            "queue_id": queue.queue_id,
            "number": queue.number,
            "member_endpoint": selected,
            "active_agent_endpoint": active_selected,
            "active_agent_poll_authoritative": active_selected is not None,
            "agent_count": len(agents),
            "active_agent_count": len(active_agents),
            "member_count": len(members),
            "logged_in_count": len(logged),
            "explicit_login_values": explicit_login_values,
            "explicit_logout_values": explicit_logout_values,
            "unknown_login_count": unknown_login,
            "agent_fields": sorted(fields),
            "member_errors": member_errors[-10:],
            "active_agent_errors": active_errors[-10:],
        }

        _LOGGER.info(
            "3CX queue %s: member_endpoint=%s active_endpoint=%s members=%s logged_in=%s explicit_out=%s",
            queue.display_name,
            selected,
            active_selected,
            len(members),
            len(logged),
            explicit_logout_values,
        )

    snapshot.queue_records = tuple(updated)
    return snapshot, diagnostics
