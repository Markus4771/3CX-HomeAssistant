"""Structured live state derived from 3CX Call Control events."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from .api import ThreeCXSnapshot

_QUEUE_OVERRIDE_TTL = timedelta(minutes=15)


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _flatten(value: Any, result: dict[str, Any] | None = None) -> dict[str, Any]:
    if result is None:
        result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                _flatten(child, result)
            elif child not in (None, ""):
                result.setdefault(str(key).lower().replace("_", ""), child)
    elif isinstance(value, list):
        for child in value[:20]:
            _flatten(child, result)
    return result


def _pick(flat: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = flat.get(key.lower().replace("_", ""))
        if value not in (None, ""):
            return _text(value)
    return ""


def _live_state(flat: dict[str, Any], normalized: dict[str, Any]) -> str:
    """Prefer explicit queue/agent transitions over generic call words."""
    original = _text(normalized.get("normalized_state")) or "unknown"
    searchable = " ".join(_text(value).lower() for value in flat.values())
    queue_related = "queue" in searchable or "agent" in searchable
    if queue_related:
        if any(word in searchable for word in ("logout", "loggedout", "logoff", "signedout")):
            return "queue_logout"
        if any(word in searchable for word in ("login", "loggedin", "logon", "signedin")):
            return "queue_login"
        if any(word in searchable for word in ("wrapup", "wrap-up", "aftercall", "after call")):
            return "wrap_up"
        if any(word in searchable for word in ("resume", "unpause", "available")):
            return "agent_resume"
        if any(word in searchable for word in ("pause", "paused", "break")):
            return "agent_pause"
    return original


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(slots=True)
class LiveExtensionState:
    phone_state: str = "unknown"
    queue_state: str = "unknown"
    call_id: str | None = None
    peer: str | None = None
    direction: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class QueueAgentDecision:
    """One authoritative, time-limited queue decision from Call Control."""

    logged_in: bool
    state: str
    source: str
    updated_at: str

    def is_fresh(self, now: datetime) -> bool:
        timestamp = _parse_time(self.updated_at)
        return timestamp is not None and now - timestamp <= _QUEUE_OVERRIDE_TTL


@dataclass(slots=True)
class ThreeCXLiveState:
    """In-memory realtime state, merged into polling snapshots."""

    extensions: dict[str, LiveExtensionState] = field(default_factory=dict)
    active_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    queue_logins: dict[str, set[str]] = field(default_factory=dict)
    queue_decisions: dict[str, dict[str, QueueAgentDecision]] = field(default_factory=dict)
    events_applied: int = 0
    events_ignored: int = 0
    last_applied_event: dict[str, Any] = field(default_factory=dict)

    def _extension(self, identity: str) -> LiveExtensionState:
        return self.extensions.setdefault(identity, LiveExtensionState())

    def _set_queue_decision(
        self, queue: str, extension: str, logged_in: bool, state: str, now: str
    ) -> None:
        decisions = self.queue_decisions.setdefault(queue, {})
        decisions[extension] = QueueAgentDecision(
            logged_in=logged_in,
            state=state,
            source="call_control",
            updated_at=now,
        )
        logged = self.queue_logins.setdefault(queue, set())
        if logged_in:
            logged.add(extension)
        else:
            logged.discard(extension)

    def ingest(self, payload: dict[str, Any], normalized: dict[str, Any]) -> bool:
        """Apply one normalized event when it can be mapped safely."""
        flat = _flatten(payload)
        state = _live_state(flat, normalized)
        extension = _pick(
            flat,
            "extension", "extensionnumber", "dnnumber", "usernumber",
            "agent", "agentnumber", "participantnumber",
        )
        queue = _pick(flat, "queue", "queuenumber", "queueid", "queueextension")
        source = _text(normalized.get("source"))
        destination = _text(normalized.get("destination"))
        call_id = _text(normalized.get("call_id"))
        identities = {value for value in (extension, source, destination) if value}
        now = datetime.now(timezone.utc).isoformat()
        applied = False

        if state in {"queue_login", "queue_logout", "agent_pause", "agent_resume", "wrap_up"} and extension:
            ext = self._extension(extension)
            ext.queue_state = state.removeprefix("agent_")
            ext.updated_at = now
            if queue:
                if state in {"queue_login", "agent_resume"}:
                    self._set_queue_decision(queue, extension, True, state, now)
                elif state == "queue_logout":
                    self._set_queue_decision(queue, extension, False, state, now)
                else:
                    # Pause and wrap-up preserve login membership but keep the live state.
                    currently_logged = extension in self.queue_logins.get(queue, set())
                    self._set_queue_decision(queue, extension, currently_logged, state, now)
            applied = True

        if state in {"ringing", "dialing", "connected", "held", "transferred", "ended"}:
            for identity in identities:
                ext = self._extension(identity)
                ext.phone_state = "idle" if state == "ended" else state
                ext.call_id = call_id or None
                ext.direction = _text(normalized.get("direction")) or None
                ext.peer = destination if identity == source else source or destination or None
                ext.updated_at = now
                applied = True
            if call_id:
                if state == "ended":
                    self.active_calls.pop(call_id, None)
                else:
                    self.active_calls[call_id] = {
                        "state": state,
                        "source": source or None,
                        "destination": destination or None,
                        "direction": normalized.get("direction"),
                        "updated_at": now,
                    }
                applied = True

        if applied:
            self.events_applied += 1
            self.last_applied_event = {
                **normalized,
                "normalized_state": state,
                "extension": extension,
                "queue": queue,
            }
        else:
            self.events_ignored += 1
        return applied

    def apply_to_snapshot(self, snapshot: ThreeCXSnapshot) -> ThreeCXSnapshot:
        """Merge polling and realtime data using fresh Call Control decisions first."""
        updated_extensions = []
        for record in snapshot.extension_records:
            live = self.extensions.get(record.number) or self.extensions.get(record.extension_id)
            if live is None:
                updated_extensions.append(record)
                continue
            status = dict(record.status_fields)
            status.update({
                "live_phone_state": live.phone_state,
                "live_queue_state": live.queue_state,
                "live_call_id": live.call_id,
                "live_peer": live.peer,
                "live_direction": live.direction,
                "live_updated_at": live.updated_at,
            })
            updated_extensions.append(replace(record, status_fields=tuple(sorted(status.items()))))

        now = datetime.now(timezone.utc)
        updated_queues = []
        for queue_record in snapshot.queue_records:
            live_members = set(queue_record.logged_in_members)
            applied_decisions = 0
            newest_update: str | None = None
            for key in (queue_record.queue_id, queue_record.number, queue_record.display_name):
                for extension, decision in self.queue_decisions.get(key, {}).items():
                    if not decision.is_fresh(now):
                        continue
                    applied_decisions += 1
                    if newest_update is None or decision.updated_at > newest_update:
                        newest_update = decision.updated_at
                    if decision.logged_in:
                        live_members.add(extension)
                    else:
                        live_members.discard(extension)
            raw = dict(queue_record.raw_fields)
            raw.update({
                "live_logged_in_count": len(live_members),
                "live_state_source": (
                    "call_control>odata>configuration_api"
                    if applied_decisions else "odata>configuration_api"
                ),
                "live_decisions_applied": applied_decisions,
                "live_last_updated_at": newest_update,
                "live_override_ttl_seconds": int(_QUEUE_OVERRIDE_TTL.total_seconds()),
            })
            updated_queues.append(replace(
                queue_record,
                logged_in_members=tuple(sorted(live_members)),
                raw_fields=tuple(sorted(raw.items())),
            ))

        snapshot.extension_records = tuple(updated_extensions)
        snapshot.queue_records = tuple(updated_queues)
        snapshot.active_calls = len(self.active_calls)
        return snapshot

    def diagnostics(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "events_applied": self.events_applied,
            "events_ignored": self.events_ignored,
            "active_calls": dict(self.active_calls),
            "queue_logins": {key: sorted(value) for key, value in self.queue_logins.items()},
            "queue_decisions": {
                queue: {
                    extension: {
                        "logged_in": decision.logged_in,
                        "state": decision.state,
                        "source": decision.source,
                        "updated_at": decision.updated_at,
                        "fresh": decision.is_fresh(now),
                    }
                    for extension, decision in decisions.items()
                }
                for queue, decisions in self.queue_decisions.items()
            },
            "source_priority": ["call_control", "odata_entity_set", "configuration_api"],
            "queue_override_ttl_seconds": int(_QUEUE_OVERRIDE_TTL.total_seconds()),
            "extension_states": {
                key: {
                    "phone_state": value.phone_state,
                    "queue_state": value.queue_state,
                    "call_id": value.call_id,
                    "peer": value.peer,
                    "direction": value.direction,
                    "updated_at": value.updated_at,
                }
                for key, value in self.extensions.items()
            },
            "last_applied_event": self.last_applied_event,
        }
