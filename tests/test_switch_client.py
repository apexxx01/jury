"""The protocol client against a fake Switch server (httpx MockTransport)."""

import asyncio
import json

import httpx
import pytest

from jury import switch
from jury.switch import Credentials, SwitchAgent, SwitchError

AGENT = "a1"


def sse(*frames: tuple[str, dict, int | None]) -> bytes:
    out = [": keepalive\n\n"]
    for kind, data, seq in frames:
        lines = []
        if seq is not None:
            lines.append(f"id: {seq}")
        lines.append(f"event: {kind}")
        lines.append(f"data: {json.dumps(data)}")
        out.append("\n".join(lines) + "\n\n")
    return "".join(out).encode()


class FakeSwitch:
    def __init__(self, stream_body: bytes) -> None:
        self.stream_body = stream_body
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.opens: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}") if request.method == "POST" else {}
        self.calls.append((request.method, path, body, dict(request.headers)))
        if path.endswith("/events"):
            self.opens.append(request)
            if request.headers.get("accept") != "text/event-stream":
                return httpx.Response(200, json={"events": []})
            return httpx.Response(200, content=self.stream_body, headers={"content-type": "text/event-stream"})
        if path.endswith("/connection/beat"):
            return httpx.Response(200, json={"ok": True})
        if path.endswith("/ops/connect_to_room"):
            return httpx.Response(200, json={"result": {"room_id": body["room_id"]}})
        if path.endswith("/ops/post_message"):
            return httpx.Response(200, json={"result": {"event_id": "ev1"}})
        if path.endswith("/ops/read_context"):
            return httpx.Response(
                200,
                json={
                    "result": {
                        "threads": [
                            {
                                "root": {"id": "m1", "kind": "message", "sender_name": "jury-clerk", "body": "@a hello", "timestamp": 1000},
                                "replies": [],
                            }
                        ]
                    }
                },
            )
        if path.endswith("/ops/nope"):
            return httpx.Response(404, json={"detail": "Unknown operation: nope"})
        if path == "/agents":
            if body["name"] == "taken":
                return httpx.Response(409, json={"detail": "exists"})
            return httpx.Response(200, json={"id": "new-id", "api_key": "k"})
        return httpx.Response(404, json={"detail": "not found"})


def make_agent(fake: FakeSwitch, name: str = "a") -> SwitchAgent:
    agent = SwitchAgent("http://switch.test", Credentials(name=name, agent_id=AGENT, api_key="key", connection_id="conn-1"))
    agent.http = httpx.AsyncClient(transport=httpx.MockTransport(fake), headers={"Authorization": "Bearer key"})
    return agent


async def run_until(agent: SwitchAgent, handler, stop: asyncio.Event, timeout: float = 3.0) -> None:
    task = asyncio.create_task(agent.run(handler))
    try:
        await asyncio.wait_for(stop.wait(), timeout)
    finally:
        agent._stopping = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_stream_dispatches_domain_events_and_tracks_state():
    room = "r1"
    body = sse(
        ("connection_state", {"rooms": [room], "heartbeat_interval_seconds": 10, "protocol": 9}, None),
        ("message", {"type": "message", "room_id": room, "payload": {"message_id": "x", "body": "hi"}}, 41),
        ("gap", {"reason": "restart"}, None),
        ("message", {"type": "message", "room_id": room, "payload": {"message_id": "y", "body": "there"}}, 42),
    )
    fake = FakeSwitch(body)
    agent = make_agent(fake)
    got, stop = [], asyncio.Event()

    async def handler(ev):
        got.append((ev.kind, ev.payload.get("body"), ev.seq))
        if len(got) == 2:
            stop.set()

    await run_until(agent, handler, stop)
    assert got == [("message", "hi", 41), ("message", "there", 42)]
    assert agent.rooms == [room]
    assert agent._heartbeat_interval == 5.0  # announced 10, clamped so we never lapse
    open_req = fake.opens[0]
    assert open_req.headers["accept"] == "text/event-stream"
    assert open_req.url.params["connection_id"] == "conn-1"
    assert open_req.url.params["scope"] == "all"


async def test_reconnect_reuses_connection_id_and_resumes():
    fake = FakeSwitch(sse(("message", {"type": "message", "room_id": "r", "payload": {"message_id": "z"}}, 7)))
    agent = make_agent(fake)
    stop = asyncio.Event()

    async def handler(ev):
        if len(fake.opens) >= 2:
            stop.set()

    task = asyncio.create_task(agent.run(handler))
    for _ in range(60):
        if len(fake.opens) >= 2:
            break
        await asyncio.sleep(0.05)
    agent._stopping = True
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(fake.opens) >= 2, "client should reopen after the stream ends"
    first, second = fake.opens[0], fake.opens[1]
    assert first.url.params["connection_id"] == second.url.params["connection_id"] == "conn-1"
    assert second.headers.get("last-event-id") == "7"


async def test_room_op_claims_room_once_and_binds_header():
    fake = FakeSwitch(sse(("connection_state", {"rooms": []}, None)))
    agent = make_agent(fake)
    agent._stream_open.set()
    await agent.post("room-9", "one")
    await agent.post("room-9", "two")
    ops = [(p.rsplit("/", 1)[-1], b, h.get("x-switch-connection-id")) for m, p, b, h in fake.calls if "/ops/" in p]
    assert [o[0] for o in ops] == ["connect_to_room", "post_message", "post_message"]
    assert ops[0][1] == {"room_id": "room-9", "include_general_instructions": False}
    assert all(o[2] == "conn-1" for o in ops)


async def test_op_errors_carry_status_and_detail():
    agent = make_agent(FakeSwitch(b""))
    with pytest.raises(SwitchError) as e:
        await agent.op("nope")
    assert e.value.status == 404 and "Unknown operation" in e.value.detail


async def test_catch_up_replays_recent_history_as_events():
    agent = make_agent(FakeSwitch(b""))
    got = []

    async def handler(ev):
        got.append(ev)

    await agent._catch_up("r1", handler)
    assert len(got) == 1
    p = got[0].payload
    assert got[0].room_id == "r1" and p["message_id"] == "m1" and p["addressed"] is True and p["replayed"] is True


async def test_register_agent(monkeypatch):
    fake = FakeSwitch(b"")
    real = httpx.AsyncClient

    def patched(*a, **kw):
        kw["transport"] = httpx.MockTransport(fake)
        return real(*a, **kw)

    monkeypatch.setattr(switch.httpx, "AsyncClient", patched)
    creds = await switch.register_agent("http://switch.test", "reg", name="clerk", display_name="Clerk", description="d")
    assert (creds.agent_id, creds.api_key) == ("new-id", "k")
    method, path, body, headers = fake.calls[-1]
    assert headers["authorization"] == "Bearer reg"
    assert body["integration_profile"]["connection_model"] == "always_on"
    with pytest.raises(SwitchError) as e:
        await switch.register_agent("http://switch.test", "reg", name="taken", display_name="T", description="d")
    assert e.value.status == 409
