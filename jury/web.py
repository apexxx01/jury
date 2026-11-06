"""A tiny read-only web view of the leaderboard (stdlib only)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("jury.web")
STATIC = Path(__file__).parent / "static"


async def serve(host: str, port: int, snapshot: Callable[[], dict[str, Any]]) -> asyncio.AbstractServer:
    page = (STATIC / "index.html").read_bytes()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            parts = line.decode(errors="replace").split()
            path = parts[1] if len(parts) > 1 else "/"
            while await asyncio.wait_for(reader.readline(), 10) not in (b"\r\n", b"\n", b""):
                pass
            if path.startswith("/api/state"):
                body = json.dumps(snapshot()).encode()
                ctype = "application/json"
            elif path in ("/", "/index.html"):
                body, ctype = page, "text/html; charset=utf-8"
            elif path == "/health":
                body, ctype = b'{"ok":true}', "application/json"
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            writer.write(
                f"HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                "Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, host, port)
    log.info("leaderboard on http://%s:%d", host, port)
    return server
