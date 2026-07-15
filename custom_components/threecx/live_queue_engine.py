"""Inspect live 3CX queue-agent records and apply only explicit live states."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from .api import ThreeCXApiClient, ThreeCXApiError, ThreeCXSnapshot

_TRUE_VALUES = {
    "true", "1", "yes", "on", "active", "available", "ready", "online",
    "loggedin", "signedin", "enabled",
}
_FALSE_VALUES = {
    "false", "0", "no", "off", "inactive", "unavailable", "notready",
    "offline", "loggedout", "signedout", "disabled",
}
_STATUS_PARTS = (
    "login", "logged", "signin", "signout", "active", "available", "ready",
    "queueagentstatus", "agentstatus", "queuestate", "agentstate",
)
_IDENTITY_KEYS = ("Number", "Extension", "ExtensionNumber", "AgentNo", "Dn", "Id")


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold().replace("_", "").replace("-", "").replace(" ", "")


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    if depth > 6:
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


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = _norm(value)
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return None


def _identity(row: dict[str, Any]) -> str:
    flat = _flatten(row)
    for wanted in _IDENTITY_KEYS:
        wanted_norm = _norm(wanted)
        for path, value in flat.items():
            if _norm(path.rsplit(".", 1)[-1].split("[", 1)[0]) == wanted_norm and value not in (None, ""):
                return str(value).strip()
    return ""


def _explicit_state(row: dict[str, Any]) -> tuple[bool | None, str | None, dict[str, Any]]:
    flat = _flatten(row)
    candidates: dict[str, Any] = {}
    positive: list[str] = []
    negative: list[str] = []
    for path, value in flat.items():
        normalized_path = _norm(path)
        if not any(part in normalized_path for part in _STATUS_PARTS):
            continue
        parsed = _parse_bool(value)
        if parsed is None:
            continue
        candidates[path] = value
        (positive if parsed else negative).append(path)
    if positive and not negative:
        return True, positive[0], candidates
    if negative and not positive:
        return False, negative[0], candidates
    return None, None, candidates


def _safe_sample(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {"value_type": type(row).__name__}
    flat = _flatten(row)
    sample: dict[str, Any] = {}
    for path, value in list(flat.items())[:80]:
        if value is None or isinstance(value, (str, int, float, bool)):
            sample[path] = value
    return sample


async def async_apply_live_queue_status(
    client: ThreeCXApiClient,
    snapshot: ThreeCXSnapshot,
) -> tuple[ThreeCXSnapshot, dict[str, Any]]:
    """Poll live queue agents and apply status only when a field is explicit."""
    diagnostics: dict[str, Any] = {
        "engine_version": 1,
        "polled_at": datetime.now(timezone.utc).isoformat(),
        "source": "live_queue_api",
        "authoritative": False,
        "queues": {},
        "decisions": {},
    }
    logged_by_queue = {queue.queue_id: set(queue.logged_in_members) for queue in snapshot.queue_records}

    for queue in snapshot.queue_records:
        path = f"/xapi/v1/Queues({queue.queue_id})/Agents?$top=100"
        queue_diag: dict[str, Any] = {
            "endpoint": path,
            "success": False,
            "rows": 0,
            "explicit_states": 0,
            "unknown_states": 0,
            "field_names": [],
            "status_candidates": {},
            "raw_samples": [],
            "error": None,
        }
        try:
            rows, _pages = await client._async_get_all_odata(path)  # noqa: SLF001
            queue_diag["success"] = True
        except ThreeCXApiError as err:
            queue_diag["error"] = str(err)[:500]
            diagnostics["queues"][queue.display_name] = queue_diag
            continue

        queue_diag["rows"] = len(rows)
        fields: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            flat = _flatten(row)
            fields.update(flat)
            if len(queue_diag["raw_samples"]) < 5:
                queue_diag["raw_samples"].append(_safe_sample(row))
            identity = _identity(row)
            state, source_field, candidates = _explicit_state(row)
            if candidates and identity:
                queue_diag["status_candidates"][identity] = candidates
            if not identity or state is None:
                queue_diag["unknown_states"] += 1
                continue
            queue_diag["explicit_states"] += 1
            if state:
                logged_by_queue[queue.queue_id].add(identity)
            else:
                logged_by_queue[queue.queue_id].discard(identity)
            diagnostics["decisions"][f"{queue.number}:{identity}"] = {
                "logged_in": state,
                "source_field": source_field,
                "confidence": 100,
            }
        queue_diag["field_names"] = sorted(fields)
        if queue_diag["explicit_states"] > 0:
            diagnostics["authoritative"] = True
        diagnostics["queues"][queue.display_name] = queue_diag

    snapshot.queue_records = tuple(
        replace(queue, logged_in_members=tuple(sorted(logged_by_queue[queue.queue_id])))
        for queue in snapshot.queue_records
    )
    return snapshot, diagnostics
