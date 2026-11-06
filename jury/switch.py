"""A small, dependency-light client for the Switch agent protocol.

Down: one server-sent event stream. Up: HTTP. That is the whole contract,
and this module implements the parts Jury needs:

* registration with a registration token (``POST /agents``)
* the event stream, with a client-chosen connection id reused across
  reconnects and ``Last-Event-ID`` resume
* the heartbeat, at the interval the server announces in ``connection_state``
* operations (``POST /agents/{id}/ops/{name}``), bound to a room by
  ``connect_to_room`` + the ``X-Switch-Connection-Id`` header

See https://docs.switchagents.ai (internals / agent protocol).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("jury.switch")

ALWAYS_ON_PROFILE = {
    "connection_model": "always_on",
    "message_exchange": True,
    "pre_invocation_mediation": [],
    "post_invocation_mediation": [],
    "event_reporting": [],
}

CLIENT_NAME = "jury"
CLIENT_VERSION = "1.0.0"


class SwitchError(RuntimeError):
    def __init__(self, status: int, detail: str, op: str = "") -> None:
        super().__init__(f"{op or 'request'} failed ({status}): {detail}")
        self.status = status
        self.detail = detail
        self.op = op


@dataclass
class Event:
    """One frame off the stream. ``kind`` is the SSE ``event:`` name."""

    kind: str
    data: dict[str, Any]
    seq: int | None = None

    @property
    def room_id(self) -> str | None:
        return self.data.get("room_id")

    @property
    def payload(self) -> dict[str, Any]:
        return self.data.get("payload") or {}


@dataclass
class Credentials:
    name: str
    agent_id: str
    api_key: str
    connection_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_json(self) -> dict[str, str]:
        return {
            "name": self.name,
            "agent_id": self.agent_id,
            "api_key": self.api_key,
            "connection_id": self.connection_id,
        }


async def health(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{base_url}/health")
            return r.status_code == 200
    except httpx.HTTPError:
        return False


async def register_agent(
    base_url: str,
    registration_token: str,
    *,
    name: str,
    display_name: str,
    description: str,
    icon_url: str | None = None,
    overwrite: bool = False,
) -> Credentials:
    """Register a custom always-on agent. The API key comes back once."""
    body: dict[str, Any] = {
        "name": name,
        "display_name": display_name,
        "description": description,
        "connector_type": "jury",
        "integration_profile": ALWAYS_ON_PROFILE,
        "metadata": {"client": CLIENT_NAME, "client_version": CLIENT_VERSION},
        "overwrite": overwrite,
    }
    if icon_url:
        body["icon_url"] = icon_url
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{base_url}/agents",
            headers={"Authorization": f"Bearer {registration_token}"},
            json=body,
        )
    if r.status_code != 200:
        raise SwitchError(r.status_code, _detail(r), f"register {name}")
    data = r.json()
    return Credentials(name=name, agent_id=data["id"], api_key=data["api_key"])


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json()
        return str(d.get("detail", d)) if isinstance(d, dict) else str(d)
    except ValueError:
        return r.text[:300]


Handler = Callable[[Event], Awaitable[None]]


class SwitchAgent:
    """One agent identity: its stream, its heartbeat and its operations."""

    def __init__(self, base_url: str, creds: Credentials) -> None:
        self.base_url = base_url.rstrip("/")
        self.creds = creds
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=10),
            headers={"Authorization": f"Bearer {creds.api_key}"},
        )
        self._room_lock = asyncio.Lock()
        self._current_room: str | None = None
        self._last_seq: int | None = None
        self._stream_open = asyncio.Event()
        self._heartbeat_interval = 2.0
        self._stopping = False
        self._bg: set[asyncio.Task[None]] = set()
        self.rooms: list[str] = []

    @property
    def name(self) -> str:
        return self.creds.name

    @property
    def agent_id(self) -> str:
        return self.creds.agent_id

    # ── operations ──────────────────────────────────────────────────────────

    async def op(self, name: str, args: dict[str, Any] | None = None, *, bind: bool = False) -> Any:
        headers = {}
        if bind:
            headers["X-Switch-Connection-Id"] = self.creds.connection_id
        r = await self.http.post(
            f"{self.base_url}/agents/{self.agent_id}/ops/{name}",
            headers=headers,
            json=args or {},
        )
        if r.status_code != 200:
            raise SwitchError(r.status_code, _detail(r), name)
        return r.json().get("result")

    async def room_op(self, room_id: str, name: str, args: dict[str, Any] | None = None) -> Any:
        """Run a room-bound operation in ``room_id``.

        Room-bound operations resolve the room from this connection's current
        claim, so claiming and calling must not interleave across rooms.
        """
        await self._stream_open.wait()
        async with self._room_lock:
            if self._current_room != room_id:
                await self.op(
                    "connect_to_room",
                    {"room_id": room_id, "include_general_instructions": False},
                    bind=True,
                )
                self._current_room = room_id
            try:
                return await self.op(name, args, bind=True)
            except SwitchError as e:
                if e.status == 409:  # connection went away under us; reclaim once
                    self._current_room = None
                    await self.op(
                        "connect_to_room",
                        {"room_id": room_id, "include_general_instructions": False},
                        bind=True,
                    )
                    self._current_room = room_id
                    return await self.op(name, args, bind=True)
                raise

    async def post(self, room_id: str, body: str, thread_id: str | None = None) -> str | None:
        args: dict[str, Any] = {"body": body}
        if thread_id:
            args["thread_id"] = thread_id
        try:
            res = await self.room_op(room_id, "post_message", args)
            return (res or {}).get("event_id") or (res or {}).get("message_id")
        except SwitchError as e:
            if thread_id:  # a thread can vanish (deleted root); fall back to top level
                log.warning("threaded post failed (%s); posting top-level", e)
                return await self.post(room_id, body)
            raise

    async def mention(self, room_id: str, names: list[str], body: str, thread_id: str | None = None) -> None:
        args: dict[str, Any] = {"body": body, "target_names": names}
        if thread_id:
            args["thread_id"] = thread_id
        await self.room_op(room_id, "send_targeted_message", args)

    async def post_direct(self, room_id: str, body: str) -> None:
        """Post without a connection (``POST /agents/{id}/message``)."""
        r = await self.http.post(
            f"{self.base_url}/agents/{self.agent_id}/message",
            json={"room_id": room_id, "content": body},
        )
        if r.status_code != 200:
            raise SwitchError(r.status_code, _detail(r), "message")

    # ── stream + heartbeat ──────────────────────────────────────────────────

    async def run(self, handler: Handler) -> None:
        """Stream forever, reconnecting with the same connection id."""
        beat = asyncio.create_task(self._heartbeat_loop(), name=f"{self.name}-beat")
        backoff = 1.0
        try:
            while not self._stopping:
                try:
                    await self._stream_once(handler)
                    backoff = 1.0
                except (httpx.HTTPError, SwitchError) as e:
                    log.warning("%s: stream dropped (%s); reconnecting in %.0fs", self.name, e, backoff)
                except asyncio.CancelledError:
                    raise
                self._stream_open.clear()
                self._current_room = None
                if self._stopping:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
        finally:
            beat.cancel()

    async def _stream_once(self, handler: Handler) -> None:
        params = {
            "connection_id": self.creds.connection_id,
            "scope": "all",
            "filter": "all",
            "start_from": "head",
            "client": CLIENT_NAME,
            "client_version": CLIENT_VERSION,
        }
        headers = {"Accept": "text/event-stream"}
        if self._last_seq is not None:
            headers["Last-Event-ID"] = str(self._last_seq)
        timeout = httpx.Timeout(connect=10, read=90, write=30, pool=30)
        async with self.http.stream(
            "GET",
            f"{self.base_url}/agents/{self.agent_id}/events",
            params=params,
            headers=headers,
            timeout=timeout,
        ) as r:
            if r.status_code != 200:
                body = (await r.aread()).decode(errors="replace")[:300]
                raise SwitchError(r.status_code, body, "open stream")
            kind, data_lines, seq = "message", [], None
            async for line in r.aiter_lines():
                if line == "":
                    if data_lines:
                        await self._dispatch(kind, "\n".join(data_lines), seq, handler)
                    kind, data_lines, seq = "message", [], None
                    continue
                if line.startswith(":"):
                    continue  # keepalive comment
                field_name, _, value = line.partition(":")
                value = value.removeprefix(" ")
                if field_name == "event":
                    kind = value
                elif field_name == "data":
                    data_lines.append(value)
                elif field_name == "id":
                    try:
                        seq = int(value)
                    except ValueError:
                        seq = None

    async def _dispatch(self, kind: str, raw: str, seq: int | None, handler: Handler) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.debug("unparseable frame %r", raw[:200])
            return
        if kind == "connection_state":
            self._heartbeat_interval = min(float(data.get("heartbeat_interval_seconds") or 2), 5.0)
            self.rooms = list(data.get("rooms") or [])
            self._stream_open.set()
            log.info("%s: connected (protocol %s)", self.name, data.get("protocol"))
            return
        if kind == "evicted":
            log.warning("%s: evicted (%s)", self.name, data.get("reason"))
            raise SwitchError(409, str(data.get("reason")), "stream")
        if kind == "gap":
            log.warning("%s: gap in stream (%s); history will be re-read on demand", self.name, data.get("reason"))
            return
        if kind in ("subscription_changed", "room_released"):
            self._current_room = None
            if kind == "subscription_changed":
                new = [r for r in (data.get("rooms") or []) if r not in self.rooms]
                self.rooms = list(data.get("rooms") or [])
                log.debug("%s: rooms now %s (new %s)", self.name, len(self.rooms), new)
                for room in new:
                    t = asyncio.create_task(self._catch_up(room, handler))
                    self._bg.add(t)
                    t.add_done_callback(self._bg.discard)
            return
        if seq is not None:
            self._last_seq = seq
        try:
            await handler(Event(kind=kind, data=data, seq=seq))
        except Exception:  # a bad event must never kill the stream
            log.exception("%s: handler failed on %s", self.name, kind)

    async def _catch_up(self, room_id: str, handler: Handler, window_ms: int = 120_000) -> None:
        """Replay what was said in a room just before we covered it.

        The protocol docs warn that a room covered after catch-up has its
        buffered events skipped. Re-reading the last couple of minutes when a
        room joins the stream closes that gap. Handlers dedupe by message id,
        so replaying something already delivered is harmless.
        """
        await asyncio.sleep(0.5)
        try:
            res = await self.op("read_context", {"room_id": room_id, "limit": 25})
        except SwitchError as e:
            log.debug("%s: catch-up read failed for %s: %s", self.name, room_id, e)
            return
        newest = 0
        items = []
        for t in (res or {}).get("threads", []):
            root = t.get("root") or {}
            for m in [root, *(t.get("replies") or [])]:
                if m.get("kind", "message") != "message":
                    continue
                newest = max(newest, int(m.get("timestamp") or 0))
                items.append((m, root.get("id") if m is not root else None))
        for m, thread in items:
            if newest - int(m.get("timestamp") or 0) > window_ms:
                continue
            body = m.get("body") or ""
            payload = {
                "addressed": f"@{self.name}" in body,
                "sender": m.get("sender"),
                "sender_name": m.get("sender_name"),
                "sender_kind": m.get("sender_kind"),
                "on_behalf_of": m.get("on_behalf_of"),
                "message_id": m.get("id"),
                "body": body,
                "timestamp": m.get("timestamp"),
                "thread_id": thread,
                "attachments": m.get("attachments") or [],
                "replayed": True,
            }
            try:
                await handler(Event(kind="message", data={"type": "message", "room_id": room_id, "payload": payload}))
            except Exception:
                log.exception("%s: handler failed on replayed message", self.name)

    async def _heartbeat_loop(self) -> None:
        while True:
            await self._stream_open.wait()
            try:
                r = await self.http.post(
                    f"{self.base_url}/agents/{self.agent_id}/connection/beat",
                    json={"connection_id": self.creds.connection_id, "cursor": self._last_seq or 0},
                    timeout=10,
                )
                if r.status_code in (404, 409):
                    log.info("%s: heartbeat says reopen (%s)", self.name, r.status_code)
            except httpx.HTTPError as e:
                log.debug("%s: heartbeat error %s", self.name, e)
            await asyncio.sleep(self._heartbeat_interval)

    async def close(self) -> None:
        self._stopping = True
        await self.http.aclose()
