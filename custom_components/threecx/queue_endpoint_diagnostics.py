"""Read-only diagnostic explorer for queue and agent endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from .api import ThreeCXApiError, ThreeCXSnapshot


def _field_names(value: Any, prefix: str = "", depth: int = 0) -> set[str]:
    """Return nested field paths without exposing values."""
    if depth > 6:
        return set()
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.add(path)
            if isinstance(child, (dict, list)):
                result.update(_field_names(child, path, depth + 1))
    elif isinstance(value, list):
        for child in value[:5]:
            result.update(_field_names(child, prefix, depth + 1))
    return result


def _queue_paths(queue: Any) -> tuple[str, ...]:
    """Build read-only endpoint candidates for one queue."""
    paths: list[str] = []
    for identifier in (queue.queue_id, queue.number):
        if not identifier:
            continue
        raw = str(identifier)
        encoded = quote(raw, safe="")
        escaped = raw.replace("'", "''")
        paths.extend(
            (
                f"/xapi/v1/Queues({encoded})/Agents?$top=5",
                f"/xapi/v1/Queues({encoded})/Agents?$expand=User&$top=5",
                f"/xapi/v1/Queues({encoded})/LoggedInAgents?$top=5",
                f"/xapi/v1/Queues({encoded})/LoggedInAgents?$expand=User&$top=5",
                f"/xapi/v1/Queues({encoded})/ActiveAgents?$top=5",
                f"/xapi/v1/Queues({encoded})/ActiveAgents?$expand=User&$top=5",
                f"/xapi/v1/Queues({encoded})/Members?$top=5",
                f"/xapi/v1/Queues({encoded})/AgentStatus?$top=5",
                f"/xapi/v1/Queues({encoded})/QueueStatus?$top=5",
                f"/xapi/v1/Queues('{escaped}')/Agents?$top=5",
                f"/xapi/v1/Queues('{escaped}')/LoggedInAgents?$top=5",
                f"/xapi/v1/Queues('{escaped}')/ActiveAgents?$top=5",
                f"/xapi/v1/Queues/{encoded}/Agents?$top=5",
                f"/xapi/v1/Queues/{encoded}/LoggedInAgents?$top=5",
                f"/xapi/v1/Queues/{encoded}/ActiveAgents?$top=5",
                f"/xapi/v1/QueueAgents?$filter=QueueId eq {encoded}&$top=5",
                f"/xapi/v1/QueueAgents?$filter=QueueNumber eq '{escaped}'&$top=5",
                f"/xapi/v1/QueueAgentStatus?$filter=QueueId eq {encoded}&$top=5",
                f"/xapi/v1/QueueAgentStatus?$filter=QueueNumber eq '{escaped}'&$top=5",
            )
        )
    return tuple(dict.fromkeys(paths))


async def _probe(client: Any, path: str) -> dict[str, Any]:
    """Probe one endpoint and return compact diagnostics."""
    try:
        payload, response = await client._async_get(path)  # noqa: SLF001
        if isinstance(payload, dict):
            values = payload.get("value", [])
            if not isinstance(values, list):
                values = [payload]
        elif isinstance(payload, list):
            values = payload
        else:
            values = []
        fields: set[str] = set()
        for value in values[:5]:
            fields.update(_field_names(value))
        return {
            "success": True,
            "http_status": response.status,
            "row_count": len(values),
            "field_names": sorted(fields)[:200],
        }
    except ThreeCXApiError as err:
        return {"success": False, "error": str(err)[:500]}
    except Exception as err:  # Diagnostic failures must never break polling.
        return {"success": False, "error": f"{type(err).__name__}: {err}"[:500]}


async def async_run_queue_endpoint_diagnostics(
    client: Any,
    snapshot: ThreeCXSnapshot,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Probe metadata-advertised and queue-specific endpoints read-only."""
    result: dict[str, Any] = {
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "queues": {},
        "metadata_entity_sets": {},
        "successful_endpoints": 0,
        "failed_endpoints": 0,
    }

    probes = metadata.get("entity_set_probes", {}) if isinstance(metadata, dict) else {}
    if isinstance(probes, dict):
        for name, probe in probes.items():
            if not isinstance(probe, dict):
                continue
            result["metadata_entity_sets"][name] = {
                "success": bool(probe.get("success")),
                "endpoint": probe.get("endpoint"),
                "http_status": probe.get("http_status"),
                "sample_count": probe.get("sample_count", 0),
                "field_names": list(probe.get("field_names", []))[:200],
                "status_field_names": list(probe.get("status_field_names", []))[:200],
                "anonymized_samples": list(probe.get("anonymized_samples", []))[:5],
                "error": probe.get("error"),
            }

    for queue in snapshot.queue_records:
        endpoint_results: dict[str, Any] = {}
        for path in _queue_paths(queue):
            probe = await _probe(client, path)
            endpoint_results[path] = probe
            if probe.get("success"):
                result["successful_endpoints"] += 1
            else:
                result["failed_endpoints"] += 1
        result["queues"][queue.display_name] = {
            "queue_id": queue.queue_id,
            "number": queue.number,
            "member_count": len(queue.members),
            "current_logged_in_count": len(queue.logged_in_members),
            "endpoint_results": endpoint_results,
        }

    return result
