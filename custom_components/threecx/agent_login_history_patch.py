"""Compatibility patch for 3CX AgentLoginHistory field names."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import agent_login_history as _history


def _fixed_event_from_row(
    flat: dict[str, Any],
) -> tuple[bool | None, datetime | None, str | None]:
    """Recognize the field names returned by 3CX V20 Update 9."""
    direct: list[tuple[bool, str]] = []
    login_times: list[tuple[datetime, str]] = []
    logout_times: list[tuple[datetime, str]] = []

    for path, raw in flat.items():
        key = _history._normalized(path.rsplit(".", 1)[-1].split("[", 1)[0])
        text = str(raw or "").strip().casefold()
        stamp = _history._parse_datetime(raw)

        if stamp is not None:
            if any(part in key for part in ("login", "loggedin", "signin", "signedin")):
                login_times.append((stamp, path))
            if any(part in key for part in ("logout", "loggedout", "signout", "signedout")):
                logout_times.append((stamp, path))
            continue

        if any(part in key for part in ("status", "state", "action", "event", "type", "operation")):
            if any(word in text for word in _history._LOGOUT_WORDS):
                direct.append((False, path))
            elif any(word in text for word in _history._LOGIN_WORDS):
                direct.append((True, path))

        if isinstance(raw, bool) and any(
            part in key for part in ("login", "logged", "active")
        ):
            direct.append((raw, path))

    if login_times or logout_times:
        minimum = datetime.min.replace(tzinfo=timezone.utc)
        latest_login = max(login_times, default=(minimum, ""))
        latest_logout = max(logout_times, default=(minimum, ""))
        if latest_login[0] > latest_logout[0]:
            return True, latest_login[0], latest_login[1]
        if latest_logout[0] > minimum:
            return False, latest_logout[0], latest_logout[1]

    if direct:
        state, source = direct[-1]
        timestamps = [
            _history._parse_datetime(raw)
            for field, raw in flat.items()
            if any(
                part in _history._normalized(field)
                for part in ("time", "date", "timestamp", "created", "changed")
            )
        ]
        valid = [stamp for stamp in timestamps if stamp is not None]
        return state, max(valid) if valid else None, source

    return None, None, None


def apply_patch() -> None:
    """Install the V20 Update 9 field-name compatibility fix."""
    _history._event_from_row = _fixed_event_from_row
