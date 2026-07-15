"""Experimental 3CX V20 Call Control realtime discovery transport.

All candidate websocket channels are isolated from Configuration API polling.
The client keeps every successful channel open, counts frames per channel and
selects the first channel that actually produces events instead of trusting the
first endpoint that merely accepts a websocket upgrade.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

from aiohttp import ClientError, ClientSession, ClientWebSocketResponse, WSMsgType

_LOGGER = logging.getLogger(__name__)

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
TokenProvider = Callable[[], Awaitable[str]]


@dataclass(slots=True, frozen=True)
class ChannelCandidate:
    """One websocket path and authentication mode."""

    path: str
    auth_mode: str

    @property
    def key(self) -> str:
        return f"{self.path} [{self.auth_mode}]"


@dataclass(slots=True)
class CallControlState:
    """Current Call Control discovery and last-event state."""

    connected: bool = False
    endpoint: str | None = None
    selected_channel: str | None = None
    selected_by_event: bool = False
    discovery_mode: str = "parallel"
    active_channels: list[str] = field(default_factory=list)
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
    candidate_paths: list[str] = field(default_factory=list)
    endpoint_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    connection_attempts: int = 0
    last_attempt_at: str | None = None


def _normalized_key(value: str) -> str:
    return value.lower().replace("_", "").replace("-", "").replace(" ", "")


def _walk_scalars(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
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

    # Queue states are checked before generic call states because some queue
    # payloads contain words such as available/connected as secondary values.
    if "queue" in searchable and any(
        word in searchable for word in ("logout", "loggedout", "logged out", "signout")
    ):
        normalized = "queue_logout"
    elif "queue" in searchable and any(
        word in searchable for word in ("login", "loggedin", "logged in", "signin")
    ):
        normalized = "queue_login"
    elif "queue" in searchable and any(word in searchable for word in ("pause", "paused")):
        normalized = "pause"
    elif "queue" in searchable and any(word in searchable for word in ("resume", "resumed", "unpause")):
        normalized = "resume"
    elif "queue" in searchable and any(word in searchable for word in ("wrapup", "wrap up", "after call")):
        normalized = "wrap_up"
    elif any(word in searchable for word in ("ringing", "ring", "incoming", "alerting")):
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
    """Discover and consume all viable 3CX realtime websocket channels."""

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
        self._channel_tasks: list[asyncio.Task[None]] = []
        self._recent_fingerprints: dict[str, float] = {}
        self.state = CallControlState(candidate_paths=list(candidate_paths))

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="threecx-call-control-discovery")

    async def stop(self) -> None:
        self._stop.set()
        for task in self._channel_tasks:
            task.cancel()
        if self._channel_tasks:
            await asyncio.gather(*self._channel_tasks, return_exceptions=True)
        self._channel_tasks.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.state.connected = False
        self.state.active_channels = []

    def _websocket_url(self, path: str, token: str | None = None) -> str:
        http_url = urljoin(f"{self._base_url}/", path.lstrip("/"))
        parsed = urlparse(http_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = parsed.query
        if token is not None:
            query_values = dict(item.split("=", 1) for item in query.split("&") if "=" in item)
            query_values["access_token"] = token
            query = urlencode(query_values)
        return urlunparse(parsed._replace(scheme=scheme, query=query))

    def _candidates(self) -> list[ChannelCandidate]:
        result: list[ChannelCandidate] = []
        for path in self._candidate_paths:
            result.append(ChannelCandidate(path, "bearer_header"))
            result.append(ChannelCandidate(path, "access_token_query"))
        return result

    async def _connect_candidate(
        self, candidate: ChannelCandidate, token: str
    ) -> ClientWebSocketResponse:
        headers: dict[str, str] = {}
        url_token: str | None = None
        if candidate.auth_mode == "bearer_header":
            headers["Authorization"] = f"Bearer {token}"
        else:
            url_token = token
        return await self._session.ws_connect(
            self._websocket_url(candidate.path, url_token),
            headers=headers,
            ssl=self._verify_ssl,
            heartbeat=30,
            receive_timeout=90,
            max_msg_size=4 * 1024 * 1024,
        )

    def _set_channel_connected(self, candidate: ChannelCandidate, connected: bool) -> None:
        active = set(self.state.active_channels)
        if connected:
            active.add(candidate.key)
        else:
            active.discard(candidate.key)
        self.state.active_channels = sorted(active)
        self.state.connected = bool(active)
        if connected and self.state.endpoint is None:
            self.state.endpoint = candidate.path
            self.state.selected_channel = candidate.key

    def _is_duplicate(self, text: str) -> bool:
        now = asyncio.get_running_loop().time()
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        previous = self._recent_fingerprints.get(digest)
        self._recent_fingerprints[digest] = now
        self._recent_fingerprints = {
            key: timestamp
            for key, timestamp in self._recent_fingerprints.items()
            if now - timestamp < 10
        }
        return previous is not None and now - previous < 2

    async def _handle_message(self, text: str, candidate: ChannelCandidate) -> None:
        channel = self.state.endpoint_results.setdefault(candidate.key, {})
        channel["frames_received"] = int(channel.get("frames_received", 0)) + 1
        channel["last_frame_at"] = datetime.now(timezone.utc).isoformat()

        if self._is_duplicate(text):
            channel["duplicate_frames"] = int(channel.get("duplicate_frames", 0)) + 1
            return

        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = {"type": "text", "value": text[:1000]}
        payload = decoded if isinstance(decoded, dict) else {"value": decoded}
        normalized = normalize_call_control_event(payload)
        timestamp = datetime.now(timezone.utc).isoformat()

        self.state.endpoint = candidate.path
        self.state.selected_channel = candidate.key
        self.state.selected_by_event = True
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
                "channel": candidate.key,
                "type": self.state.last_event_type,
                "state": self.state.normalized_state,
                "call_id": self.state.call_id,
                "source": self.state.source,
                "destination": self.state.destination,
            }
        )
        del self.state.recent_events[:-50]

        event_payload = dict(payload)
        event_payload["_threecx_normalized"] = normalized
        event_payload["_threecx_channel"] = candidate.key
        await self._event_callback(event_payload)

    async def _consume(
        self, websocket: ClientWebSocketResponse, candidate: ChannelCandidate
    ) -> None:
        async for message in websocket:
            if self._stop.is_set():
                break
            if message.type == WSMsgType.TEXT:
                await self._handle_message(message.data, candidate)
            elif message.type == WSMsgType.BINARY:
                try:
                    await self._handle_message(message.data.decode("utf-8"), candidate)
                except UnicodeDecodeError:
                    result = self.state.endpoint_results.setdefault(candidate.key, {})
                    result["non_utf8_frames"] = int(result.get("non_utf8_frames", 0)) + 1
            elif message.type in (WSMsgType.CLOSED, WSMsgType.CLOSE, WSMsgType.ERROR):
                break

    async def _run_candidate(self, candidate: ChannelCandidate) -> None:
        delay = 5
        while not self._stop.is_set():
            websocket: ClientWebSocketResponse | None = None
            attempted_at = datetime.now(timezone.utc).isoformat()
            self.state.connection_attempts += 1
            self.state.last_attempt_at = attempted_at
            try:
                token = await self._token_provider()
                websocket = await self._connect_candidate(candidate, token)
                self.state.reconnects += 1
                self._set_channel_connected(candidate, True)
                previous = self.state.endpoint_results.get(candidate.key, {})
                self.state.endpoint_results[candidate.key] = {
                    **previous,
                    "success": True,
                    "connected": True,
                    "auth_mode": candidate.auth_mode,
                    "path": candidate.path,
                    "attempted_at": attempted_at,
                    "url": self._websocket_url(candidate.path),
                    "error": None,
                    "frames_received": int(previous.get("frames_received", 0)),
                }
                delay = 5
                await self._consume(websocket, candidate)
            except asyncio.CancelledError:
                raise
            except (ClientError, asyncio.TimeoutError, ValueError, ConnectionError) as err:
                error_text = str(err)[:500]
                previous = self.state.endpoint_results.get(candidate.key, {})
                self.state.endpoint_results[candidate.key] = {
                    **previous,
                    "success": False,
                    "connected": False,
                    "auth_mode": candidate.auth_mode,
                    "path": candidate.path,
                    "attempted_at": attempted_at,
                    "url": self._websocket_url(candidate.path),
                    "error": error_text,
                    "frames_received": int(previous.get("frames_received", 0)),
                }
                self.state.last_error = f"{candidate.key}: {error_text}"
            finally:
                self._set_channel_connected(candidate, False)
                result = self.state.endpoint_results.setdefault(candidate.key, {})
                result["connected"] = False
                if websocket is not None and not websocket.closed:
                    await websocket.close()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                delay = min(delay * 2, 300)

    async def _run(self) -> None:
        self._channel_tasks = [
            asyncio.create_task(
                self._run_candidate(candidate),
                name=f"threecx-call-control-{index}",
            )
            for index, candidate in enumerate(self._candidates())
        ]
        try:
            await asyncio.gather(*self._channel_tasks)
        finally:
            self.state.connected = False
            self.state.active_channels = []
