"""Read queue-agent login state from metadata-advertised OData entity sets."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .api import ThreeCXSnapshot

_AGENT_KEYS = (
    "Agent", "AgentNumber", "Extension", "ExtensionNumber", "Number",
    "User", "UserNumber", "DN", "DnNumber", "Member",
)
_QUEUE_KEYS = (
    "Queue", "QueueNumber", "QueueId", "QueueID", "QueueExtension",
)
_LOGIN_KEYS = (
    "IsLoggedIn", "LoggedIn", "QueueLoggedIn", "IsQueueLoggedIn",
    "AgentLoggedIn", "LoginStatus", "QueueStatus", "Status", "State",
    "IsActive", "Active", "Available",
)


def _norm(value: str) -> str:
    return value.lower().replace("_", "").replace("-", "").replace(" ", "")


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    if depth > 5:
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
        for index, child in enumerate(value[:20]):
            result.update(_flatten(child, f"{prefix}[{index}]", depth + 1))
    return result


def _pick(flat: dict[str, Any], keys: tuple[str, ...]) -> str:
    wanted = {_norm(key) for key in keys}
    for path, value in flat.items():
        final = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if _norm(final) in wanted and value not in (None, ""):
            return str(value).strip()
    return ""


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", "").replace("-", "")
        if normalized in {
            "true", "yes", "1", "on", "online", "active", "available",
            "loggedin", "login", "signedin", "ready",
        }:
            return True
        if normalized in {
            "false", "no", "0", "off", "offline", "inactive", "unavailable",
            "loggedout", "logout", "signedout", "notready",
        }:
            return False
    return None


def _pick_login(flat: dict[str, Any]) -> tuple[bool | None, str | None, Any]:
    wanted = {_norm(key) for key in _LOGIN_KEYS}
    for path, value in flat.items():
        final = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if _norm(final) not in wanted:
            continue
        parsed = _as_bool(value)
        if parsed is not None:
            return parsed, path, value
    return None, None, None


async def async_apply_entity_set_status(
    client: Any,
    snapshot: ThreeCXSnapshot,
    metadata: dict[str, Any],
) -> tuple[ThreeCXSnapshot, dict[str, Any]]:
    """Read successful metadata-discovered sets and merge unambiguous login states."""
    probes = metadata.get("entity_set_probes", {})
    if not isinstance(probes, dict):
        return snapshot, {"available": False, "reason": "Keine Entity-Set-Probes"}

    queue_aliases: dict[str, str] = {}
    for queue in snapshot.queue_records:
        for alias in (queue.queue_id, queue.number, queue.display_name):
            if alias:
                queue_aliases[str(alias)] = queue.queue_id

    extension_aliases: dict[str, str] = {}
    for extension in snapshot.extension_records:
        for alias in (extension.extension_id, extension.number):
            if alias:
                extension_aliases[str(alias)] = extension.number or extension.extension_id

    logged_by_queue: dict[str, set[str]] = {
        queue.queue_id: set(queue.logged_in_members) for queue in snapshot.queue_records
    }
    results: dict[str, Any] = {}
    total_rows = matched_rows = logged_rows = 0

    for name, probe in probes.items():
        if not isinstance(probe, dict) or not probe.get("success"):
            continue
        field_names = {str(item) for item in probe.get("field_names", [])}
        normalized_fields = {_norm(item) for item in field_names}
        has_agent = any(_norm(key) in normalized_fields for key in _AGENT_KEYS)
        has_queue = any(_norm(key) in normalized_fields for key in _QUEUE_KEYS)
        has_login = any(_norm(key) in normalized_fields for key in _LOGIN_KEYS)
        if not (has_agent and has_queue and has_login):
            results[name] = {
                "used": False,
                "reason": "Benötigte Agent-/Queue-/Loginfelder nicht gemeinsam vorhanden",
            }
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
        for row in rows:
            if not isinstance(row, dict):
                continue
            total_rows += 1
            flat = _flatten(row)
            agent_raw = _pick(flat, _AGENT_KEYS)
            queue_raw = _pick(flat, _QUEUE_KEYS)
            login, login_path, _ = _pick_login(flat)
            if login_path:
                login_fields.add(login_path)
            agent = extension_aliases.get(agent_raw)
            queue_id = queue_aliases.get(queue_raw)
            if not agent or not queue_id or login is None:
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
            "used": True,
            "pages": pages,
            "rows": len(rows),
            "matched_rows": set_matched,
            "logged_in_rows": set_logged,
            "login_fields": sorted(login_fields),
        }

    updated_queues = tuple(
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
    snapshot.queue_records = updated_queues
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
