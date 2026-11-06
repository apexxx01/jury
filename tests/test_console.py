"""The web console's data: the live snapshot and the recorded demo floor."""

import asyncio
import json
import time
from pathlib import Path

from jury import scoring
from jury.cli import DEMO_FILE, demo_snapshot, snapshot
from jury.config import Config
from jury.state import Case, State
from jury.web import serve

ROOT = Path(__file__).resolve().parent.parent
RUBRIC = scoring.load_rubric(ROOT / "rubrics" / "flip-the-switch.yaml")


def _cfg(tmp_path):
    return Config(
        switch_url="http://x",
        registration_token=None,
        source="t",
        state_dir=tmp_path,
        rubric_path=ROOT / "rubrics" / "flip-the-switch.yaml",
    )


def test_snapshot_carries_everything_the_console_shows(tmp_path):
    st = State(path=tmp_path / "s.json")
    c = Case(1, "o", "r", "u", "m1", room_id="r1", room_name="case-001-r")
    c.scorecards = {
        "jury-auditor": {"scores": {"technical_execution": 9}, "at": time.time()},
        "jury-inspector": {"scores": {"technical_execution": 3}, "at": time.time()},
    }
    c.extra = {
        "judges": ["jury-auditor", "jury-inspector"],
        "assignee": "maya",
        "first_pass_s": 42,
        "appeals": [{"by": "o", "text": "look again", "at": 1.0, "previous": {"decision": "pass"}}],
    }
    st.cases[c.key] = c
    snap = snapshot(st, RUBRIC, _cfg(tmp_path), ["jury-inspector"])
    d = snap["cases"][0]
    (fight,) = d["disagreements"]
    assert fight["criterion"] == "technical_execution" and {fight["sa"], fight["sb"]} == {9, 3}
    assert d["appeal_log"][0]["previous"] == "pass" and d["appeals"] == 1
    assert "intake_message_id" not in d and d["assignee"] == "maya"
    assert snap["settings"]["window_end"].startswith("2026-11-07")
    assert snap["bench"]["present"] == ["jury-inspector"]
    assert snap["rubric"]["shortlist_at"] == RUBRIC.shortlist_at
    json.dumps(snap)  # must serialise


def test_demo_floor_is_fresh_and_honest():
    raw = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    snap = demo_snapshot()
    assert snap["demo"] is True and abs(snap["generated_at"] - time.time()) < 5
    # times are shifted together, so ages are preserved
    lag = raw["generated_at"] - raw["cases"][0]["created_at"]
    assert abs((snap["generated_at"] - snap["cases"][0]["created_at"]) - lag) < 5
    # only the real case links anywhere; sample teams are marked as samples
    for c in snap["cases"]:
        assert (c["url"] is not None) == (not c.get("sample"))
    assert any(not c.get("sample") for c in snap["cases"])
    stages = {c["status"] for c in snap["cases"]}
    assert stages == {"open", "scored", "decided"}


def test_console_serves_page_and_state(tmp_path):
    async def go():
        server = await serve("127.0.0.1", 0, lambda: {"cases": [], "ok": 1})
        port = server.sockets[0].getsockname()[1]

        async def get(path):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            await w.drain()
            data = await r.read()
            w.close()
            return data

        page = await get("/")
        state = await get("/api/state")
        missing = await get("/nope")
        server.close()
        return page, state, missing

    page, state, missing = asyncio.run(go())
    assert b"200 OK" in page and b"Jury Console" in page
    assert b'"ok": 1' in state
    assert b"404" in missing
