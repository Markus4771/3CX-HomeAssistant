#!/usr/bin/env python3
"""Passive 3CX web-client and realtime protocol explorer.

Version 0.1.0 performs read-only discovery. It never sends queue login/logout
commands and never writes configuration to the 3CX system.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp

VERSION = "0.1.0"
SENSITIVE_KEYS = {"access_token", "token", "authorization", "client_secret", "password", "secret"}
PATTERNS = {
    "websocket_urls": re.compile(r"(?:wss?|https?)://[^\"'\\\s)]+", re.IGNORECASE),
    "websocket_paths": re.compile(r"[\"'](/[^\"']*(?:ws|websocket|callcontrol|signalr|hub|event)[^\"']*)[\"']", re.IGNORECASE),
    "protocol_terms": re.compile(r"\b(?:WebSocket|SignalR|HubConnection|subscribe|subscription|heartbeat|keepalive|callcontrol|queue|agent)\b", re.IGNORECASE),
}
DEFAULT_WS_PATHS = (
    "/callcontrol/ws",
    "/callcontrol",
    "/ws/callcontrol",
    "/signalr",
    "/signalr/connect",
    "/hubs/callcontrol",
    "/hub/callcontrol",
)


class ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        values = dict(attrs)
        src = values.get("src")
        if src:
            self.scripts.append(src)


@dataclass(slots=True)
class HttpProbe:
    url: str
    status: int | None
    content_type: str | None
    bytes_read: int
    error: str | None


@dataclass(slots=True)
class WebSocketProbe:
    path: str
    connected: bool
    response_status: int | None
    frames_received: int
    first_frame_type: str | None
    first_frame_preview: str | None
    error: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value: Any, key: str = "") -> Any:
    if key.lower() in SENSITIVE_KEYS:
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(access_token=)[^&\s]+", r"\1<redacted>", value)
        value = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~-]+", r"\1<redacted>", value)
    return value


def websocket_url(base_url: str, path: str) -> str:
    http_url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(parsed._replace(scheme=scheme))


async def read_text(session: aiohttp.ClientSession, url: str, limit: int = 2_000_000) -> tuple[str, HttpProbe]:
    try:
        async with session.get(url, allow_redirects=True) as response:
            data = await response.content.read(limit)
            charset = response.charset or "utf-8"
            text = data.decode(charset, errors="replace")
            return text, HttpProbe(
                url=str(response.url),
                status=response.status,
                content_type=response.headers.get("Content-Type"),
                bytes_read=len(data),
                error=None,
            )
    except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError) as err:
        return "", HttpProbe(url=url, status=None, content_type=None, bytes_read=0, error=str(err)[:500])


async def authenticate(
    session: aiohttp.ClientSession,
    base_url: str,
    client_id: str,
    client_secret: str,
) -> tuple[str | None, dict[str, Any]]:
    token_url = urljoin(base_url.rstrip("/") + "/", "connect/token")
    payloads = (
        {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret, "scope": "pbx"},
    )
    attempts: list[dict[str, Any]] = []
    for form in payloads:
        try:
            async with session.post(token_url, data=form) as response:
                body = await response.text()
                attempts.append({"status": response.status, "body_preview": body[:300]})
                if response.status >= 400:
                    continue
                decoded = json.loads(body)
                token = decoded.get("access_token")
                if isinstance(token, str) and token:
                    return token, {"success": True, "token_url": token_url, "attempts": attempts}
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as err:
            attempts.append({"status": None, "error": str(err)[:500]})
    return None, {"success": False, "token_url": token_url, "attempts": attempts}


def inspect_javascript(text: str) -> dict[str, Any]:
    urls = sorted(set(PATTERNS["websocket_urls"].findall(text)))[:200]
    paths = sorted(set(PATTERNS["websocket_paths"].findall(text)))[:200]
    terms: dict[str, int] = {}
    for match in PATTERNS["protocol_terms"].finditer(text):
        term = match.group(0).lower()
        terms[term] = terms.get(term, 0) + 1
    return {
        "websocket_urls": urls,
        "websocket_paths": paths,
        "protocol_terms": dict(sorted(terms.items())),
    }


async def probe_websocket(
    session: aiohttp.ClientSession,
    base_url: str,
    path: str,
    token: str | None,
    wait_seconds: float,
) -> WebSocketProbe:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = websocket_url(base_url, path)
    try:
        async with session.ws_connect(
            url,
            headers=headers,
            heartbeat=20,
            receive_timeout=wait_seconds,
            max_msg_size=4 * 1024 * 1024,
        ) as websocket:
            frames = 0
            first_type: str | None = None
            first_preview: str | None = None
            deadline = asyncio.get_running_loop().time() + wait_seconds
            while asyncio.get_running_loop().time() < deadline:
                remaining = max(0.1, deadline - asyncio.get_running_loop().time())
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if message.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    frames += 1
                    first_type = first_type or message.type.name
                    if first_preview is None:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            first_preview = str(message.data)[:500]
                        else:
                            first_preview = bytes(message.data)[:200].hex()
                elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
            return WebSocketProbe(path, True, 101, frames, first_type, first_preview, None)
    except aiohttp.WSServerHandshakeError as err:
        return WebSocketProbe(path, False, err.status, 0, None, None, str(err)[:500])
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        return WebSocketProbe(path, False, None, 0, None, None, str(err)[:500])


async def run(args: argparse.Namespace) -> dict[str, Any]:
    ssl_context: ssl.SSLContext | bool
    if args.no_verify_ssl:
        ssl_context = False
    else:
        ssl_context = ssl.create_default_context()

    timeout = aiohttp.ClientTimeout(total=args.timeout)
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    headers = {"User-Agent": f"3CX-Protocol-Explorer/{VERSION}"}
    async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
        token: str | None = None
        authentication: dict[str, Any] = {"attempted": False}
        if args.client_id and args.client_secret:
            token, authentication = await authenticate(session, args.base_url, args.client_id, args.client_secret)
            authentication["attempted"] = True
            if token:
                session.headers.update({"Authorization": f"Bearer {token}"})

        html, root_probe = await read_text(session, args.base_url)
        parser = ScriptParser()
        parser.feed(html)
        script_urls = [urljoin(root_probe.url or args.base_url, src) for src in parser.scripts]

        scripts: list[dict[str, Any]] = []
        discovered_paths: set[str] = set(DEFAULT_WS_PATHS)
        for script_url in script_urls[: args.max_scripts]:
            text, probe = await read_text(session, script_url, args.max_script_bytes)
            findings = inspect_javascript(text)
            discovered_paths.update(findings["websocket_paths"])
            scripts.append({"probe": asdict(probe), "findings": findings})

        websocket_probes: list[WebSocketProbe] = []
        for path in sorted(discovered_paths)[: args.max_websocket_paths]:
            websocket_probes.append(
                await probe_websocket(session, args.base_url, path, token, args.websocket_wait)
            )

    report = {
        "tool": "3CX Protocol Explorer",
        "version": VERSION,
        "generated_at": utc_now(),
        "target": args.base_url,
        "verify_ssl": not args.no_verify_ssl,
        "authentication": authentication,
        "root": asdict(root_probe),
        "script_count_discovered": len(script_urls),
        "scripts": scripts,
        "websocket_paths_tested": len(websocket_probes),
        "websocket_probes": [asdict(item) for item in websocket_probes],
        "summary": {
            "successful_http_root": root_probe.status is not None and root_probe.status < 400,
            "successful_websocket_upgrades": sum(1 for item in websocket_probes if item.connected),
            "websockets_with_frames": sum(1 for item in websocket_probes if item.frames_received > 0),
        },
    }
    return redact(report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Passive 3CX web-client protocol explorer")
    parser.add_argument("--base-url", required=True, help="3CX URL, e.g. https://pbx.example.de")
    parser.add_argument("--client-id", default="", help="Optional 3CX API client ID")
    parser.add_argument("--client-secret", default="", help="Optional 3CX API client secret")
    parser.add_argument("--output", default="3cx_protocol_report.json")
    parser.add_argument("--no-verify-ssl", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--websocket-wait", type=float, default=5.0)
    parser.add_argument("--max-scripts", type=int, default=50)
    parser.add_argument("--max-script-bytes", type=int, default=2_000_000)
    parser.add_argument("--max-websocket-paths", type=int, default=50)
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    output = Path(args.output)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report written: {output.resolve()}")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
