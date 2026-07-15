"""Capture and compare 3CX queue API responses in two known states."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .api import ThreeCXApiError


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    if depth > 8:
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
        for index, child in enumerate(value[:200]):
            result.update(_flatten(child, f"{prefix}[{index}]", depth + 1))
    return result


def _identity(row: Any, index: int) -> str:
    if not isinstance(row, dict):
        return str(index)
    for key in ("Number", "AgentNo", "Extension", "Id", "Name"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return str(index)


def _normalize_rows(rows: list[Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for index, row in enumerate(rows):
        key = _identity(row, index)
        # Duplicate identities remain visible rather than silently overwriting.
        suffix = 2
        base = key
        while key in normalized:
            key = f"{base}#{suffix}"
            suffix += 1
        normalized[key] = row
    return normalized


async def async_capture_queue_state(coordinator: Any, label: str) -> dict[str, Any]:
    """Capture all relevant read-only responses for one known queue state."""
    snapshot = coordinator.data
    capture: dict[str, Any] = {
        "label": label,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "queues": {},
    }
    if snapshot is None:
        capture["error"] = "Coordinator has no current snapshot"
        return capture

    for queue in snapshot.queue_records:
        queue_key = queue.display_name
        queue_capture: dict[str, Any] = {
            "queue_id": queue.queue_id,
            "number": queue.number,
            "endpoints": {},
        }
        paths = (
            f"/xapi/v1/Queues({queue.queue_id})/Agents?$top=100",
            f"/xapi/v1/Queues({queue.queue_id})?$expand=Agents",
            f"/xapi/v1/Queues({queue.queue_id})",
            f"/xapi/v1/Queues?$filter=Id eq {queue.queue_id}&$top=5",
        )
        for path in paths:
            try:
                payload, _response = await coordinator.client._async_get(path)  # noqa: SLF001
                values = payload.get("value") if isinstance(payload, dict) else None
                if isinstance(values, list):
                    data: Any = _normalize_rows(values)
                else:
                    data = payload
                queue_capture["endpoints"][path] = {
                    "success": True,
                    "data": data,
                    "flat": _flatten(data),
                }
            except ThreeCXApiError as err:
                queue_capture["endpoints"][path] = {
                    "success": False,
                    "error": str(err)[:500],
                }
        capture["queues"][queue_key] = queue_capture
    return capture


def compare_captures(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Return compact field-level differences between two captures."""
    changes: list[dict[str, Any]] = []
    first_queues = first.get("queues", {})
    second_queues = second.get("queues", {})
    for queue_name in sorted(set(first_queues) | set(second_queues)):
        first_eps = first_queues.get(queue_name, {}).get("endpoints", {})
        second_eps = second_queues.get(queue_name, {}).get("endpoints", {})
        for endpoint in sorted(set(first_eps) | set(second_eps)):
            before = first_eps.get(endpoint, {})
            after = second_eps.get(endpoint, {})
            if before.get("success") != after.get("success"):
                changes.append({
                    "queue": queue_name,
                    "endpoint": endpoint,
                    "field": "$success",
                    "before": before.get("success"),
                    "after": after.get("success"),
                })
            before_flat = before.get("flat", {}) if before.get("success") else {}
            after_flat = after.get("flat", {}) if after.get("success") else {}
            for field in sorted(set(before_flat) | set(after_flat)):
                old = before_flat.get(field)
                new = after_flat.get(field)
                if old != new:
                    changes.append({
                        "queue": queue_name,
                        "endpoint": endpoint,
                        "field": field,
                        "before": old,
                        "after": new,
                    })
    return {
        "first_label": first.get("label"),
        "first_captured_at": first.get("captured_at"),
        "second_label": second.get("label"),
        "second_captured_at": second.get("captured_at"),
        "change_count": len(changes),
        "changes": changes[:500],
        "truncated": len(changes) > 500,
    }
