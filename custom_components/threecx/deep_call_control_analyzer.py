"""Deep diagnostics for 3CX Call Control traffic.

The analyzer records only payloads delivered by the websocket client callback.
Secrets, authorization headers and access tokens are never persisted.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from typing import Any


class DeepCallControlAnalyzer:
    """Keep a bounded, privacy-conscious trace of realtime payloads."""

    def __init__(self, limit: int = 200) -> None:
        self.limit = limit
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.frames: list[dict[str, Any]] = []
        self.event_types: Counter[str] = Counter()
        self.normalized_states: Counter[str] = Counter()
        self.channels: Counter[str] = Counter()
        self.unknown_events = 0
        self.queue_events = 0
        self.agent_events = 0
        self.last_packet_at: str | None = None

    @staticmethod
    def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON-safe payload with obvious secret fields removed."""
        blocked = {"authorization", "access_token", "token", "client_secret", "password"}

        def clean(value: Any, depth: int = 0) -> Any:
            if depth > 8:
                return "<max-depth>"
            if isinstance(value, dict):
                return {
                    str(key): "<redacted>" if str(key).casefold() in blocked else clean(child, depth + 1)
                    for key, child in list(value.items())[:200]
                }
            if isinstance(value, list):
                return [clean(child, depth + 1) for child in value[:200]]
            if value is None or isinstance(value, (str, int, float, bool)):
                text = value if not isinstance(value, str) else value[:4000]
                return text
            return str(value)[:1000]

        return clean(payload)

    def record(self, payload: dict[str, Any]) -> None:
        normalized = payload.get("_threecx_normalized", {})
        if not isinstance(normalized, dict):
            normalized = {}
        raw_type = str(normalized.get("raw_type", "unknown"))
        state = str(normalized.get("normalized_state", "unknown"))
        channel = str(payload.get("_threecx_channel", "unknown"))
        now = datetime.now(timezone.utc).isoformat()
        searchable = json.dumps(payload, ensure_ascii=False, default=str).casefold()

        self.event_types[raw_type] += 1
        self.normalized_states[state] += 1
        self.channels[channel] += 1
        if state == "unknown":
            self.unknown_events += 1
        if "queue" in searchable:
            self.queue_events += 1
        if "agent" in searchable or "extension" in searchable:
            self.agent_events += 1
        self.last_packet_at = now
        self.frames.append(
            {
                "at": now,
                "channel": channel,
                "raw_type": raw_type,
                "normalized_state": state,
                "field_names": list(normalized.get("field_names", []))[:200],
                "payload": self._safe_payload(payload),
            }
        )
        del self.frames[:-self.limit]

    def clear(self) -> None:
        self.frames.clear()
        self.event_types.clear()
        self.normalized_states.clear()
        self.channels.clear()
        self.unknown_events = 0
        self.queue_events = 0
        self.agent_events = 0
        self.last_packet_at = None
        self.started_at = datetime.now(timezone.utc).isoformat()

    def diagnostics(self, call_control: Any | None) -> dict[str, Any]:
        state = getattr(call_control, "state", None)
        endpoint_results = dict(getattr(state, "endpoint_results", {}) or {})
        channel_summary = {}
        for key, value in endpoint_results.items():
            if not isinstance(value, dict):
                continue
            channel_summary[key] = {
                "path": value.get("path"),
                "auth_mode": value.get("auth_mode"),
                "connected": value.get("connected"),
                "upgrade_success": value.get("success"),
                "frames_received": value.get("frames_received", 0),
                "duplicate_frames": value.get("duplicate_frames", 0),
                "non_utf8_frames": value.get("non_utf8_frames", 0),
                "last_frame_at": value.get("last_frame_at"),
                "error": value.get("error"),
            }
        return {
            "analyzer_version": 1,
            "started_at": self.started_at,
            "buffer_limit": self.limit,
            "received_packets": len(self.frames),
            "last_packet_at": self.last_packet_at,
            "event_types": dict(self.event_types),
            "normalized_states": dict(self.normalized_states),
            "channels_with_payloads": dict(self.channels),
            "unknown_events": self.unknown_events,
            "queue_events": self.queue_events,
            "agent_events": self.agent_events,
            "websocket_connected": bool(getattr(state, "connected", False)),
            "selected_channel": getattr(state, "selected_channel", None),
            "selected_by_event": bool(getattr(state, "selected_by_event", False)),
            "events_received_by_client": int(getattr(state, "events_received", 0) or 0),
            "connection_attempts": int(getattr(state, "connection_attempts", 0) or 0),
            "last_error": getattr(state, "last_error", None),
            "channel_results": channel_summary,
            "recent_packets": list(self.frames[-25:]),
            "interpretation": (
                "payloads_received"
                if self.frames
                else "websocket_upgrade_without_payloads"
                if getattr(state, "connected", False)
                else "no_active_websocket_channel"
            ),
        }
