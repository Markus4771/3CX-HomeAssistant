"""Poll the PBX-wide queue-agent state independently of the login client."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import re
from typing import Any

from .api import ThreeCXSnapshot

_SAFE_SET = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_AGENT_PARTS = ("agent", "user", "extension", "member", "dn")
_QUEUE_PARTS = ("queue", "skillgroup")
_LOGIN_PARTS = (
    "login", "logged", "signed", "active", "enabled", "available",
    "ready", "status", "state", "inqueue",
)


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold().strip("{}()")


def _key(value: str) -> str:
    return value.casefold().replace("_", "").replace("-", "").replace(" ", "")


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    if depth > 7:
        return {}
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for name, child in value.items():
            path = f"{prefix}.{name}" if prefix else str(name)
            if isinstance(child, (dict, list)):
                result.update(_flatten(child, path, depth + 1))
            else:
                result[path] = child
    elif isinstance(value, list):
        for index, child in enumerate(value[:100]):
            result.update(_flatten(child, f"{prefix}[{index}]", depth + 1))
    return result


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = _key(value)
        if text in {
            "true", "yes", "1", "on", "active", "enabled", "available",
            "ready", "loggedin", "signedin", "login", "inqueue", "online",
        }:
            return True
        if text in {
            "false", "no", "0", "off", "inactive", "disabled", "unavailable",
            "notready", "loggedout", "signedout", "logout", "outofqueue", "offline",
        }:
            return False
    return None


def _paths_matching(flat: dict[str, Any], parts: tuple[str, ...]) -> list[tuple[str, Any]]:
    ranked: list[tuple[int, str, Any]] = []
    for path, value in flat.items():
        normalized = _key(path)
        if not any(part in normalized for part in parts):
            continue
        priority = 0
        lowered = path.casefold()
        if any(token in lowered for token in ("agent.", "user.", "extension.", "queue.")):
            priority -= 10
        ranked.append((priority, path, value))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [(path, value) for _, path, value in ranked]


def _resolve(flat: dict[str, Any], aliases: dict[str, str], parts: tuple[str, ...]) -> tuple[str | None, str | None]:
    for path, value in _paths_matching(flat, parts):
        resolved = aliases.get(_norm(value))
        if resolved:
            return resolved, path
    return None, None


def _login(flat: dict[str, Any]) -> tuple[bool | None, str | None]:
    candidates = _paths_matching(flat, _LOGIN_PARTS)
    # Prefer explicit login fields over generic Status/State fields.
    candidates.sort(
        key=lambda item: (
            0 if any(word in _key(item[0]) for word in ("login", "logged", "signed", "inqueue")) else 1,
            item[0],
        )
    )
    for path, value in candidates:
        parsed = _as_bool(value)
        if parsed is not None:
            return parsed, path
    return None, None


def _candidate_sets(metadata: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    for name in metadata.get("entity_sets", []):
        text = str(name)
        normalized = _key(text)
        if not _SAFE_SET.fullmatch(text):
            continue
        if "queue" in normalized and any(part in normalized for part in ("agent", "member", "status", "login")):
            names.add(text)
    # Common PBX-wide names are safe to try even when older metadata omits them.
    names.update({
        "QueueAgents", "QueueAgentStatus", "QueueAgentStatuses",
        "QueueAgentStates", "ActiveQueueAgents", "LoggedInQueueAgents",
    })
    return sorted(names)


async def async_poll_central_queue_status(
    client: Any,
    snapshot: ThreeCXSnapshot,
    metadata: dict[str, Any],
) -> tuple[ThreeCXSnapshot, dict[str, Any]]:
    """Read central PBX state and replace queue logins only when authoritative."""
    extension_aliases: dict[str, str] = {}
    for extension in snapshot.extension_records:
        identity = extension.number or extension.extension_id
        for value in (extension.extension_id, extension.number, extension.name, extension.email):
            if _norm(value):
                extension_aliases[_norm(value)] = identity

    queue_aliases: dict[str, str] = {}
    for queue in snapshot.queue_records:
        for value in (queue.queue_id, queue.number, queue.name, queue.display_name):
            if _norm(value):
                queue_aliases[_norm(value)] = queue.queue_id

    decisions: dict[str, dict[str, bool]] = {}
    results: dict[str, Any] = {}
    total_matches = 0

    for entity_set in _candidate_sets(metadata):
        path = f"/xapi/v1/{entity_set}?$top=100"
        try:
            rows, pages = await client._async_get_all_odata(path)  # noqa: SLF001
        except Exception as err:  # Discovery failures must not break normal polling.
            results[entity_set] = {"success": False, "endpoint": path, "error": str(err)[:300]}
            continue

        matched = logged = logged_out = 0
        agent_fields: set[str] = set()
        queue_fields: set[str] = set()
        login_fields: set[str] = set()
        local_decisions: dict[str, dict[str, bool]] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            flat = _flatten(row)
            agent, agent_path = _resolve(flat, extension_aliases, _AGENT_PARTS)
            queue_id, queue_path = _resolve(flat, queue_aliases, _QUEUE_PARTS)
            login, login_path = _login(flat)
            if not agent or not queue_id or login is None:
                continue
            matched += 1
            total_matches += 1
            if login:
                logged += 1
            else:
                logged_out += 1
            local_decisions.setdefault(queue_id, {})[agent] = login
            if agent_path:
                agent_fields.add(agent_path)
            if queue_path:
                queue_fields.add(queue_path)
            if login_path:
                login_fields.add(login_path)

        authoritative = matched > 0
        if authoritative:
            for queue_id, queue_values in local_decisions.items():
                decisions.setdefault(queue_id, {}).update(queue_values)

        results[entity_set] = {
            "success": True,
            "endpoint": path,
            "pages": pages,
            "rows": len(rows),
            "matched_rows": matched,
            "logged_in_rows": logged,
            "logged_out_rows": logged_out,
            "authoritative": authoritative,
            "agent_fields": sorted(agent_fields),
            "queue_fields": sorted(queue_fields),
            "login_fields": sorted(login_fields),
        }

    updated_queues = []
    for queue in snapshot.queue_records:
        queue_decisions = decisions.get(queue.queue_id)
        current = set(queue.logged_in_members)
        if queue_decisions is not None:
            # Central PBX rows are authoritative for every agent explicitly represented.
            for agent, is_logged_in in queue_decisions.items():
                if is_logged_in:
                    current.add(agent)
                else:
                    current.discard(agent)
        raw = dict(queue.raw_fields)
        raw.update({
            "central_status_poll": bool(queue_decisions),
            "central_status_decisions": len(queue_decisions or {}),
            "central_status_logged_in_count": len(current),
            "central_status_last_poll": datetime.now(timezone.utc).isoformat(),
            "central_status_source": "pbx_odata_entity_sets" if queue_decisions else None,
        })
        updated_queues.append(replace(
            queue,
            logged_in_members=tuple(sorted(current)),
            raw_fields=tuple(sorted(raw.items())),
        ))

    snapshot.queue_records = tuple(updated_queues)
    return snapshot, {
        "available": bool(results),
        "authoritative": total_matches > 0,
        "matched_rows": total_matches,
        "queue_decisions": {queue: dict(values) for queue, values in decisions.items()},
        "entity_sets": results,
        "polled_at": datetime.now(timezone.utc).isoformat(),
        "login_method_independent": True,
    }
