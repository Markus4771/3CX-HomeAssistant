"""Install the 0.9.24 live-queue source policy into the coordinator."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import coordinator as _coordinator
from .agent_login_history import async_apply_agent_login_history as _history
from .live_queue_engine import async_apply_live_queue_status


async def _live_then_history(client: Any, snapshot: Any) -> tuple[Any, dict[str, Any]]:
    """Apply live state; execute history on a copy for diagnostics only."""
    live_snapshot, live_diagnostics = await async_apply_live_queue_status(client, snapshot)
    _unused_history_snapshot, history_diagnostics = await _history(
        client, deepcopy(live_snapshot)
    )
    history_diagnostics["status_source_enabled"] = False
    history_diagnostics["diagnostics_only"] = True
    history_diagnostics["live_queue_status"] = live_diagnostics
    history_diagnostics["selected_status_source"] = (
        "live_queue_api" if live_diagnostics.get("authoritative") else "configuration_or_call_control"
    )
    return live_snapshot, history_diagnostics


def apply_live_queue_policy() -> None:
    """Replace history-as-status with live-queue-first processing."""
    _coordinator.async_apply_agent_login_history = _live_then_history
