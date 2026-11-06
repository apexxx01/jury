from pathlib import Path

import pytest

from jury import scoring
from jury.state import Case, State, find_repos, slug

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def rubric():
    return scoring.load_rubric(ROOT / "rubrics" / "flip-the-switch.yaml")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://github.com/sandbox-quantum/switch", [("sandbox-quantum", "switch")]),
        ("**Case is open: https://github.com/pallets/click** (filed)", [("pallets", "click")]),
        ("<https://github.com/a/b.git>.", [("a", "b")]),
        ("see github.com/foo/bar-baz/tree/main/src and https://github.com/foo/bar-baz", [("foo", "bar-baz")]),
        ("two: https://github.com/a/one, https://github.com/b/two.", [("a", "one"), ("b", "two")]),
        ("https://github.com/orgs/whatever", []),
        ("no links here", []),
        ("[repo](https://github.com/x/y.js)", [("x", "y.js")]),
    ],
)
def test_find_repos(text, expected):
    assert find_repos(text) == expected


def test_rubric_weights_add_up(rubric):
    assert sum(c.weight for c in rubric.criteria) == 100
    assert rubric.verdicts == ["shortlist", "maybe", "pass"]
    assert {c.id for c in rubric.criteria_for("jury-auditor")} == {"use_of_switch", "technical_execution", "presentation"}


def test_take_home_rubric_loads():
    r = scoring.load_rubric(ROOT / "rubrics" / "take-home.yaml")
    assert r.verdicts[0] == "advance"


def test_parse_scorecard_tagged(rubric):
    body = 'Looks good.\n\n```jury\n{"scores": {"use_of_switch": 8, "Presentation": "7/10", "nope": 3}, "summary": "ok", "red_flags": "none committed"}\n```'
    card = scoring.parse_scorecard(body, rubric)
    assert card["scores"] == {"use_of_switch": 8.0, "presentation": 7.0}
    assert card["summary"] == "ok"
    assert card["red_flags"] == ["none committed"]


def test_parse_scorecard_untagged_json_and_trailing_comma(rubric):
    body = '```json\n{"scores": {"technical_execution": 11, "creativity_impact": "85/100",},}\n```'
    card = scoring.parse_scorecard(body, rubric)
    assert card["scores"] == {"technical_execution": 10.0, "creativity_impact": 8.5}


def test_parse_scorecard_last_block_wins(rubric):
    body = '```jury\n{"scores": {"use_of_switch": 2}}\n```\nrevised:\n```jury\n{"scores": {"use_of_switch": 9}}\n```'
    assert scoring.parse_scorecard(body, rubric)["scores"] == {"use_of_switch": 9.0}


def test_parse_scorecard_ignores_plain_code(rubric):
    assert scoring.parse_scorecard("```bash\nnpm install\n```", rubric) is None
    assert scoring.parse_scorecard('```json\n{"name": "x"}\n```', rubric) is None


def test_provisional_score_renormalises(rubric):
    cards = {"a": {"scores": {"use_of_switch": 10, "technical_execution": 8, "presentation": 10}}}
    score, complete = scoring.provisional_score(cards, rubric)
    assert not complete
    # (30*1 + 25*.8 + 20*1) / 75 * 100
    assert score == pytest.approx(93.3, abs=0.05)
    cards["b"] = {"scores": {"creativity_impact": 6, "use_of_switch": 8}}
    score, complete = scoring.provisional_score(cards, rubric)
    assert complete
    assert score == pytest.approx(30 * 0.9 + 25 * 0.8 + 25 * 0.6 + 20 * 1.0)


def test_disagreements(rubric):
    cards = {"jury-auditor": {"scores": {"technical_execution": 8}}, "jury-inspector": {"scores": {"technical_execution": 3}}}
    assert scoring.disagreements(cards, rubric, 4) == [("technical_execution", "jury-inspector", 3, "jury-auditor", 8)]
    assert scoring.disagreements(cards, rubric, 6) == []


@pytest.mark.parametrize(
    "body,decision,score,note",
    [
        ("@jury-clerk verdict shortlist 88 great use of rooms", "shortlist", 88, "great use of rooms"),
        ("@jury-clerk verdict: pass — no Switch at all", "pass", None, "no Switch at all"),
        ("verdict maybe 64/100, needs a video", "maybe", 64, "needs a video"),
        ("@jury-clerk verdict yes", "shortlist", None, ""),
        ("@jury-clerk verdict reject 10", "pass", 10, ""),
    ],
)
def test_parse_verdict(rubric, body, decision, score, note):
    v = scoring.parse_verdict(body, rubric)
    assert v["decision"] == decision
    assert v["score"] == score
    assert v["note"] == note


def test_parse_verdict_rejects_unknown(rubric):
    assert scoring.parse_verdict("@jury-clerk verdict banana", rubric) is None
    assert scoring.parse_verdict("no verdict word", rubric) is None


def test_state_roundtrip_and_ranking(tmp_path):
    st = State(path=tmp_path / "s.json")
    a = Case(number=1, owner="a", repo="x", submitter="p", intake_message_id="m1", provisional=50, complete=True)
    b = Case(number=2, owner="b", repo="y", submitter="q", intake_message_id="m2", provisional=70, complete=True)
    partial = Case(number=4, owner="d", repo="w", submitter="s", intake_message_id="m4", provisional=99)
    c = Case(
        number=3, owner="c", repo="z", submitter="r", intake_message_id="m3", provisional=60, verdict={"decision": "shortlist", "score": 95}
    )
    for x in (a, b, c, partial):
        st.cases[x.key] = x
    assert st.mark_seen("m") and not st.mark_seen("m")
    st.save()
    st2 = State.load(tmp_path / "s.json")
    assert [x.number for x in st2.ranked()] == [3, 2, 1, 4]
    st2.by_number(2).verdict = {"decision": "pass", "score": 99}
    st2.by_number(1).verdict = {"decision": "maybe", "score": 10}
    # shortlist, maybe, undecided partial, pass (whatever its score)
    assert [x.number for x in st2.ranked()] == [3, 1, 4, 2]
    assert st2.by_number(2).repo == "y"
    assert oct((tmp_path / "s.json").stat().st_mode & 0o777) == "0o600"


def test_slug():
    assert slug("My Cool_Repo.JS") == "my-cool-repo-js"
    assert slug("!!!") == "case"


def test_parse_scorecard_backtick_wrapped_lines(rubric):
    body = (
        "Report text.\n"
        '`{"scores": {"technical_execution": 7, "creativity_impact": 7},`\n'
        '` "notes": {"technical_execution": "38 tests pass"},`\n'
        '` "summary": "ok", "runs": true,`\n'
        '` "red_flags": []}`'
    )
    card = scoring.parse_scorecard(body, rubric)
    assert card["scores"] == {"technical_execution": 7.0, "creativity_impact": 7.0}
    assert card["runs"] is True


def test_parse_scorecard_bare_json(rubric):
    card = scoring.parse_scorecard('Scores: {"scores": {"presentation": 6}, "summary": "s"} done', rubric)
    assert card["scores"] == {"presentation": 6.0}
