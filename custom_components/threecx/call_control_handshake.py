"""Safe handshake discovery for the 3CX Call Control websocket.

The public endpoint accepts a websocket upgrade but may require a client hello
or subscription frame before it emits data.  This module installs a small,
read-only handshake sequence and deliberately disables access-token query URLs
because aiohttp error messages may expose the full token in diagnostics.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .call_control import ChannelCandidate, ThreeCXCallControlClient

_APPLIED = False
_RECORD_SEPARATOR = "\x1e"


def _handshake_frames(candidate: ChannelCandidate) -> tuple[tuple[str, str], ...]:
    """Return conservative read-only hello/subscription frames."""
    common_topics = ["calls", "queues", "agents", "presence"]
    return (
        (
            "signalr_json_handshake",
            json.dumps({"protocol": "json", "version": 1}, separators=(",", ":"))
            + _RECORD_SEPARATOR,
        ),
        (
            "generic_subscribe_topics",
            json.dumps(
                {"type": "subscribe", "topics": common_topics},
                separators=(",", ":"),
            ),
        ),
        (
            "generic_subscribe_events",
            json.dumps(
                {"action": "subscribe", "events": common_topics},
                separators=(",", ":"),
            ),
        ),
        (
            "signalr_subscribe_invocation",
            json.dumps(
                {
                    "type": 1,
                    "target": "Subscribe",
                    "arguments": [common_topics],
                },
                separators=(",", ":"),
            )
            + _RECORD_SEPARATOR,
        ),
        ("generic_hello", json.dumps({"type": "hello"}, separators=(",", ":"))),
    )


def apply_call_control_handshake_discovery() -> None:
    """Install handshake discovery once for all new Call Control clients."""
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    original_connect = ThreeCXCallControlClient._connect_candidate

    def bearer_candidates(self: ThreeCXCallControlClient) -> list[ChannelCandidate]:
        """Use bearer headers only so tokens never appear in diagnostic URLs."""
        return [ChannelCandidate(path, "bearer_header") for path in self._candidate_paths]

    async def connect_and_initialize(
        self: ThreeCXCallControlClient,
        candidate: ChannelCandidate,
        token: str,
    ) -> Any:
        websocket = await original_connect(self, candidate, token)
        result = self.state.endpoint_results.setdefault(candidate.key, {})
        attempts: list[dict[str, Any]] = []

        # Only the endpoint that has demonstrated a successful websocket upgrade
        # receives protocol probes. Other paths remain ordinary discovery probes.
        if candidate.path == "/callcontrol/ws":
            await asyncio.sleep(0.15)
            for name, frame in _handshake_frames(candidate):
                attempt: dict[str, Any] = {"name": name, "sent": False, "error": None}
                try:
                    await websocket.send_str(frame)
                    attempt["sent"] = True
                except Exception as err:  # connection-specific diagnostic only
                    attempt["error"] = str(err)[:300]
                    attempts.append(attempt)
                    break
                attempts.append(attempt)
                await asyncio.sleep(0.15)

        result["handshake_discovery_enabled"] = candidate.path == "/callcontrol/ws"
        result["handshake_attempts"] = attempts
        result["handshake_frames_sent"] = sum(1 for item in attempts if item["sent"])
        result["query_token_auth_disabled"] = True
        return websocket

    ThreeCXCallControlClient._candidates = bearer_candidates
    ThreeCXCallControlClient._connect_candidate = connect_and_initialize
