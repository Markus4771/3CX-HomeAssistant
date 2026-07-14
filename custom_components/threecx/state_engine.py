"""Centralized agent and queue state engine for the 3CX integration."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .api import ThreeCXSnapshot

_PHONE_PRIORITY = {
    "connected": "telefoniert",
    "held": "telefoniert",
    "transferred": "telefoniert",
    "ringing": "klingelt",
    "dialing": "waehlt",
}
_QUEUE_PRIORITY = {
    "pause": "pause",
    "wrap_up": "nachbearbeitung",
    "wrapup": "nachbearbeitung",
    "queue_login": "warteschleife_angemeldet",
    "login": "warteschleife_angemeldet",
    "resume": "warteschleife_angemeldet",
    "queue_logout": "warteschleife_abgemeldet",
    "logout": "warteschleife_abgemeldet",
}


def _agent_state(record) -> str:
    """Return one deterministic state for an extension."""
    attrs = record.status_attributes
    phone_state = str(attrs.get("live_phone_state") or "").strip().lower()
    queue_state = str(attrs.get("live_queue_state") or "").strip().lower()

    if phone_state in _PHONE_PRIORITY:
        return _PHONE_PRIORITY[phone_state]
    if queue_state in _QUEUE_PRIORITY:
        return _QUEUE_PRIORITY[queue_state]
    if record.queue_logged_in_names:
        return "warteschleife_angemeldet"
    if record.queue_names:
        return "warteschleife_abgemeldet"
    if record.registered is True:
        return "registriert"
    if record.registered is False:
        return "offline"
    return "unbekannt"


def apply_state_engine(snapshot: ThreeCXSnapshot) -> tuple[ThreeCXSnapshot, dict[str, Any]]:
    """Apply one consistent agent model to extension and queue records."""
    state_by_identity: dict[str, str] = {}
    updated_extensions = []

    for record in snapshot.extension_records:
        state = _agent_state(record)
        attrs = dict(record.status_fields)
        attrs["agent_state"] = state
        attrs["agent_state_source"] = "state_engine"
        updated = replace(record, status_fields=tuple(sorted(attrs.items())))
        updated_extensions.append(updated)
        for identity in (record.extension_id, record.number):
            if identity:
                state_by_identity[str(identity)] = state

    updated_queues = []
    queue_summary: dict[str, Any] = {}
    for queue in snapshot.queue_records:
        logged = set(queue.logged_in_members)
        counts = {
            "angemeldet": len(logged),
            "abgemeldet": 0,
            "pause": 0,
            "nachbearbeitung": 0,
            "klingelt": 0,
            "telefoniert": 0,
            "frei": 0,
            "offline": 0,
        }
        agent_states: dict[str, str] = {}
        for member in queue.members:
            state = state_by_identity.get(str(member), "unbekannt")
            agent_states[str(member)] = state
            if state == "pause":
                counts["pause"] += 1
            elif state == "nachbearbeitung":
                counts["nachbearbeitung"] += 1
            elif state == "klingelt":
                counts["klingelt"] += 1
            elif state == "telefoniert":
                counts["telefoniert"] += 1
            elif state == "offline":
                counts["offline"] += 1
            elif member in logged:
                counts["frei"] += 1
            else:
                counts["abgemeldet"] += 1

        raw = dict(queue.raw_fields)
        raw.update({
            "state_engine": True,
            "agent_states": agent_states,
            "state_counts": counts,
        })
        updated_queues.append(replace(queue, raw_fields=tuple(sorted(raw.items()))))
        queue_summary[queue.display_name] = {
            "number": queue.number,
            "members": len(queue.members),
            **counts,
        }

    snapshot.extension_records = tuple(updated_extensions)
    snapshot.queue_records = tuple(updated_queues)
    diagnostics = {
        "extensions": len(updated_extensions),
        "queues": len(updated_queues),
        "queue_summary": queue_summary,
        "state_model": [
            "offline", "registriert", "warteschleife_abgemeldet",
            "warteschleife_angemeldet", "pause", "nachbearbeitung",
            "klingelt", "waehlt", "telefoniert", "unbekannt",
        ],
    }
    return snapshot, diagnostics
