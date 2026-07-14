"""Read queue-agent login state from metadata-advertised OData entity sets."""

from __future__ import annotations

from collections import Counter
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


def _path_matches(path: str, keys: tuple[str, ...]) -> bool:
    final = path.rsplit(".", 1)[-1].split("[", 1)[0]
    return _norm_key(final) in {_norm_key(key) for key in keys}


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


def _learn_alias_path(
    flattened_rows: list[dict[str, Any]],
    keys: tuple[str, ...],
    aliases: dict[str, str],
) -> tuple[str | None, dict[str, int]]:
    """Score candidate paths by how often their values resolve to known aliases."""
    scores: Counter[str] = Counter()
    for flat in flattened_rows:
        for path, value in flat.items():
            if value in (None, "") or not _path_matches(path, keys):
                continue
            if _norm_value(value) in aliases:
                scores[path] += 1
    if not scores:
        return None, {}
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return ranked[0][0], dict(ranked[:10])


def _learn_login_path(
    flattened_rows: list[dict[str, Any]],
) -> tuple[str | None, dict[str, int]]:
    """Score paths by the number of values that can safely become booleans."""
    scores: Counter[str] = Counter()
    for flat in flattened_rows:
        for path, value in flat.items():
            if not _path_matches(path, _LOGIN_KEYS):
                continue
            if _as_bool(value) is not None:
                scores[path] += 1
    if not scores:
        return None, {}
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return ranked[0][0], dict(ranked[:10])


def _resolve_path(
    flat: dict[str, Any], path: str | None, aliases: dict[str, str]
) -> str | None:
    if not path or path not in flat:
        return None
    return aliases.get(_norm_value(flat[path]))


def _resolve_fallback(
    flat: dict[str, Any], keys: tuple[str, ...], aliases: dict[str, str]
) -> tuple[str | None, str | None]:
    """Resolve from all candidate paths when the learned path is absent in a row."""
    candidates: list[tuple[int, str, Any]] = []
    for path, value in flat.items():
        if value in (None, "") or not _path_matches(path, keys):
            continue
        lowered = path.casefold()
        priority = -10 if any(
            part in lowered for part in ("agent.", "user.", "member.", "queue.")
        ) else 0
        candidates.append((priority, path, value))
    for _, path, value in sorted(candidates, key=lambda item: (item[0], item[1])):
        resolved = aliases.get(_norm_value(value))
        if resolved:
            return resolved, path
    return None, None


def _login_from_path(flat: dict[str, Any], path: str | None) -> bool | None:
    if not path or path not in flat:
        return None
    return _as_bool(flat[path])


def _login_fallback(flat: dict[str, Any]) -> tuple[bool | None, str | None]:
    for path, value in sorted(flat.items()):
        if _path_matches(path, _LOGIN_KEYS):
            parsed = _as_bool(value)
            if parsed is not None:
                return parsed, path
    return None, None


async def async_apply_entity_set_status(
    client: Any,
    snapshot: ThreeCXSnapshot,
    metadata: dict[str, Any],
) -> tuple[ThreeCXSnapshot, dict[str, Any]]:
    """Learn field paths and merge only unambiguous queue login states."""
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

        flattened_rows = [_flatten(row) for row in rows if isinstance(row, dict)]
        agent_path, agent_scores = _learn_alias_path(
            flattened_rows, _AGENT_KEYS, extension_aliases
        )
        queue_path, queue_scores = _learn_alias_path(
            flattened_rows, _QUEUE_KEYS, queue_aliases
        )
        login_path, login_scores = _learn_login_path(flattened_rows)

        set_matched = set_logged = 0
        used_agent_fields: set[str] = set()
        used_queue_fields: set[str] = set()
        used_login_fields: set[str] = set()
        unmatched = {
            "agent_not_found": 0,
            "queue_not_found": 0,
            "login_unknown": 0,
            "multiple_missing": 0,
        }

        for flat in flattened_rows:
            total_rows += 1
            agent = _resolve_path(flat, agent_path, extension_aliases)
            used_agent_path = agent_path if agent else None
            if not agent:
                agent, used_agent_path = _resolve_fallback(
                    flat, _AGENT_KEYS, extension_aliases
                )

            queue_id = _resolve_path(flat, queue_path, queue_aliases)
            used_queue_path = queue_path if queue_id else None
            if not queue_id:
                queue_id, used_queue_path = _resolve_fallback(
                    flat, _QUEUE_KEYS, queue_aliases
                )

            login = _login_from_path(flat, login_path)
            used_login_path = login_path if login is not None else None
            if login is None:
                login, used_login_path = _login_fallback(flat)

            if used_agent_path:
                used_agent_fields.add(used_agent_path)
            if used_queue_path:
                used_queue_fields.add(used_queue_path)
            if used_login_path:
                used_login_fields.add(used_login_path)

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

        confidence = {
            "agent": (agent_scores.get(agent_path, 0) if agent_path else 0),
            "queue": (queue_scores.get(queue_path, 0) if queue_path else 0),
            "login": (login_scores.get(login_path, 0) if login_path else 0),
        }
        results[name] = {
            "used": bool(rows),
            "pages": pages,
            "rows": len(rows),
            "matched_rows": set_matched,
            "logged_in_rows": set_logged,
            "learned_paths": {
                "agent": agent_path,
                "queue": queue_path,
                "login": login_path,
            },
            "path_confidence_hits": confidence,
            "agent_path_scores": agent_scores,
            "queue_path_scores": queue_scores,
            "login_path_scores": login_scores,
            "agent_fields": sorted(used_agent_fields),
            "queue_fields": sorted(used_queue_fields),
            "login_fields": sorted(used_login_fields),
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
