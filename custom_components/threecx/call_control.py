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
    """Current Call Control transport state."""

    connected: bool = False
    endpoint: str | None = None
    last_error: str | None = None
    last_event_type: str | None = None
    last_event_at: str | None = None
    events_received: int = 0
    reconnects: int = 0
    last_event: dict[str, Any] = field(default_factory=dict)


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

    @staticmethod
    def _event_type(payload: dict[str, Any]) -> str:
        for key in ("event", "eventType", "type", "name", "action", "state"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        return "unknown"

    async def _handle_message(self, text: str) -> None:
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = {"type": "text", "value": text[:1000]}
        payload = decoded if isinstance(decoded, dict) else {"value": decoded}
        event_type = self._event_type(payload)
        self.state.events_received += 1
        self.state.last_event_type = event_type
        self.state.last_event_at = datetime.now(timezone.utc).isoformat()
        self.state.last_event = payload
        await self._event_callback(payload)

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
