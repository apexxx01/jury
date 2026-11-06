"""Build ``jury/static/demo.json``: the recorded floor ``jury demo`` serves.

Every submission except one is a sample team (no real repository behind it,
so the console doesn't link them). Case #001 is Jury's own dry run on the
author's machine: real scorecards, real flags. The floor goes through the
same scoring code and the same snapshot as a live one, so what the demo
shows is exactly what ``jury up`` would show for those scorecards.

    uv run python scripts/make_demo.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jury import config, scoring  # noqa: E402
from jury.cli import snapshot  # noqa: E402
from jury.state import Case, State  # noqa: E402

A, INS, C = "jury-auditor", "jury-inspector", "jury-critic"
BENCH = [A, INS, C]
NOW = time.time()
H = 3600.0


def card(judge: str, at: float, scores: dict[str, float], summary: str, notes: dict[str, str] | None = None, flags=None) -> tuple:
    return judge, {"scores": scores, "notes": notes or {}, "summary": summary, "red_flags": flags or [], "at": at}


def audit(repo: str, sha: str, sw: float, te: float, pr: float, window: str, at: float, flags=None) -> tuple:
    return card(
        A,
        at,
        {"use_of_switch": sw, "technical_execution": te, "presentation": pr},
        f"Deterministic audit of {repo}@{sha}. Clean on secrets. {window} commits in the hackathon window.",
        {
            "use_of_switch": "Switch SDK calls and room ops found in source." if sw >= 5 else "No trace of Switch in the repository.",
            "technical_execution": "Tests, lockfile and CI present." if te >= 7 else "No tests and no lockfile.",
            "presentation": "README with screenshots and a demo video link." if pr >= 7 else "Thin README, no demo video.",
        },
        flags,
    )


SAMPLES = [
    # (owner, repo, filed hours ago, assignee, scorecards, verdict, extra flags, appeal)
    (
        "orbit-labs",
        "standup-relay",
        7.6,
        "maya",
        [
            audit("orbit-labs/standup-relay", "9c1e2aa", 10, 8.5, 9, "64/64", 7.6 * H - 40),
            card(
                INS,
                7.6 * H - 610,
                {"technical_execution": 8, "creativity_impact": 9},
                "Cloned, `docker compose up` worked first try. Agents post async standups into a room and the summary doc updates live.",
                {"technical_execution": "Clean install, 41 tests pass.", "creativity_impact": "Replaces a daily meeting outright."},
            ),
            card(
                C,
                7.6 * H - 720,
                {"use_of_switch": 9, "creativity_impact": 9, "presentation": 8},
                "Switch is the product here, not a bolt-on: rooms per team, a template for the standup bot, documents as the output.",
                {"presentation": "Two-minute video shows the whole loop."},
            ),
        ],
        {"decision": "shortlist", "score": 90, "note": "best use of rooms + documents on the floor", "judge": "maya", "after": 0.9 * H},
        [],
        None,
    ),
    (
        "ledgerline",
        "invoice-chaser",
        7.1,
        "dev",
        [
            audit("ledgerline/invoice-chaser", "41b07fe", 9, 8, 7, "38/38", 7.1 * H - 35),
            card(
                INS,
                7.1 * H - 540,
                {"technical_execution": 7, "creativity_impact": 8},
                "Ran it against the sample ledger: overdue invoices become a room per client, the agent drafts the nudge and waits for approval.",
                {"technical_execution": "One env var missing from .env.example; fixed in 30s."},
            ),
            card(
                C,
                7.1 * H - 600,
                {"use_of_switch": 8, "creativity_impact": 9, "presentation": 7},
                "A real business workflow with a human approval step. Would pay for this.",
            ),
        ],
        {"decision": "shortlist", "score": 86, "note": "human-in-the-loop done right", "judge": "dev", "after": 1.4 * H},
        [],
        None,
    ),
    (
        "pagerpals",
        "oncall-copilot",
        6.4,
        "maya",
        [
            audit("pagerpals/oncall-copilot", "e02d913", 8, 6, 6, "22/22", 6.4 * H - 50),
            card(
                INS,
                6.4 * H - 900,
                {"technical_execution": 5, "creativity_impact": 7},
                "Incident rooms open correctly, but the runbook agent crashed on the second alert. Restart fixed it.",
                {"technical_execution": "Unhandled exception on reconnect."},
            ),
            card(
                C,
                6.4 * H - 960,
                {"use_of_switch": 8, "creativity_impact": 7, "presentation": 6},
                "Good idea, crowded space. Video stops before the interesting part.",
            ),
        ],
        {"decision": "maybe", "score": 64, "note": "promising, needs the reconnect fix", "judge": "maya", "after": 2.1 * H},
        [],
        None,
    ),
    (
        "mergebot",
        "pr-triage-room",
        5.2,
        "dev",
        [
            audit("mergebot/pr-triage-room", "77fa0c4", 9, 8, 7, "51/53", 5.2 * H - 30),
            card(
                INS,
                5.2 * H - 1300,
                {"technical_execution": 3, "creativity_impact": 7},
                "Could not get the webhook path working from a clean clone: the README skips the GitHub App setup.",
                {"technical_execution": "Blocked on undocumented GitHub App permissions."},
            ),
            card(
                C,
                5.2 * H - 1100,
                {"use_of_switch": 9, "creativity_impact": 7, "presentation": 7},
                "Each PR gets a room with reviewer agents; clever use of targeted messages.",
            ),
        ],
        None,
        [],
        None,
    ),
    (
        "notetakers",
        "meeting-minutes",
        6.0,
        "maya",
        [
            audit("notetakers/meeting-minutes", "a5d3b10", 7, 7, 6, "19/19", 6.0 * H - 45),
            card(
                INS,
                6.0 * H - 700,
                {"technical_execution": 7, "creativity_impact": 6},
                "Works. Transcribes a call into a room document and pings owners of action items.",
            ),
            card(
                C,
                6.0 * H - 820,
                {"use_of_switch": 7, "creativity_impact": 5, "presentation": 6},
                "Solid but familiar. The Switch part is the action-item hand-off, which is the best bit.",
            ),
        ],
        None,
        [],
        (
            "notetakers",
            "Action items are assigned to agents in other rooms; the Critic missed the cross-room hand-off in the video at 1:40.",
            4.2 * H,
            "maybe",
        ),
    ),
    (
        "peoplestack",
        "onboarding-bot",
        4.6,
        "dev",
        [
            audit(
                "peoplestack/onboarding-bot",
                "3b9e5c2",
                0,
                7,
                5,
                "12/12",
                4.6 * H - 30,
                ["No trace of Switch in the repository (rule: Switch must be central)."],
            ),
            card(
                INS,
                4.6 * H - 480,
                {"technical_execution": 6, "creativity_impact": 5},
                "A Slack bot with a checklist. Runs fine, but Switch is only mentioned in the README.",
            ),
            card(C, 4.6 * H - 520, {"use_of_switch": 2, "creativity_impact": 5, "presentation": 5}, "Switch is name-dropped, not used."),
        ],
        {"decision": "pass", "score": 38, "note": "Switch isn't central", "judge": "dev", "after": 0.6 * H},
        [],
        None,
    ),
    (
        "quickship",
        "onboarding-bot-v2",
        3.9,
        "maya",
        [
            audit(
                "quickship/onboarding-bot-v2",
                "3b9e5c2",
                0,
                7,
                5,
                "0/12",
                3.9 * H - 25,
                [
                    "No trace of Switch in the repository (rule: Switch must be central).",
                    "No commits inside the hackathon window (rule: the Switch workflow must be built during the event).",
                    "Possible copy: shares 12 commit(s) of git history and 97% of its distinctive code lines (2,140) also appear with **Case #007** `peoplestack/onboarding-bot`. A human should compare them.",
                ],
            ),
            card(
                INS,
                3.9 * H - 450,
                {"technical_execution": 6, "creativity_impact": 4},
                "Identical to another submission down to the commit hashes.",
            ),
            card(
                C,
                3.9 * H - 470,
                {"use_of_switch": 1, "creativity_impact": 2, "presentation": 4},
                "Same project as Case #007 under a new name.",
            ),
        ],
        {"decision": "pass", "score": 5, "note": "copy of #007", "judge": "maya", "after": 0.3 * H},
        [],
        None,
    ),
    (
        "ghostteam",
        "secret-sauce",
        3.4,
        "dev",
        [
            card(
                A,
                3.4 * H - 20,
                {"use_of_switch": 0, "technical_execution": 0, "presentation": 0},
                "Could not clone ghostteam/secret-sauce: private, renamed or missing.",
                {"use_of_switch": "clone failed", "technical_execution": "clone failed", "presentation": "clone failed"},
                ["The repository could not be cloned (private, renamed or missing?)."],
            ),
            card(INS, 3.4 * H - 300, {"technical_execution": 0, "creativity_impact": 0}, "Nothing to run: the link 404s."),
            card(C, 3.4 * H - 320, {"use_of_switch": 0, "creativity_impact": 0, "presentation": 0}, "Can't judge a repo that isn't there."),
        ],
        {"decision": "pass", "score": 0, "note": "repository is private", "judge": "dev", "after": 0.2 * H},
        [],
        None,
    ),
    (
        "helpdesk-hq",
        "support-swarm",
        1.8,
        "maya",
        [
            audit("helpdesk-hq/support-swarm", "c4410de", 9, 8, 6, "33/33", 1.8 * H - 40),
            card(
                INS,
                1.8 * H - 760,
                {"technical_execution": 8, "creativity_impact": 8},
                "Three agents split a ticket queue and escalate to a human room. Install took four minutes.",
            ),
        ],
        None,
        [],
        None,
    ),
    (
        "dealflow",
        "sales-room",
        0.9,
        "dev",
        [audit("dealflow/sales-room", "18ab6f0", 8, 6, 4, "17/17", 0.9 * H - 30, ["No demo video link in the README."])],
        None,
        [],
        None,
    ),
    ("spendwise", "expense-agent", 0.05, "maya", [], None, [], None),
]


def main() -> None:
    cfg = config.load()
    cfg.judge_users = ["maya", "dev"]
    rubric = scoring.load_rubric(ROOT / "rubrics" / "flip-the-switch.yaml")
    state = State(path=ROOT / ".jury" / "demo-state.json")

    # Case #001: Jury's own bench on this repo, recorded on the author's machine.
    t0 = NOW - 8.2 * H
    jury = Case(1, "apexxx01", "jury", "user", None, room_id="r1", room_name="case-001-jury", created_at=t0)
    jury.scorecards = {
        A: {
            "scores": {"use_of_switch": 10, "technical_execution": 9, "presentation": 7},
            "notes": {},
            "summary": "Deterministic audit of apexxx01/jury@8f8dc47. Clean on secrets. 7/7 commits in the hackathon window.",
            "at": t0 + 30,
        },
        INS: {
            "scores": {"technical_execution": 7, "creativity_impact": 7},
            "notes": {},
            "summary": "Install, 38 tests and lint all pass on a clean clone with no fixes, but the end-to-end loop was not run from my clone and was only seen working on the author's own bench.",
            "at": t0 + 540,
        },
        C: {"scores": {"use_of_switch": 9, "creativity_impact": 7, "presentation": 6}, "notes": {}, "summary": "", "at": t0 + 611},
    }
    jury.red_flags = [
        "No demo video link in the README.",
        "First commit is a 3,727-line drop on day three of the window, so the history alone cannot show it was built during the event; human should check",
        "Conflict of interest: the bench is judging its own repo and its judges' instructions come from that repo",
    ]
    jury.extra = {"judges": BENCH, "assignee": "maya", "first_pass_s": 611}
    jury.provisional, jury.complete = scoring.provisional_score(jury.scorecards, rubric)
    jury.status = "scored"
    state.cases[jury.key] = jury

    for n, (owner, repo, ago, who, cards, verdict, _flags, appeal) in enumerate(SAMPLES, 2):
        created = NOW - ago * H
        c = Case(n, owner, repo, owner.split("-")[0], None, room_id=f"r{n}", room_name=f"case-{n:03d}-{repo}"[:40], created_at=created)
        c.extra = {"judges": BENCH, "assignee": who}
        for judge, sc in cards:
            sc = dict(sc)
            sc["at"] = NOW - sc["at"] if sc["at"] > 0 else created + 5  # stored as "seconds ago" above
            c.scorecards[judge] = sc
            for f in sc.get("red_flags", []):
                if f not in c.red_flags:
                    c.red_flags.append(f)
        # times above are "this long before now"; anchor them after filing
        for sc in c.scorecards.values():
            sc["at"] = max(sc["at"], created + 5)
        c.provisional, c.complete = scoring.provisional_score(c.scorecards, rubric) if c.scorecards else (None, False)
        if c.scorecards and all(j in c.scorecards for j in BENCH):
            last = max(sc["at"] for sc in c.scorecards.values())
            c.extra["first_pass_s"] = round(last - created)
            c.status = "scored"
            if c.provisional is not None:
                for cid, ja, _sa, jb, _sb in scoring.disagreements(c.scorecards, rubric, cfg.disagreement_gap):
                    c.flagged.append(f"{cid}:{ja}:{jb}")
        if verdict:
            v = dict(verdict)
            at = created + c.extra["first_pass_s"] + v.pop("after")
            c.verdict = {**v, "at": min(at, NOW - 60), "provisional": c.provisional}
            c.status = "decided"
        if appeal:
            by, text, after, prev = appeal
            c.extra["appeals"] = [{"by": by, "text": text, "at": created + after, "previous": {"decision": prev, "score": 58}}]
            c.status = "scored"
        state.cases[c.key] = c

    snap = snapshot(state, rubric, cfg)
    for d in snap["cases"]:
        if d["owner"] != "apexxx01":
            d["url"] = None
            d["sample"] = True
    snap["generated_at"] = NOW
    out = ROOT / "jury" / "static" / "demo.json"
    out.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    print(f"wrote {out} ({len(snap['cases'])} cases)")


if __name__ == "__main__":
    main()
