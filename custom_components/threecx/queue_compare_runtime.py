"""Runtime integration for the queue state comparator."""

from __future__ import annotations

from typing import Any

from .coordinator import ThreeCXDataUpdateCoordinator
from .queue_state_comparator import async_capture_queue_state, compare_captures

_APPLIED = False


def apply_queue_compare_runtime() -> None:
    """Add capture methods and diagnostics without changing coordinator layout."""
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    original_init = ThreeCXDataUpdateCoordinator.__init__
    original_monitor = ThreeCXDataUpdateCoordinator.live_monitor_diagnostics

    def patched_init(self: ThreeCXDataUpdateCoordinator, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.queue_compare_captures = {}
        self.queue_compare_result = {
            "ready": False,
            "instructions": "Capture logged_in first and logged_out second",
        }

    async def async_capture_queue_compare(
        self: ThreeCXDataUpdateCoordinator, label: str
    ) -> dict[str, Any]:
        capture = await async_capture_queue_state(self, label)
        self.queue_compare_captures[label] = capture
        logged_in = self.queue_compare_captures.get("logged_in")
        logged_out = self.queue_compare_captures.get("logged_out")
        if logged_in and logged_out:
            self.queue_compare_result = {
                "ready": True,
                **compare_captures(logged_in, logged_out),
            }
        else:
            self.queue_compare_result = {
                "ready": False,
                "captured": sorted(self.queue_compare_captures),
                "missing": "logged_out" if logged_in else "logged_in",
            }
        self.async_update_listeners()
        return capture

    def patched_monitor(self: ThreeCXDataUpdateCoordinator) -> dict[str, Any]:
        diagnostics = original_monitor(self)
        diagnostics["queue_state_comparator"] = {
            "captures": {
                label: {
                    "captured_at": value.get("captured_at"),
                    "queue_count": len(value.get("queues", {})),
                    "error": value.get("error"),
                }
                for label, value in self.queue_compare_captures.items()
            },
            "comparison": self.queue_compare_result,
        }
        return diagnostics

    ThreeCXDataUpdateCoordinator.__init__ = patched_init
    ThreeCXDataUpdateCoordinator.async_capture_queue_compare = async_capture_queue_compare
    ThreeCXDataUpdateCoordinator.live_monitor_diagnostics = patched_monitor
