"""Reconstruct current queue logins from the 3CX AgentLoginHistory report."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from urllib.parse import quote

from .api import ThreeCXApiClient, ThreeCXApiError, ThreeCXSnapshot

_LOGGER = logging.getLogger(__name__)

_LOGIN_WORDS = ("login", "loggedin", "signin", "signedin")
_LOGOUT_WORDS = ("logout", "loggedout", "signout", "signedout")


def _normalized(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .casefold()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    )


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    if depth > 6:
        return {}
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, (dict, list)):
                result.update(_flatten(child, path, depth + 1))
            else:
                result[path] = child
    elif isinstance(value, list):
        for index, child in enumerate(value[:100]):
            result.update(_flatten(child, f"{prefix}[{index}]", depth + 1))
    return result


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_from_row(
    flat: dict[str, Any],
) -> tuple[bool | None, datetime | None, str | None, datetime | None, datetime | None]:
    """Return state, effective time, source, last login and last logout."""
    login_times: list[tuple[datetime, str]] = []
    logout_times: list[tuple[datetime, str]] = []
    direct: list[tuple[bool, str]] = []

    for path, raw in flat.items():
        key = _normalized(path.rsplit(".", 1)[-1].split("[", 1)[0])
        text = _normalized(raw)
        stamp = _parse_datetime(raw)

        if stamp is not None:
            if any(word in key for word in _LOGIN_WORDS):
                login_times.append((stamp, path))
            if any(word in key for word in _LOGOUT_WORDS):
                logout_times.append((stamp, path))
            continue

        if any(part in key for part in ("status", "state", "action", "event", "type", "operation")):
            if any(word in text for word in _LOGOUT_WORDS):
                direct.append((False, path))
            elif any(word in text for word in _LOGIN_WORDS):
                direct.append((True, path))
        if isinstance(raw, bool) and any(part in key for part in ("login", "logged", "active")):
            direct.append((raw, path))

    minimum = datetime.min.replace(tzinfo=timezone.utc)
    latest_login = max(login_times, default=(minimum, ""))
    latest_logout = max(logout_times, default=(minimum, ""))
    last_login = latest_login[0] if latest_login[0] > minimum else None
    last_logout = latest_logout[0] if latest_logout[0] > minimum else None

    # A report row describes one login session. An empty logout means that the
    # session is still open. If both timestamps exist, the later event wins.
    if last_login is not None and (last_logout is None or last_login > last_logout):
        return True, last_login, latest_login[1], last_login, last_logout
    if last_logout is not None:
        return False, last_logout, latest_logout[1], last_login, last_logout

    if direct:
        state, source = direct[-1]
        generic = [
            _parse_datetime(raw)
            for field, raw in flat.items()
            if any(part in _normalized(field) for part in ("time", "date", "timestamp", "created", "changed"))
        ]
        valid = [stamp for stamp in generic if stamp is not None]
        return state, max(valid) if valid else None, source, last_login, last_logout

    return None, None, None, last_login, last_logout


def _function_paths(
    queue_number: str,
    agent_number: str,
    start: datetime,
    end: datetime,
) -> tuple[str, ...]:
    start_text = start.isoformat(timespec="seconds").replace("+00:00", "Z")
    end_text = end.isoformat(timespec="seconds").replace("+00:00", "Z")
    queue_value = queue_number.replace("'", "''")
    agent_value = agent_number.replace("'", "''")
    orders = (
        f"clientTimeZone='UTC',startDt={start_text},endDt={end_text},queueDnStr='{queue_value}',agentDnStr='{agent_value}'",
        f"startDt={start_text},endDt={end_text},queueDnStr='{queue_value}',agentDnStr='{agent_value}',clientTimeZone='UTC'",
    )
    paths: list[str] = []
    for root in ("ReportAgentLoginHistory", "AgentLoginHistory"):
        for namespace in ("Pbx.", ""):
            for parameters in orders:
                raw = f"/xapi/v1/{root}/{namespace}GetAgentLoginHistoryData({parameters})?$top=100"
                paths.append(quote(raw, safe="/:?$=(),'._-+"))
    return tuple(dict.fromkeys(paths))


def _queue_extensions(snapshot: ThreeCXSnapshot, queue: Any) -> list[Any]:
    member_aliases = {_normalized(value) for value in queue.members if value}
    result = []
    for extension in snapshot.extension_records:
        aliases = {
            _normalized(extension.extension_id),
            _normalized(extension.number),
            _normalized(extension.name),
            _normalized(extension.email),
        }
        aliases.discard("")
        if aliases.intersection(member_aliases):
            result.append(extension)
    return result


async def async_apply_agent_login_history(
    client: ThreeCXApiClient,
    snapshot: ThreeCXSnapshot,
) -> tuple[ThreeCXSnapshot, dict[str, Any]]:
    """Apply the newest reconstructed queue state for every known member."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=30)
    diagnostics: dict[str, Any] = {
        "engine_version": 2,
        "available": False,
        "window_days": 30,
        "client_time_zone": "UTC",
        "query_mode": "per_queue_member",
        "queried_at": now.isoformat(),
        "queues": {},
        "decisions": {},
        "matched_rows": 0,
        "unmatched_rows": 0,
        "successful_queries": 0,
        "failed_queries": 0,
    }
    decisions: dict[tuple[str, str], dict[str, Any]] = {}

    for queue in snapshot.queue_records:
        members = _queue_extensions(snapshot, queue)
        queue_diag: dict[str, Any] = {
            "members_queried": len(members),
            "successful_queries": 0,
            "rows": 0,
            "matched_rows": 0,
            "agents": {},
        }
        for extension in members:
            agent_key = extension.number or extension.extension_id
            agent_diag: dict[str, Any] = {
                "number": extension.number,
                "selected_endpoint": None,
                "rows": 0,
                "matched_rows": 0,
                "field_names": [],
                "last_login": None,
                "last_logout": None,
                "last_event": None,
                "last_event_time": None,
                "derived_state": "unknown",
                "confidence": 0,
                "errors": [],
            }
            values: list[Any] = []
            for path in _function_paths(queue.number or queue.queue_id, agent_key, start, now):
                try:
                    values, _pages = await client._async_get_all_odata(path)  # noqa: SLF001
                    agent_diag["selected_endpoint"] = path
                    diagnostics["available"] = True
                    diagnostics["successful_queries"] += 1
                    queue_diag["successful_queries"] += 1
                    break
                except ThreeCXApiError as err:
                    agent_diag["errors"].append(f"{path}: {str(err)[:300]}")
            if agent_diag["selected_endpoint"] is None:
                diagnostics["failed_queries"] += 1

            fields: set[str] = set()
            agent_diag["rows"] = len(values)
            queue_diag["rows"] += len(values)
            for row in values:
                if not isinstance(row, dict):
                    diagnostics["unmatched_rows"] += 1
                    continue
                flat = _flatten(row)
                fields.update(flat)
                state, timestamp, source, row_login, row_logout = _event_from_row(flat)
                if row_login and (agent_diag["last_login"] is None or row_login.isoformat() > agent_diag["last_login"]):
                    agent_diag["last_login"] = row_login.isoformat()
                if row_logout and (agent_diag["last_logout"] is None or row_logout.isoformat() > agent_diag["last_logout"]):
                    agent_diag["last_logout"] = row_logout.isoformat()
                if state is None or timestamp is None:
                    diagnostics["unmatched_rows"] += 1
                    continue

                key = (queue.queue_id, extension.extension_id)
                previous = decisions.get(key)
                if previous is None or timestamp >= previous["at"]:
                    decisions[key] = {
                        "at": timestamp,
                        "logged_in": state,
                        "source_field": source or "unknown",
                    }
                agent_diag["matched_rows"] += 1
                queue_diag["matched_rows"] += 1
                diagnostics["matched_rows"] += 1

            decision = decisions.get((queue.queue_id, extension.extension_id))
            if decision:
                agent_diag["last_event"] = "login" if decision["logged_in"] else "logout"
                agent_diag["last_event_time"] = decision["at"].isoformat()
                agent_diag["derived_state"] = "logged_in" if decision["logged_in"] else "logged_out"
                agent_diag["confidence"] = 100
            agent_diag["field_names"] = sorted(fields)
            agent_diag["errors"] = agent_diag["errors"][-4:]
            queue_diag["agents"][agent_key] = agent_diag
        diagnostics["queues"][queue.display_name] = queue_diag

    logged_by_queue = {queue.queue_id: set(queue.logged_in_members) for queue in snapshot.queue_records}
    extension_queue_names = {
        extension.extension_id: set(extension.queue_logged_in_names)
        for extension in snapshot.extension_records
    }
    queues_by_id = {queue.queue_id: queue for queue in snapshot.queue_records}
    extensions_by_id = {extension.extension_id: extension for extension in snapshot.extension_records}

    for (queue_id, extension_id), decision in decisions.items():
        queue = queues_by_id.get(queue_id)
        extension = extensions_by_id.get(extension_id)
        if queue is None or extension is None:
            continue
        identity = extension.number or extension.extension_id
        if decision["logged_in"]:
            logged_by_queue[queue_id].add(identity)
            extension_queue_names[extension_id].add(queue.display_name)
        else:
            logged_by_queue[queue_id].discard(identity)
            extension_queue_names[extension_id].discard(queue.display_name)
        diagnostics["decisions"][f"{queue.number}:{extension.number}"] = {
            "logged_in": decision["logged_in"],
            "at": decision["at"].isoformat(),
            "source_field": decision["source_field"],
            "confidence": 100,
        }

    snapshot.queue_records = tuple(
        replace(queue, logged_in_members=tuple(sorted(logged_by_queue[queue.queue_id])))
        for queue in snapshot.queue_records
    )
    snapshot.extension_records = tuple(
        replace(
            extension,
            queue_logged_in_names=tuple(sorted(extension_queue_names[extension.extension_id])),
        )
        for extension in snapshot.extension_records
    )
    _LOGGER.info(
        "3CX Queue Engine 2.0: available=%s queries=%s matched=%s decisions=%s",
        diagnostics["available"],
        diagnostics["successful_queries"],
        diagnostics["matched_rows"],
        len(diagnostics["decisions"]),
    )
    return snapshot, diagnostics
