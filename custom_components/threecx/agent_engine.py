"""Central agent, queue and call engine for the 3CX integration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from .api import ThreeCXExtension, ThreeCXSnapshot

_PHONE_STATES = {
    "connected": "telefoniert",
    "held": "telefoniert",
    "transferred": "telefoniert",
    "ringing": "klingelt",
    "dialing": "waehlt",
}
_QUEUE_STATES = {
    "pause": "pause",
    "agent_pause": "pause",
    "wrap_up": "nachbearbeitung",
    "wrapup": "nachbearbeitung",
    "queue_login": "warteschleife_angemeldet",
    "login": "warteschleife_angemeldet",
    "resume": "warteschleife_angemeldet",
    "agent_resume": "warteschleife_angemeldet",
    "queue_logout": "warteschleife_abgemeldet",
    "logout": "warteschleife_abgemeldet",
}


@dataclass(frozen=True, slots=True)
class AgentModel:
    """One deterministic agent state assembled from every data source."""

    extension_id: str
    number: str
    name: str
    presence: str
    registered: bool | None
    member_of: tuple[str, ...]
    logged_in_to: tuple[str, ...]
    phone_state: str
    queue_state: str
    state: str
    source: str
    updated_at: str | None
    call_id: str | None
    peer: str | None
    direction: str | None

    def diagnostics(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "name": self.name,
            "presence": self.presence,
            "registered": self.registered,
            "member_of": list(self.member_of),
            "logged_in_to": list(self.logged_in_to),
            "phone_state": self.phone_state,
            "queue_state": self.queue_state,
            "state": self.state,
            "source": self.source,
            "updated_at": self.updated_at,
            "call_id": self.call_id,
            "peer": self.peer,
            "direction": self.direction,
        }


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _derive_agent(record: ThreeCXExtension) -> AgentModel:
    attrs = record.status_attributes
    phone_state = _text(attrs.get("live_phone_state")).lower()
    queue_state = _text(attrs.get("live_queue_state")).lower()
    updated_at = _text(attrs.get("live_updated_at")) or None

    if phone_state in _PHONE_STATES:
        state = _PHONE_STATES[phone_state]
        source = "call_control"
    elif queue_state in _QUEUE_STATES:
        state = _QUEUE_STATES[queue_state]
        source = "call_control"
    elif record.queue_logged_in_names:
        state = "warteschleife_angemeldet"
        source = "odata_or_configuration"
    elif record.queue_names:
        state = "warteschleife_abgemeldet"
        source = "configuration"
    elif record.registered is True:
        state = "registriert"
        source = "configuration"
    elif record.registered is False:
        state = "offline"
        source = "configuration"
    else:
        state = "unbekannt"
        source = "unknown"

    return AgentModel(
        extension_id=record.extension_id,
        number=record.number,
        name=record.name,
        presence=record.presence_status,
        registered=record.registered,
        member_of=record.queue_names,
        logged_in_to=record.queue_logged_in_names,
        phone_state=phone_state or "unknown",
        queue_state=queue_state or "unknown",
        state=state,
        source=source,
        updated_at=updated_at,
        call_id=_text(attrs.get("live_call_id")) or None,
        peer=_text(attrs.get("live_peer")) or None,
        direction=_text(attrs.get("live_direction")) or None,
    )


def _state_counts() -> dict[str, int]:
    return {
        "angemeldet": 0,
        "abgemeldet": 0,
        "pause": 0,
        "nachbearbeitung": 0,
        "klingelt": 0,
        "waehlt": 0,
        "telefoniert": 0,
        "frei": 0,
        "offline": 0,
        "unbekannt": 0,
    }


def apply_agent_engine(
    snapshot: ThreeCXSnapshot,
) -> tuple[ThreeCXSnapshot, dict[str, Any]]:
    """Build a single source of truth for agents, queues and active calls."""
    generated_at = datetime.now(timezone.utc).isoformat()
    agents: dict[str, AgentModel] = {}
    identity_map: dict[str, AgentModel] = {}
    updated_extensions: list[ThreeCXExtension] = []

    for record in snapshot.extension_records:
        agent = _derive_agent(record)
        agents[record.extension_id] = agent
        for identity in (record.extension_id, record.number):
            if identity:
                identity_map[str(identity)] = agent

        attrs = dict(record.status_fields)
        attrs.update(
            {
                "agent_state": agent.state,
                "agent_state_source": agent.source,
                "agent_engine_updated_at": generated_at,
                "agent_phone_state": agent.phone_state,
                "agent_queue_state": agent.queue_state,
            }
        )
        updated_extensions.append(
            replace(record, status_fields=tuple(sorted(attrs.items())))
        )

    queue_models: dict[str, Any] = {}
    updated_queues = []
    for queue in snapshot.queue_records:
        logged = set(queue.logged_in_members)
        counts = _state_counts()
        agent_states: dict[str, str] = {}
        resolved_agents: dict[str, dict[str, Any]] = {}

        for member in queue.members:
            identity = str(member)
            agent = identity_map.get(identity)
            state = agent.state if agent else "unbekannt"
            agent_states[identity] = state
            if agent:
                resolved_agents[identity] = {
                    "extension_id": agent.extension_id,
                    "number": agent.number,
                    "name": agent.name,
                    "state": state,
                    "source": agent.source,
                }

            if state == "pause":
                counts["pause"] += 1
            elif state == "nachbearbeitung":
                counts["nachbearbeitung"] += 1
            elif state == "klingelt":
                counts["klingelt"] += 1
            elif state == "waehlt":
                counts["waehlt"] += 1
            elif state == "telefoniert":
                counts["telefoniert"] += 1
            elif state == "offline":
                counts["offline"] += 1
            elif identity in logged:
                counts["angemeldet"] += 1
                counts["frei"] += 1
            elif state == "unbekannt":
                counts["unbekannt"] += 1
            else:
                counts["abgemeldet"] += 1

        raw = dict(queue.raw_fields)
        raw.update(
            {
                "agent_engine": True,
                "agent_engine_updated_at": generated_at,
                "agent_states": agent_states,
                "state_counts": counts,
                "resolved_agents": resolved_agents,
            }
        )
        updated_queues.append(replace(queue, raw_fields=tuple(sorted(raw.items()))))
        queue_models[queue.display_name] = {
            "queue_id": queue.queue_id,
            "number": queue.number,
            "members": len(queue.members),
            "logged_in_members": list(queue.logged_in_members),
            "state_counts": counts,
            "agent_states": agent_states,
        }

    snapshot.extension_records = tuple(updated_extensions)
    snapshot.queue_records = tuple(updated_queues)

    active_calls = {
        "count": snapshot.active_calls,
        "agents": {
            agent.extension_id: {
                "number": agent.number,
                "name": agent.name,
                "state": agent.state,
                "call_id": agent.call_id,
                "peer": agent.peer,
                "direction": agent.direction,
            }
            for agent in agents.values()
            if agent.state in {"klingelt", "waehlt", "telefoniert"}
        },
    }

    diagnostics = {
        "engine_version": 1,
        "generated_at": generated_at,
        "source_priority": [
            "call_control",
            "odata_entity_set",
            "configuration_api",
        ],
        "state_model": [
            "offline",
            "registriert",
            "warteschleife_abgemeldet",
            "warteschleife_angemeldet",
            "pause",
            "nachbearbeitung",
            "klingelt",
            "waehlt",
            "telefoniert",
            "unbekannt",
        ],
        "agent_count": len(agents),
        "queue_count": len(queue_models),
        "agents": {key: value.diagnostics() for key, value in agents.items()},
        "queues": queue_models,
        "calls": active_calls,
    }
    return snapshot, diagnostics
