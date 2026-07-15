"""Reconstruct current queue logins from the 3CX AgentLoginHistory function."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from urllib.parse import quote

from .api import ThreeCXApiClient, ThreeCXApiError, ThreeCXSnapshot

_LOGGER = logging.getLogger(__name__)

_LOGIN_WORDS = ("login", "loggedin", "logged in", "signin", "signedin", "signed in")
_LOGOUT_WORDS = ("logout", "loggedout", "logged out", "signout", "signedout", "signed out")


def _normalized(value: Any) -> str:
    return str(value or "").strip().casefold().replace("_", "").replace("-", "").replace(" ", "")


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


def _event_from_row(flat: dict[str, Any]) -> tuple[bool | None, datetime | None, str | None]:
    """Return logged-in state, timestamp and source field from one history row."""
    direct: list[tuple[datetime | None, bool, str]] = []
    login_times: list[tuple[datetime, str]] = []
    logout_times: list[tuple[datetime, str]] = []

    for path, raw in flat.items():
        key = _normalized(path.rsplit(".", 1)[-1].split("[", 1)[0])
        text = str(raw or "").strip().casefold()
        timestamp = _parse_datetime(raw)

        if timestamp is not None:
            if any(word.replace(" ", "") in key for word in ("login", "signin")):
                login_times.append((timestamp, path))
            if any(word.replace(" ", "") in key for word in ("logout", "signout")):
                logout_times.append((timestamp, path))
            continue

        if any(part in key for part in ("status", "state", "action", "event", "type", "operation")):
            event_time = None
            if any(word in text for word in _LOGOUT_WORDS):
                direct.append((event_time, False, path))
            elif any(word in text for word in _LOGIN_WORDS):
                direct.append((event_time, True, path))

        if isinstance(raw, bool) and any(part in key for part in ("login", "logged", "active")):
            direct.append((None, raw, path))

    # A row containing a login and no logout represents an open session.
    if login_times or logout_times:
        latest_login = max(login_times, default=(datetime.min.replace(tzinfo=timezone.utc), ""))
        latest_logout = max(logout_times, default=(datetime.min.replace(tzinfo=timezone.utc), ""))
        if latest_login[0] > latest_logout[0]:
            return True, latest_login[0], latest_login[1]
        if latest_logout[0] > datetime.min.replace(tzinfo=timezone.utc):
            return False, latest_logout[0], latest_logout[1]

    if direct:
        _time, state, path = direct[-1]
        generic_times = [
            (_parse_datetime(raw), field)
            for field, raw in flat.items()
            if any(part in _normalized(field) for part in ("time", "date", "timestamp", "created", "changed"))
        ]
        valid_times = [(stamp, field) for stamp, field in generic_times if stamp is not None]
        stamp = max(valid_times, default=(None, ""), key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))[0]
        return state, stamp, path

    return None, None, None


def _candidate_values(flat: dict[str, Any], role: str) -> list[str]:
    values: list[str] = []
    role_parts = ("agent", "user", "extension", "dn", "member") if role == "agent" else ("queue",)
    identity_parts = ("id", "number", "dn", "name", "email", "extension")
    for path, raw in flat.items():
        normalized_path = _normalized(path)
        if not any(part in normalized_path for part in role_parts):
            continue
        final = _normalized(path.rsplit(".", 1)[-1].split("[", 1)[0])
        if not any(part in final for part in identity_parts):
            continue
        if raw not in (None, ""):
            values.append(str(raw).strip())
    return values


def _resolve_alias(candidates: list[str], aliases: dict[str, str]) -> str | None:
    matches = {aliases[_normalized(value)] for value in candidates if _normalized(value) in aliases}
    return next(iter(matches)) if len(matches) == 1 else None


def _function_paths(queue_number: str, start: datetime, end: datetime) -> tuple[str, ...]:
    start_text = start.isoformat(timespec="seconds").replace("+00:00", "Z")
    end_text = end.isoformat(timespec="seconds").replace("+00:00", "Z")
    queue_value = queue_number.replace("'", "''")
    params = (
        f"clientTimeZone=null,startDt={start_text},endDt={end_text},"
        f"queueDnStr='{queue_value}',agentDnStr=null"
    )
    params_alt = (
        f"startDt={start_text},endDt={end_text},queueDnStr='{queue_value}',"
        f"agentDnStr=null,clientTimeZone=null"
    )
    roots = ("AgentLoginHistory", "ReportAgentLoginHistory")
    paths: list[str] = []
    for root in roots:
        for namespace in ("Pbx.", ""):
            for arguments in (params, params_alt):
                raw = f"/xapi/v1/{root}/{namespace}GetAgentLoginHistoryData({arguments})?$top=100"
                # Keep OData punctuation intact while escaping spaces and unusual characters.
                paths.append(quote(raw, safe="/:?$=(),'._-+"))
    return tuple(dict.fromkeys(paths))


async def async_apply_agent_login_history(
    client: ThreeCXApiClient,
    snapshot: ThreeCXSnapshot,
) -> tuple[ThreeCXSnapshot, dict[str, Any]]:
    """Apply latest login/logout history decisions to queue and extension records."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=30)

    extension_aliases: dict[str, str] = {}
    for extension in snapshot.extension_records:
        for alias in (
            extension.extension_id,
            extension.number,
            extension.name,
            extension.email,
        ):
            if alias:
                extension_aliases[_normalized(alias)] = extension.extension_id

    queue_aliases: dict[str, str] = {}
    for queue in snapshot.queue_records:
        for alias in (queue.queue_id, queue.number, queue.name, queue.display_name):
            if alias:
                queue_aliases[_normalized(alias)] = queue.queue_id

    diagnostics: dict[str, Any] = {
        "available": False,
        "window_days": 30,
        "queried_at": now.isoformat(),
        "queues": {},
        "decisions": {},
        "matched_rows": 0,
        "unmatched_rows": 0,
    }
    decisions: dict[tuple[str, str], tuple[datetime, bool, str]] = {}

    for queue in snapshot.queue_records:
        queue_diag: dict[str, Any] = {
            "selected_endpoint": None,
            "rows": 0,
            "matched_rows": 0,
            "field_names": [],
            "errors": [],
        }
        values: list[Any] = []
        for path in _function_paths(queue.number or queue.queue_id, start, now):
            try:
                values, _pages = await client._async_get_all_odata(path)  # noqa: SLF001
                queue_diag["selected_endpoint"] = path
                diagnostics["available"] = True
                break
            except ThreeCXApiError as err:
                queue_diag["errors"].append(f"{path}: {str(err)[:300]}")

        fields: set[str] = set()
        queue_diag["rows"] = len(values)
        for row in values:
            if not isinstance(row, dict):
                continue
            flat = _flatten(row)
            fields.update(flat)
            state, timestamp, source_field = _event_from_row(flat)
            if state is None:
                diagnostics["unmatched_rows"] += 1
                continue

            agent_id = _resolve_alias(_candidate_values(flat, "agent"), extension_aliases)
            queue_id = _resolve_alias(_candidate_values(flat, "queue"), queue_aliases) or queue.queue_id
            if agent_id is None or queue_id != queue.queue_id:
                diagnostics["unmatched_rows"] += 1
                continue

            effective_time = timestamp or start
            key = (queue_id, agent_id)
            previous = decisions.get(key)
            if previous is None or effective_time >= previous[0]:
                decisions[key] = (effective_time, state, source_field or "unknown")
            queue_diag["matched_rows"] += 1
            diagnostics["matched_rows"] += 1

        queue_diag["field_names"] = sorted(fields)
        queue_diag["errors"] = queue_diag["errors"][-8:]
        diagnostics["queues"][queue.display_name] = queue_diag

    logged_by_queue: dict[str, set[str]] = {
        queue.queue_id: set(queue.logged_in_members) for queue in snapshot.queue_records
    }
    extension_queue_names: dict[str, set[str]] = {
        extension.extension_id: set(extension.queue_logged_in_names)
        for extension in snapshot.extension_records
    }
    queues_by_id = {queue.queue_id: queue for queue in snapshot.queue_records}
    extensions_by_id = {extension.extension_id: extension for extension in snapshot.extension_records}

    for (queue_id, extension_id), (timestamp, state, source_field) in decisions.items():
        queue = queues_by_id.get(queue_id)
        extension = extensions_by_id.get(extension_id)
        if queue is None or extension is None:
            continue
        identity = extension.number or extension.extension_id
        if state:
            logged_by_queue[queue_id].add(identity)
            extension_queue_names[extension_id].add(queue.display_name)
        else:
            logged_by_queue[queue_id].discard(identity)
            extension_queue_names[extension_id].discard(queue.display_name)
        diagnostics["decisions"][f"{queue.number}:{extension.number}"] = {
            "logged_in": state,
            "at": timestamp.isoformat(),
            "source_field": source_field,
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
        "3CX AgentLoginHistory: available=%s matched=%s decisions=%s",
        diagnostics["available"],
        diagnostics["matched_rows"],
        len(diagnostics["decisions"]),
    )
    return snapshot, diagnostics
