"""Clerk's workflow logic against a fake Switch agent (no server)."""

import time
from pathlib import Path

import pytest

from jury import scoring
from jury.clerk import Clerk
from jury.config import Config
from jury.state import Case, State

ROOT = Path(__file__).resolve().parent.parent


class FakeAgent:
    name = "jury-clerk"

    def __init__(self):
        self.posts: list[tuple[str, str]] = []
        self.mentions: list[tuple[str, list[str], str]] = []

    async def post(self, room, body, thread=None):
        self.posts.append((room, body))

    async def mention(self, room, names, body, thread=None):
        self.mentions.append((room, names, body))

    async def op(self, name, args=None, bind=False):
        if name == "list_agents":
            return [{"name": n} for n in ("jury-auditor", "jury-inspector", "jury-critic")]
        return {}

    async def room_op(self, room, name, args=None):
        return {"id": "doc1"}


@pytest.fixture
def clerk(tmp_path):
    cfg = Config(
        switch_url="http://x",
        registration_token="t",
        source="test",
        state_dir=tmp_path,
        rubric_path=ROOT / "rubrics" / "flip-the-switch.yaml",
        judge_users=["asha", "ben"],
    )
    state = State(path=tmp_path / "state.json")
    state.rooms = {"intake": "R-in", "floor": "R-floor"}
    c = Clerk(cfg, state, scoring.load_rubric(cfg.rubric_path), FakeAgent(), "jury-auditor")
    c.doc_debounce = 0
    return c


def add(clerk, n, status="open", assignee=None, **kw):
    case = Case(
        number=n,
        owner="o",
        repo=f"r{n}",
        submitter="p",
        intake_message_id=f"m{n}",
        room_id=f"R{n}",
        room_name=f"case-{n:03d}",
        status=status,
        **kw,
    )
    if assignee:
        case.extra["assignee"] = assignee
    case.extra["judges"] = ["jury-auditor", "jury-inspector", "jury-critic"]
    clerk.state.cases[case.key] = case
    return case


def test_cases_are_dealt_to_the_least_loaded_judge(clerk):
    assert clerk._next_assignee() == "asha"
    add(clerk, 1, assignee="asha")
    assert clerk._next_assignee() == "ben"
    add(clerk, 2, assignee="ben")
    add(clerk, 3, assignee="ben")
    assert clerk._next_assignee() == "asha"
    # decided cases don't count as load
    clerk.state.by_number(1).status = "decided"
    assert clerk._next_assignee() == "asha"


def test_queue_lists_ready_cases_first(clerk):
    add(clerk, 1, status="open", assignee="asha")
    add(clerk, 2, status="scored", assignee="asha")
    add(clerk, 3, status="decided", assignee="asha")
    add(clerk, 4, status="scored", assignee="ben")
    q = clerk.queue_md("asha")
    assert "(2)" in q and q.index("#002") < q.index("#001") and "#003" not in q
    assert "nothing waiting" in clerk.queue_md("zed")


async def test_appeal_reopens_and_pings_the_assignee(clerk):
    case = add(clerk, 5, status="decided", assignee="ben", verdict={"decision": "pass", "score": 20, "judge": "asha"})
    await clerk._on_intake(
        {"thread_id": "m5", "message_id": "x1", "sender_name": "team-lead", "body": ""}, "appeal: the video link was fixed"
    )
    assert case.status == "scored" and case.verdict is None
    assert case.extra["appeals"][0]["previous"]["decision"] == "pass"
    room_posts = [b for r, b in clerk.agent.posts if r == "R5"]
    assert room_posts and "@ben" in room_posts[0] and "set aside" in room_posts[0]
    assert any("Appeal" in b for r, b in clerk.agent.posts if r == "R-in")


async def test_ready_for_verdict_records_first_pass_and_pings_assignee(clerk):
    case = add(clerk, 6, assignee="asha")
    case.created_at = time.time() - 125
    for judge, scores in (
        ("jury-auditor", {"use_of_switch": 8, "technical_execution": 8, "presentation": 7}),
        ("jury-inspector", {"technical_execution": 7, "creativity_impact": 7}),
        ("jury-critic", {"use_of_switch": 8, "creativity_impact": 8, "presentation": 7}),
    ):
        await clerk._record_scorecard(case, judge, {"scores": scores, "red_flags": []}, f"msg-{judge}")
    assert case.status == "scored" and 120 <= case.extra["first_pass_s"] <= 130
    ask = [b for r, b in clerk.agent.posts if r == "R6" and "All scorecards are in" in b]
    assert ask and ask[0].startswith("@asha")
    assert "median **2m" in clerk.stats_md()


async def test_commands(clerk):
    add(clerk, 7)
    await clerk._command("R-floor", {"message_id": "c1", "sender_name": "asha"}, "@jury-clerk assign 7 to @ben", None)
    assert clerk.state.by_number(7).extra["assignee"] == "ben"
    await clerk._command("R-floor", {"message_id": "c2", "sender_name": "ben"}, "@jury-clerk my queue", None)
    assert "@ben's queue" in clerk.agent.posts[-1][1]
    await clerk._command("R-floor", {"message_id": "c3", "sender_name": "ben"}, "@jury-clerk stats", None)
    assert "Jury stats" in clerk.agent.posts[-1][1]
