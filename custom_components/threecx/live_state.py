"""Structured live state derived from 3CX Call Control events."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from .api import ThreeCXSnapshot


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _flatten(value: Any, result: dict[str, Any] | None = None) -> dict[str, Any]:
    result = result or {}
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


@dataclass(slots=True)
class LiveExtensionState:
    phone_state: str = "unknown"
    queue_state: str = "unknown"
    call_id: str | None = None
    peer: str | None = None
    direction: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class ThreeCXLiveState:
    """In-memory realtime state, merged into polling snapshots."""

    extensions: dict[str, LiveExtensionState] = field(default_factory=dict)
    active_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    queue_logins: dict[str, set[str]] = field(default_factory=dict)
    events_applied: int = 0
    events_ignored: int = 0
    last_applied_event: dict[str, Any] = field(default_factory=dict)

    def _extension(self, identity: str) -> LiveExtensionState:
        return self.extensions.setdefault(identity, LiveExtensionState())

    def ingest(self, payload: dict[str, Any], normalized: dict[str, Any]) -> bool:
        """Apply one normalized event when it can be mapped safely."""
        flat = _flatten(payload)
        state = _text(normalized.get("normalized_state")) or "unknown"
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
                logged = self.queue_logins.setdefault(queue, set())
                if state in {"queue_login", "agent_resume"}:
                    logged.add(extension)
                elif state == "queue_logout":
                    logged.discard(extension)
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
            self.last_applied_event = {**normalized, "extension": extension, "queue": queue}
        else:
            self.events_ignored += 1
        return applied

    def apply_to_snapshot(self, snapshot: ThreeCXSnapshot) -> ThreeCXSnapshot:
        """Merge realtime call and queue state into a fresh polling snapshot."""
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

        updated_queues = []
        for queue_record in snapshot.queue_records:
            live_members = set(queue_record.logged_in_members)
            for key in (queue_record.queue_id, queue_record.number, queue_record.display_name):
                live_members.update(self.queue_logins.get(key, set()))
            raw = dict(queue_record.raw_fields)
            raw.update({
                "live_logged_in_count": len(live_members),
                "live_state_source": "call_control+configuration_api",
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
        return {
            "events_applied": self.events_applied,
            "events_ignored": self.events_ignored,
            "active_calls": dict(self.active_calls),
            "queue_logins": {key: sorted(value) for key, value in self.queue_logins.items()},
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
