"""Discover and probe queue- and agent-related 3CX OData entity sets."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from xml.etree import ElementTree

from aiohttp import ClientError, ClientTimeout


_SAFE_ENTITY_SET = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STATUS_PARTS = (
    "login",
    "logged",
    "status",
    "state",
    "active",
    "available",
    "pause",
    "wrap",
    "queue",
    "agent",
    "user",
    "extension",
    "number",
    "dn",
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """Flatten a small response row for field-path diagnostics."""
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
        for index, child in enumerate(value[:20]):
            result.update(_flatten(child, f"{prefix}[{index}]", depth + 1))
    return result


def _interesting_fields(values: list[Any]) -> tuple[list[str], list[str]]:
    """Return all and status-related field paths without exposing record values."""
    fields: set[str] = set()
    status_fields: set[str] = set()
    for value in values[:5]:
        if not isinstance(value, dict):
            continue
        for path in _flatten(value):
            fields.add(path)
            normalized = path.lower().replace("_", "").replace("-", "")
            if any(part in normalized for part in _STATUS_PARTS):
                status_fields.add(path)
    return sorted(fields), sorted(status_fields)


def _fingerprint(value: Any) -> str | None:
    """Create a stable, non-reversible short fingerprint for diagnostic matching."""
    if value in (None, ""):
        return None
    text = str(value).strip().casefold()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _anonymized_samples(values: list[Any]) -> list[dict[str, Any]]:
    """Return field paths and fingerprints for up to five rows, never raw identities."""
    samples: list[dict[str, Any]] = []
    for index, value in enumerate(values[:5]):
        if not isinstance(value, dict):
            continue
        flat = _flatten(value)
        interesting: dict[str, Any] = {}
        for path, raw in flat.items():
            normalized = path.lower().replace("_", "").replace("-", "")
            if not any(part in normalized for part in _STATUS_PARTS):
                continue
            final = path.rsplit(".", 1)[-1].split("[", 1)[0].lower()
            is_status_value = any(
                part in final
                for part in ("status", "state", "login", "logged", "active", "available", "pause", "wrap")
            )
            interesting[path] = raw if is_status_value and isinstance(raw, (bool, int, float)) else _fingerprint(raw)
        samples.append(
            {
                "row": index + 1,
                "field_count": len(flat),
                "candidate_fields": interesting,
            }
        )
    return samples


async def _async_probe_entity_sets(
    client: Any, entity_sets: list[str]
) -> dict[str, dict[str, Any]]:
    """Probe metadata-advertised sets with a small, read-only OData query."""
    probes: dict[str, dict[str, Any]] = {}
    for name in entity_sets[:20]:
        if not _SAFE_ENTITY_SET.fullmatch(name):
            probes[name] = {
                "success": False,
                "error": "Unsicherer Entity-Set-Name aus Metadaten verworfen",
            }
            continue
        path = f"/xapi/v1/{name}?$top=5"
        try:
            payload, response = await client._async_get(path)  # noqa: SLF001
            values = payload.get("value", []) if isinstance(payload, dict) else []
            if not isinstance(values, list):
                values = []
            fields, status_fields = _interesting_fields(values)
            probes[name] = {
                "success": True,
                "endpoint": path,
                "http_status": response.status,
                "sample_count": len(values),
                "field_names": fields,
                "status_field_names": status_fields,
                "anonymized_samples": _anonymized_samples(values),
                "privacy_note": "Identitaeten werden nur als SHA-256-Kurzfingerabdruck ausgegeben.",
            }
        except Exception as err:  # Probe failures must never break the integration.
            probes[name] = {
                "success": False,
                "endpoint": path,
                "error": str(err)[:500],
            }
    return probes


async def async_discover_queue_agent_metadata(client: Any) -> dict[str, Any]:
    """Read PBX-published OData metadata and probe relevant entity sets."""
    path = "/xapi/v1/$metadata"
    result: dict[str, Any] = {
        "endpoint": path,
        "available": False,
        "entity_sets": [],
        "entity_types": [],
        "navigation_properties": [],
        "entity_set_probes": {},
        "error": None,
    }
    try:
        token = await client.async_authenticate()
        url = f"{client.base_url}{path}"
        async with client._session.get(  # noqa: SLF001 - integration-internal helper
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/xml, text/xml, */*",
            },
            ssl=client._verify_ssl,  # noqa: SLF001
            timeout=ClientTimeout(total=20),
        ) as response:
            text = await response.text()
            if response.status >= 400:
                result["error"] = f"HTTP {response.status}: {text[:300]}"
                return result
    except (ClientError, TimeoutError, ValueError) as err:
        result["error"] = str(err)[:500]
        return result

    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as err:
        result["error"] = f"Metadata XML ungueltig: {err}"
        return result

    entity_sets: set[str] = set()
    entity_types: set[str] = set()
    navigation: set[str] = set()
    keywords = ("queue", "agent")

    for element in root.iter():
        kind = _local_name(element.tag)
        name = str(element.attrib.get("Name", ""))
        entity_type = str(element.attrib.get("EntityType", ""))
        target = f"{name} {entity_type}".lower()
        if not any(keyword in target for keyword in keywords):
            continue
        if kind == "EntitySet" and name:
            entity_sets.add(name)
        elif kind == "EntityType" and name:
            entity_types.add(name)
        elif kind == "NavigationProperty" and name:
            navigation.add(name)

    sorted_sets = sorted(entity_sets)
    result.update(
        {
            "available": True,
            "entity_sets": sorted_sets,
            "entity_types": sorted(entity_types),
            "navigation_properties": sorted(navigation),
            "metadata_size": len(text),
            "entity_set_probes": await _async_probe_entity_sets(client, sorted_sets),
        }
    )
    return result
