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


def scored(clerk, n, verdict_score=None):
    case = add(clerk, n, status="decided" if verdict_score is not None else "scored")
    case.scorecards = {
        "jury-auditor": {"scores": {"use_of_switch": 10, "technical_execution": 9, "presentation": 7}, "summary": "facts ok"},
        "jury-inspector": {"scores": {"technical_execution": 7, "creativity_impact": 7}, "summary": "38 tests pass"},
        "jury-critic": {"scores": {"use_of_switch": 9, "creativity_impact": 7, "presentation": 6}, "notes": {"presentation": "no video"}},
    }
    case.provisional, case.complete = scoring.provisional_score(case.scorecards, clerk.rubric)
    if verdict_score is not None:
        case.verdict = {"decision": "shortlist", "score": verdict_score, "judge": "asha", "note": "strong"}
    return case


def test_explain_shows_the_arithmetic(clerk):
    case = scored(clerk, 1, 90)
    text = clerk.explain_md(case)
    assert "mean of jury-auditor 10, jury-critic 9 = **9.5**/10 → 28.5 pts" in text
    assert "Panel total: **79/100**" in text
    assert "score set to 90" in text


def test_calibration_compares_each_judge_with_humans(clerk):
    scored(clerk, 1, 70)
    scored(clerk, 2, 70)
    text = clerk.calibration_md()
    # auditor alone: (30*1 + 25*.9 + 20*.7)/75*100 = 88.7 -> +18.7 vs 70
    assert "**jury-auditor**: +18.7 pts" in text and "too generous" in text
    assert text.index("jury-auditor") < text.index("jury-inspector")


def test_calibration_needs_scored_verdicts(clerk):
    scored(clerk, 1)
    assert "No calibration yet" in clerk.calibration_md()


async def test_feedback_goes_to_the_intake_thread_when_enabled(clerk):
    clerk.cfg.share_feedback = True
    case = scored(clerk, 3)
    case.red_flags = ["No demo video link in the README."]
    await clerk._record_verdict(case, "asha", {"decision": "shortlist", "score": 88, "note": "great"}, "v1")
    fb = [b for r, b in clerk.agent.posts if r == "R-in"]
    assert fb and "Feedback on Case #003" in fb[0] and "38 tests pass" in fb[0] and "Worth fixing" in fb[0] and "appeal" in fb[0]
    # once only
    await clerk._record_verdict(case, "asha", {"decision": "shortlist", "score": 89, "note": ""}, "v2")
    assert len([b for r, b in clerk.agent.posts if r == "R-in"]) == 1


async def test_closed_intake_refuses_new_cases(clerk):
    await clerk._command("R-floor", {"message_id": "c1"}, "@jury-clerk close intake", None)
    assert clerk.state.intake_closed
    assert await clerk.open_case("new", "repo", {"message_id": "m9", "sender_name": "p"}) is None
    assert any("Intake is closed" in b for r, b in clerk.agent.posts if r == "R-in")
    await clerk._command("R-floor", {"message_id": "c2"}, "@jury-clerk open intake", None)
    assert not clerk.state.intake_closed


def test_overdue_cases_are_grouped_by_judge(clerk):
    a = add(clerk, 1, status="scored", assignee="asha")
    a.created_at = time.time() - 3600
    a.extra["first_pass_s"] = 60
    b = add(clerk, 2, status="scored", assignee="ben")
    b.created_at = time.time() - 120
    b.extra["first_pass_s"] = 60  # ready a minute ago: not overdue yet
    done = add(clerk, 3, status="decided", assignee="asha")
    done.created_at = time.time() - 9999
    done.extra["first_pass_s"] = 1
    over = clerk.overdue(30)
    assert [c.number for c in over] == [1]
    text = clerk.reminder_md(over)
    assert "@asha" in text and "#001" in text and "ben" not in text
    assert clerk.reminder_md([]) is None


def test_results_markdown(clerk):
    from jury.cli import results_markdown

    scored(clerk, 1, 90)
    md = results_markdown(clerk.state, clerk.rubric)
    assert md.startswith("# Results · Flip the Switch 2026")
    assert "| 1 | [o/r1](https://github.com/o/r1) | SHORTLIST | 90 |" in md
    assert "> strong (asha)" in md and "**jury-inspector**: 38 tests pass" in md


async def test_reopen_sets_the_verdict_aside_and_keeps_it(clerk):
    c = add(clerk, 4, status="decided", assignee="asha")
    for j in ("jury-auditor", "jury-inspector", "jury-critic"):
        c.scorecards[j] = {"scores": {"presentation": 7}}
    c.verdict = {"decision": "shortlist", "score": 88, "judge": "asha"}
    await clerk._command("R-floor", {"sender_name": "asha", "message_id": "x"}, "@jury-clerk reopen 4", None)
    assert c.verdict is None and c.status == "scored"
    assert c.extra["reopened"][0]["previous"]["score"] == 88
    assert any("reopened by asha" in b for _, b in clerk.agent.posts)
    # nothing to set aside the second time
    await clerk._command("R4", {"sender_name": "asha"}, "@jury-clerk reopen", c)
    assert "no verdict to set aside" in clerk.agent.posts[-1][1]
    # an agent can't do it
    c.verdict = {"decision": "pass"}
    await clerk._command("R4", {"sender_name": "jury-critic"}, "@jury-clerk reopen", c)
    assert c.verdict == {"decision": "pass"}


async def test_reopen_intake_is_still_intake(clerk):
    clerk.state.intake_closed = True
    await clerk._command("R-floor", {"sender_name": "asha"}, "@jury-clerk reopen intake", None)
    assert clerk.state.intake_closed is False


async def test_withdraw_takes_a_case_off_the_floor_and_frees_the_repo(clerk):
    add(clerk, 1)
    add(clerk, 2)
    forgotten = []
    clerk.on_withdraw.append(forgotten.append)
    await clerk._command("R-floor", {"sender_name": "asha", "message_id": "x"}, "@jury-clerk withdraw 2 duplicate entry", None)
    assert "o/r2" not in clerk.state.cases and forgotten == ["o/r2"]
    (w,) = clerk.state.withdrawn
    assert w["number"] == 2 and w["by"] == "asha" and w["reason"] == "duplicate entry"
    assert any("withdrawn by asha" in b for _, b in clerk.agent.posts)
    # numbers are never reused, so a refiled repo gets a fresh room name
    assert clerk.state.next_number() == 3
    clerk.state.save()
    from jury.state import State

    assert State.load(clerk.state.path).withdrawn[0]["repo"] == "r2"


async def test_agents_cannot_withdraw(clerk):
    add(clerk, 1)
    await clerk._command("R-floor", {"sender_name": "jury-critic", "message_id": "x"}, "@jury-clerk withdraw 1", None)
    assert "o/r1" in clerk.state.cases
