"""Discover queue- and agent-related entity sets from 3CX OData metadata."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

from aiohttp import ClientError, ClientTimeout


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


async def async_discover_queue_agent_metadata(client: Any) -> dict[str, Any]:
    """Read the PBX-published OData metadata without guessing entity names."""
    path = "/xapi/v1/$metadata"
    result: dict[str, Any] = {
        "endpoint": path,
        "available": False,
        "entity_sets": [],
        "entity_types": [],
        "navigation_properties": [],
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
        result["error"] = f"Metadata XML ungültig: {err}"
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

    result.update(
        {
            "available": True,
            "entity_sets": sorted(entity_sets),
            "entity_types": sorted(entity_types),
            "navigation_properties": sorted(navigation),
            "metadata_size": len(text),
        }
    )
    return result
