"""Read queue-agent login state from metadata-advertised OData entity sets."""

from __future__ import annotations

from dataclasses import replace
import re
from typing import Any

from .api import ThreeCXSnapshot

_AGENT_KEYS = (
    "Agent", "AgentId", "AgentID", "AgentNumber", "Extension", "ExtensionId",
    "ExtensionID", "ExtensionNumber", "Number", "User", "UserId", "UserID",
    "UserNumber", "DN", "DnId", "DnNumber", "Member", "MemberId",
)
_QUEUE_KEYS = (
    "Queue", "QueueNumber", "QueueId", "QueueID", "QueueExtension",
    "QueueName", "Name",
)
_LOGIN_KEYS = (
    "IsLoggedIn", "LoggedIn", "QueueLoggedIn", "IsQueueLoggedIn",
    "AgentLoggedIn", "LoginStatus", "QueueStatus", "AgentStatus", "Status",
    "State", "IsActive", "Active", "Available", "IsAvailable",
)

_GUID_WRAPPER = re.compile(r"^[{(](.*)[})]$")


def _norm_key(value: str) -> str:
    return value.casefold().replace("_", "").replace("-", "").replace(" ", "")


def _norm_value(value: Any) -> str:
    """Normalize IDs, numbers and names for tolerant alias matching."""
    text = str(value or "").strip()
    match = _GUID_WRAPPER.match(text)
    if match:
        text = match.group(1)
    return text.casefold()


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    if depth > 7:
        return {}
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, (dict, list)):
                result.update(_flatten(child, path, depth + 1))
            else:
                result[path] = child
    elif isinstance(value, list):
        for index, child in enumerate(value[:50]):
            result.update(_flatten(child, f"{prefix}[{index}]", depth + 1))
    return result


def _candidate_values(flat: dict[str, Any], keys: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return all matching scalar values, preferring specific nested paths."""
    wanted = {_norm_key(key) for key in keys}
    candidates: list[tuple[int, str, str]] = []
    for path, value in flat.items():
        if value in (None, ""):
            continue
        final = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if _norm_key(final) not in wanted:
            continue
        # Nested Agent/User/Queue objects are more specific than generic root fields.
        lowered = path.casefold()
        priority = 0
        if any(part in lowered for part in ("agent.", "user.", "member.", "queue.")):
            priority -= 10
        if _norm_key(final) in {"name", "number", "status", "state"}:
            priority += 5
        candidates.append((priority, path, str(value).strip()))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [(path, value) for _, path, value in candidates]


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = _norm_key(value)
        if normalized in {
            "true", "yes", "1", "on", "online", "active", "available",
            "loggedin", "login", "signedin", "ready", "enabled", "inqueue",
        }:
            return True
        if normalized in {
            "false", "no", "0", "off", "offline", "inactive", "unavailable",
            "loggedout", "logout", "signedout", "notready", "disabled",
            "outofqueue",
        }:
            return False
    return None


def _pick_login(flat: dict[str, Any]) -> tuple[bool | None, str | None, Any]:
    for path, value in _candidate_values(flat, _LOGIN_KEYS):
        parsed = _as_bool(value)
        if parsed is not None:
            return parsed, path, value
    return None, None, None


def _resolve(candidates: list[tuple[str, str]], aliases: dict[str, str]) -> tuple[str | None, str | None]:
    for path, raw in candidates:
        resolved = aliases.get(_norm_value(raw))
        if resolved:
            return resolved, path
    return None, None


async def async_apply_entity_set_status(
    client: Any,
    snapshot: ThreeCXSnapshot,
    metadata: dict[str, Any],
) -> tuple[ThreeCXSnapshot, dict[str, Any]]:
    """Read discovered sets and merge only unambiguous queue login states."""
    probes = metadata.get("entity_set_probes", {})
    if not isinstance(probes, dict):
        return snapshot, {"available": False, "reason": "Keine Entity-Set-Probes"}

    queue_aliases: dict[str, str] = {}
    for queue in snapshot.queue_records:
        for alias in (queue.queue_id, queue.number, queue.display_name, queue.name):
            normalized = _norm_value(alias)
            if normalized:
                queue_aliases[normalized] = queue.queue_id

    extension_aliases: dict[str, str] = {}
    for extension in snapshot.extension_records:
        target = extension.number or extension.extension_id
        for alias in (
            extension.extension_id,
            extension.number,
            extension.name,
            extension.email,
        ):
            normalized = _norm_value(alias)
            if normalized:
                extension_aliases[normalized] = target

    logged_by_queue: dict[str, set[str]] = {
        queue.queue_id: set(queue.logged_in_members) for queue in snapshot.queue_records
    }
    results: dict[str, Any] = {}
    total_rows = matched_rows = logged_rows = 0

    for name, probe in probes.items():
        if not isinstance(probe, dict) or not probe.get("success"):
            continue
        try:
            rows, pages = await client._async_get_all_odata(  # noqa: SLF001
                f"/xapi/v1/{name}?$top=100"
            )
        except Exception as err:  # Optional discovery path must stay non-fatal.
            results[name] = {"used": False, "error": str(err)[:500]}
            continue

        set_matched = set_logged = 0
        login_fields: set[str] = set()
        agent_fields: set[str] = set()
        queue_fields: set[str] = set()
        unmatched = {
            "agent_not_found": 0,
            "queue_not_found": 0,
            "login_unknown": 0,
            "multiple_missing": 0,
        }

        for row in rows:
            if not isinstance(row, dict):
                continue
            total_rows += 1
            flat = _flatten(row)
            agent_candidates = _candidate_values(flat, _AGENT_KEYS)
            queue_candidates = _candidate_values(flat, _QUEUE_KEYS)
            agent, agent_path = _resolve(agent_candidates, extension_aliases)
            queue_id, queue_path = _resolve(queue_candidates, queue_aliases)
            login, login_path, _ = _pick_login(flat)

            if agent_path:
                agent_fields.add(agent_path)
            if queue_path:
                queue_fields.add(queue_path)
            if login_path:
                login_fields.add(login_path)

            missing = sum((not agent, not queue_id, login is None))
            if missing:
                if missing > 1:
                    unmatched["multiple_missing"] += 1
                elif not agent:
                    unmatched["agent_not_found"] += 1
                elif not queue_id:
                    unmatched["queue_not_found"] += 1
                else:
                    unmatched["login_unknown"] += 1
                continue

            matched_rows += 1
            set_matched += 1
            if login:
                logged_by_queue[queue_id].add(agent)
                logged_rows += 1
                set_logged += 1
            else:
                logged_by_queue[queue_id].discard(agent)

        results[name] = {
            "used": bool(rows),
            "pages": pages,
            "rows": len(rows),
            "matched_rows": set_matched,
            "logged_in_rows": set_logged,
            "agent_fields": sorted(agent_fields),
            "queue_fields": sorted(queue_fields),
            "login_fields": sorted(login_fields),
            "unmatched_reasons": unmatched,
            "known_agent_aliases": len(extension_aliases),
            "known_queue_aliases": len(queue_aliases),
        }

    snapshot.queue_records = tuple(
        replace(
            queue,
            logged_in_members=tuple(sorted(logged_by_queue[queue.queue_id])),
            raw_fields=tuple(sorted({
                **dict(queue.raw_fields),
                "entity_set_logged_in_count": len(logged_by_queue[queue.queue_id]),
            }.items())),
        )
        for queue in snapshot.queue_records
    )
    snapshot.extension_records = client._enrich_extensions_with_queues(  # noqa: SLF001
        snapshot.extension_records,
        snapshot.queue_records,
    )
    diagnostics = {
        "available": bool(results),
        "total_rows": total_rows,
        "matched_rows": matched_rows,
        "logged_in_rows": logged_rows,
        "entity_sets": results,
    }
    return snapshot, diagnostics
