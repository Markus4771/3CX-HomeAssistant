"""Discover and probe queue- and agent-related 3CX OData metadata."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from xml.etree import ElementTree

from aiohttp import ClientError, ClientTimeout

_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_STATUS_PARTS = (
    "login", "logged", "status", "state", "active", "available", "pause",
    "wrap", "queue", "agent", "user", "extension", "number", "dn",
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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
        for index, child in enumerate(value[:20]):
            result.update(_flatten(child, f"{prefix}[{index}]", depth + 1))
    return result


def _interesting_fields(values: list[Any]) -> tuple[list[str], list[str]]:
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
    if value in (None, ""):
        return None
    text = str(value).strip().casefold()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _anonymized_samples(values: list[Any]) -> list[dict[str, Any]]:
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
            interesting[path] = (
                raw if is_status_value and isinstance(raw, (bool, int, float))
                else _fingerprint(raw)
            )
        samples.append({"row": index + 1, "field_count": len(flat), "candidate_fields": interesting})
    return samples


async def _probe_get(client: Any, path: str) -> dict[str, Any]:
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
        fields, status_fields = _interesting_fields(values)
        return {
            "success": True,
            "endpoint": path,
            "http_status": response.status,
            "sample_count": len(values),
            "field_names": fields,
            "status_field_names": status_fields,
            "anonymized_samples": _anonymized_samples(values),
            "error": None,
        }
    except Exception as err:  # Diagnostic probes must never break the integration.
        return {
            "success": False,
            "endpoint": path,
            "http_status": None,
            "sample_count": 0,
            "field_names": [],
            "status_field_names": [],
            "anonymized_samples": [],
            "error": str(err)[:500],
        }


async def _async_probe_entity_sets(client: Any, entity_sets: list[str]) -> dict[str, dict[str, Any]]:
    probes: dict[str, dict[str, Any]] = {}
    for name in entity_sets[:30]:
        if not _SAFE_NAME.fullmatch(name):
            probes[name] = {"success": False, "error": "Unsicherer Entity-Set-Name verworfen"}
            continue
        probes[name] = await _probe_get(client, f"/xapi/v1/{name}?$top=5")
    return probes


def _operation_parameters(element: ElementTree.Element) -> list[dict[str, Any]]:
    parameters: list[dict[str, Any]] = []
    for child in element:
        if _local_name(child.tag) != "Parameter":
            continue
        parameters.append({
            "name": str(child.attrib.get("Name", "")),
            "type": str(child.attrib.get("Type", "")),
            "nullable": str(child.attrib.get("Nullable", "true")).lower() != "false",
        })
    return parameters


async def _probe_function_imports(client: Any, imports: list[dict[str, Any]]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for operation in imports[:30]:
        name = operation.get("name", "")
        if not _SAFE_NAME.fullmatch(name):
            continue
        parameters = operation.get("parameters", [])
        required = [p for p in parameters if not p.get("nullable", True)]
        if required:
            results[name] = {
                "tested": False,
                "reason": "Pflichtparameter vorhanden",
                "parameters": parameters,
            }
            continue
        # OData functions are side-effect free and may be called with GET.
        results[name] = {
            "tested": True,
            **await _probe_get(client, f"/xapi/v1/{name}()"),
            "parameters": parameters,
        }
    return results


async def async_discover_queue_agent_metadata(client: Any) -> dict[str, Any]:
    """Read PBX-published metadata and safely probe read-only operations."""
    path = "/xapi/v1/$metadata"
    result: dict[str, Any] = {
        "endpoint": path,
        "available": False,
        "entity_sets": [],
        "entity_types": [],
        "navigation_properties": [],
        "functions": [],
        "actions": [],
        "function_imports": [],
        "action_imports": [],
        "function_import_probes": {},
        "entity_set_probes": {},
        "error": None,
    }
    try:
        token = await client.async_authenticate()
        url = f"{client.base_url}{path}"
        async with client._session.get(  # noqa: SLF001
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/xml, text/xml, */*"},
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
    functions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    function_imports: list[dict[str, Any]] = []
    action_imports: list[dict[str, Any]] = []
    keywords = ("queue", "agent", "login", "logged", "presence")

    operation_defs: dict[str, dict[str, Any]] = {}
    for element in root.iter():
        kind = _local_name(element.tag)
        name = str(element.attrib.get("Name", ""))
        entity_type = str(element.attrib.get("EntityType", ""))
        operation_ref = str(element.attrib.get("Function", "") or element.attrib.get("Action", ""))
        target = f"{name} {entity_type} {operation_ref}".lower()
        if not any(keyword in target for keyword in keywords):
            continue
        if kind == "EntitySet" and name:
            entity_sets.add(name)
        elif kind == "EntityType" and name:
            entity_types.add(name)
        elif kind == "NavigationProperty" and name:
            navigation.add(name)
        elif kind in {"Function", "Action"} and name:
            item = {
                "name": name,
                "is_bound": str(element.attrib.get("IsBound", "false")).lower() == "true",
                "parameters": _operation_parameters(element),
            }
            operation_defs[name] = item
            (functions if kind == "Function" else actions).append(item)
        elif kind in {"FunctionImport", "ActionImport"} and name:
            ref = operation_ref.rsplit(".", 1)[-1]
            item = {
                "name": name,
                "operation": operation_ref,
                "parameters": operation_defs.get(ref, {}).get("parameters", []),
            }
            (function_imports if kind == "FunctionImport" else action_imports).append(item)

    sorted_sets = sorted(entity_sets)
    result.update({
        "available": True,
        "entity_sets": sorted_sets,
        "entity_types": sorted(entity_types),
        "navigation_properties": sorted(navigation),
        "functions": functions,
        "actions": actions,
        "function_imports": function_imports,
        "action_imports": action_imports,
        "metadata_size": len(text),
        "entity_set_probes": await _async_probe_entity_sets(client, sorted_sets),
        "function_import_probes": await _probe_function_imports(client, function_imports),
        "safety_note": "Nur parameterlose OData Functions werden per GET getestet; Actions werden niemals ausgefuehrt.",
    })
    return result
