"""Experimental 3CX V20 Call Control realtime transport.

The public 3CX documentation does not expose a stable machine-readable endpoint
contract. This module therefore isolates endpoint discovery from the existing
Configuration API integration. Failure to connect never prevents normal 3CX
polling from working.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any
from urllib.parse import urljoin, urlparse

from aiohttp import ClientError, ClientSession, ClientWebSocketResponse, WSMsgType

_LOGGER = logging.getLogger(__name__)

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
TokenProvider = Callable[[], Awaitable[str]]


@dataclass(slots=True)
class CallControlState:
    """Current Call Control transport and last-event state."""

    connected: bool = False
    endpoint: str | None = None
    last_error: str | None = None
    last_event_type: str | None = None
    normalized_state: str = "unknown"
    last_event_at: str | None = None
    events_received: int = 0
    reconnects: int = 0
    call_id: str | None = None
    source: str | None = None
    destination: str | None = None
    direction: str | None = None
    last_event: dict[str, Any] = field(default_factory=dict)
    recent_events: list[dict[str, Any]] = field(default_factory=list)


def _normalized_key(value: str) -> str:
    return value.lower().replace("_", "").replace("-", "").replace(" ", "")


def _walk_scalars(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """Flatten scalar values from a small nested event for tolerant matching."""
    if depth > 6:
        return {}
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(_walk_scalars(child, path, depth + 1))
    elif isinstance(value, list):
        for index, child in enumerate(value[:20]):
            result.update(_walk_scalars(child, f"{prefix}[{index}]", depth + 1))
    elif value is None or isinstance(value, (str, int, float, bool)):
        result[prefix] = value
    return result


def _first_matching_value(flat: dict[str, Any], key_parts: tuple[str, ...]) -> str | None:
    """Return the first non-empty scalar whose final key matches one of the names."""
    normalized_parts = {_normalized_key(part) for part in key_parts}
    for path, value in flat.items():
        final_key = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if _normalized_key(final_key) in normalized_parts and value not in (None, ""):
            return str(value)
    return None


def normalize_call_control_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize unknown 3CX event shapes into stable diagnostic fields."""
    flat = _walk_scalars(payload)
    raw_type = _first_matching_value(
        flat, ("event", "eventType", "type", "name", "action", "state", "status")
    ) or "unknown"
    searchable = " ".join(str(value) for value in flat.values() if value is not None).lower()
    searchable += f" {raw_type.lower()}"

    if any(word in searchable for word in ("ringing", "ring", "incoming", "alerting")):
        normalized = "ringing"
    elif any(word in searchable for word in ("connected", "answered", "established", "talking")):
        normalized = "connected"
    elif any(word in searchable for word in ("dialing", "dialling", "outgoing", "calling")):
        normalized = "dialing"
    elif any(word in searchable for word in ("hold", "held", "onhold")):
        normalized = "held"
    elif any(word in searchable for word in ("transfer", "transferred")):
        normalized = "transferred"
    elif any(word in searchable for word in ("ended", "terminated", "disconnected", "hangup", "released")):
        normalized = "ended"
    elif "queue" in searchable and any(word in searchable for word in ("login", "loggedin", "logged in")):
        normalized = "queue_login"
    elif "queue" in searchable and any(word in searchable for word in ("logout", "loggedout", "logged out")):
        normalized = "queue_logout"
    else:
        normalized = "unknown"

    return {
        "raw_type": raw_type,
        "normalized_state": normalized,
        "call_id": _first_matching_value(
            flat, ("callId", "call_id", "connectionId", "legId", "id")
        ),
        "source": _first_matching_value(
            flat, ("source", "from", "caller", "callerId", "origin", "src")
        ),
        "destination": _first_matching_value(
            flat, ("destination", "to", "callee", "called", "target", "dst")
        ),
        "direction": _first_matching_value(flat, ("direction", "callDirection")),
        "field_names": sorted(flat.keys())[:200],
    }


class ThreeCXCallControlClient:
    """Maintain a resilient websocket connection to 3CX Call Control."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        verify_ssl: bool,
        token_provider: TokenProvider,
        candidate_paths: tuple[str, ...],
        event_callback: EventCallback,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._verify_ssl = verify_ssl
        self._token_provider = token_provider
        self._candidate_paths = candidate_paths
        self._event_callback = event_callback
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.state = CallControlState()

    def start(self) -> None:
        """Start the connection loop once."""
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(
                self._run(), name="threecx-call-control"
            )

    async def stop(self) -> None:
        """Stop the connection loop."""
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.state.connected = False

    def _websocket_url(self, path: str) -> str:
        http_url = urljoin(f"{self._base_url}/", path.lstrip("/"))
        parsed = urlparse(http_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return parsed._replace(scheme=scheme).geturl()

    async def _connect_candidate(
        self, path: str, token: str
    ) -> ClientWebSocketResponse:
        url = self._websocket_url(path)
        return await self._session.ws_connect(
            url,
            headers={"Authorization": f"Bearer {token}"},
            ssl=self._verify_ssl,
            heartbeat=30,
            receive_timeout=75,
            max_msg_size=4 * 1024 * 1024,
        )

    async def _discover_connection(self) -> ClientWebSocketResponse:
        token = await self._token_provider()
        errors: list[str] = []
        for path in self._candidate_paths:
            try:
                websocket = await self._connect_candidate(path, token)
                self.state.endpoint = path
                self.state.last_error = None
                return websocket
            except (ClientError, asyncio.TimeoutError, ValueError) as err:
                errors.append(f"{path}: {err}")
        raise ConnectionError("; ".join(errors) or "No Call Control endpoint configured")

    async def _handle_message(self, text: str) -> None:
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = {"type": "text", "value": text[:1000]}
        payload = decoded if isinstance(decoded, dict) else {"value": decoded}
        normalized = normalize_call_control_event(payload)
        timestamp = datetime.now(timezone.utc).isoformat()

        self.state.events_received += 1
        self.state.last_event_type = str(normalized["raw_type"])
        self.state.normalized_state = str(normalized["normalized_state"])
        self.state.last_event_at = timestamp
        self.state.call_id = normalized["call_id"]
        self.state.source = normalized["source"]
        self.state.destination = normalized["destination"]
        self.state.direction = normalized["direction"]
        self.state.last_event = payload
        self.state.recent_events.append(
            {
                "at": timestamp,
                "type": self.state.last_event_type,
                "state": self.state.normalized_state,
                "call_id": self.state.call_id,
                "source": self.state.source,
                "destination": self.state.destination,
            }
        )
        del self.state.recent_events[:-20]

        event_payload = dict(payload)
        event_payload["_threecx_normalized"] = normalized
        await self._event_callback(event_payload)

    async def _consume(self, websocket: ClientWebSocketResponse) -> None:
        self.state.connected = True
        async for message in websocket:
            if self._stop.is_set():
                break
            if message.type == WSMsgType.TEXT:
                await self._handle_message(message.data)
            elif message.type == WSMsgType.BINARY:
                try:
                    await self._handle_message(message.data.decode("utf-8"))
                except UnicodeDecodeError:
                    _LOGGER.debug("Ignored non-UTF8 3CX Call Control frame")
            elif message.type in (WSMsgType.CLOSED, WSMsgType.CLOSE, WSMsgType.ERROR):
                break
        self.state.connected = False

    async def _run(self) -> None:
        delay = 5
        while not self._stop.is_set():
            websocket: ClientWebSocketResponse | None = None
            try:
                websocket = await self._discover_connection()
                self.state.reconnects += 1
                delay = 5
                await self._consume(websocket)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # Transport must never break config polling.
                self.state.connected = False
                self.state.last_error = str(err)[:1000]
                _LOGGER.warning("3CX Call Control unavailable: %s", err)
            finally:
                if websocket is not None and not websocket.closed:
                    await websocket.close()
                self.state.connected = False
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                delay = min(delay * 2, 300)
