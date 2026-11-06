"""``jury``: run the judging floor on a Switch server.

jury up                  register Clerk + Auditor, create the rooms, run
jury doctor              check the server, token, bridge and judges
jury submit URL...       file submissions into the intake room
jury import FILE.csv     file every repo link in a CSV (any column)
jury demo                the console on a recorded floor, no Switch needed
jury audit URL           run the Auditor locally and print its report
jury export [--csv F]    dump the leaderboard
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import auditor, config, scoring
from .auditor_agent import AuditorAgent
from .clerk import Clerk
from .state import State, find_repos
from .switch import Credentials, SwitchAgent, SwitchError, health, register_agent

log = logging.getLogger("jury")

CLERK = "jury-clerk"
AUDITOR = "jury-auditor"
COURIER = "jury-courier"

IDENTITIES = {
    CLERK: ("Clerk", "Runs the Jury judging floor: opens a case room per submission, tallies scorecards, records verdicts."),
    AUDITOR: (
        "Auditor",
        "Deterministic judge: clones a submission and reports hard facts (Switch wiring, repro signals, commit window, secrets).",
    ),
    COURIER: ("Courier", "Files submissions into the Jury intake room from scripts and CSV imports."),
}


TOKEN_HELP = (
    "No registration token yet. In Switch Console: Settings → API keys → type a label (e.g. jury) → "
    "Create registration key, copy it, then run:\n"
    "    echo 'SWITCH_REGISTRATION_TOKEN=<paste it>' >> .env\n"
    "  and run this command again."
)


def _die(msg: str, code: int = 1) -> None:
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


async def ensure_agent(cfg: config.Config, state: State, name: str) -> SwitchAgent:
    saved = state.agents.get(name)
    if saved:
        agent = SwitchAgent(cfg.switch_url, Credentials(**saved))
        try:
            await agent.op("list_rooms")
            return agent
        except SwitchError as e:
            if e.status not in (401, 403, 404):
                raise
            log.warning("saved key for %s rejected (%s); re-registering", name, e.status)
            await agent.close()
    if not cfg.registration_token:
        _die(TOKEN_HELP)
    display, desc = IDENTITIES[name]
    try:
        creds = await register_agent(cfg.switch_url, cfg.registration_token, name=name, display_name=display, description=desc)
    except SwitchError as e:
        if e.status != 409:
            raise
        # The name exists and this state dir has no key for it. That is either
        # our own lost key or a second Jury on the same server; taking it over
        # would cut a live Clerk off mid-case, so only do it when asked.
        if os.environ.get("JURY_TAKEOVER") != "1":
            _die(
                f"An agent named '{name}' already exists on {cfg.switch_url} and this folder has no key for it.\n"
                "  If another Jury is running against this server, stop here: two Clerks would fight over the same rooms.\n"
                "  If this is your own Jury and you lost its .jury/ folder, run again with JURY_TAKEOVER=1 to re-key it."
            )
        creds = await register_agent(
            cfg.switch_url, cfg.registration_token, name=name, display_name=display, description=desc, overwrite=True
        )
    state.agents[name] = creds.to_json()
    state.save()
    print(f"  registered {name}")
    return SwitchAgent(cfg.switch_url, creds)


async def open_addressing(a: SwitchAgent, b: SwitchAgent) -> None:
    """Let anyone in a room address the other agent (an agent can't change itself)."""
    for setter, target in ((a, b), (b, a)):
        try:
            await setter.op("update_agent_detail", {"agent_id": target.agent_id, "addressing": "anyone"})
        except SwitchError as e:
            log.warning("could not open addressing on %s: %s", target.name, e)


BENCH_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "jury-bench.template.yaml"
BENCH_NAME = "Jury bench"


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
LIBRARY = {
    BENCH_NAME: (
        "jury-bench.template.yaml",
        "Jury's two LLM judges (Inspector on Claude Code, Critic on OpenCode or Codex) and a chambers room to meet them.",
    ),
    "Jury critic": (
        "jury-critic.template.yaml",
        "Just the Jury Critic, defaulting to Claude Code. Use it to swap the Critic's provider.",
    ),
}


async def publish_template(clerk: SwitchAgent, name: str) -> str:
    """Save (or refresh) one of Jury's templates in the server's library, so Console lists it."""
    file, desc = LIBRARY[name]
    path = TEMPLATES_DIR / file
    if not path.exists():
        return "template file missing"
    text = path.read_text(encoding="utf-8")
    try:
        existing = [t for t in (await clerk.op("list_templates", {"query": name}) or []) if t.get("name") == name and t.get("can_edit")]
        if existing:
            await clerk.op("update_template", {"template_id": existing[0]["id"], "yaml": text, "description": desc})
            return "updated"
        await clerk.op("save_template", {"name": name, "description": desc, "yaml": text, "visibility": "shared"})
        return "published"
    except SwitchError as e:
        return f"not published ({e.detail[:120]})"


async def publish_bench(clerk: SwitchAgent) -> str:
    return await publish_template(clerk, BENCH_NAME)


def snapshot(state: State, rubric: scoring.Rubric, cfg: config.Config, judges_present: list[str] | None = None) -> dict[str, Any]:
    """Everything the web console shows, in one JSON document (read-only)."""
    from . import __version__

    cases = []
    for rank, c in enumerate(state.ranked(rubric.verdicts), 1):
        d = asdict(c)
        d.update(rank=rank, label=c.label, url=c.url, final=c.final)
        d.pop("extra", None)
        d.pop("intake_message_id", None)
        d["judges"] = c.extra.get("judges", [])
        d["assignee"] = c.extra.get("assignee")
        d["first_pass_s"] = c.extra.get("first_pass_s")
        d["appeal_log"] = [
            {"by": a.get("by"), "text": a.get("text"), "at": a.get("at"), "previous": (a.get("previous") or {}).get("decision")}
            for a in c.extra.get("appeals", [])
        ]
        d["appeals"] = len(d["appeal_log"])
        d["nudged"] = c.extra.get("nudged", [])
        d["disagreements"] = [
            {"criterion": cid, "a": ja, "sa": sa, "b": jb, "sb": sb}
            for cid, ja, sa, jb, sb in scoring.disagreements(c.scorecards, rubric, cfg.disagreement_gap)
        ]
        cases.append(d)
    return {
        "version": __version__,
        "rubric": {
            "name": rubric.name,
            "verdicts": rubric.verdicts,
            "shortlist_at": rubric.shortlist_at,
            "event_url": rubric.event_url,
            "criteria": [{"id": x.id, "label": x.label, "weight": x.weight, "judges": x.judges} for x in rubric.criteria],
        },
        "rooms": {"intake": cfg.intake_room, "floor": cfg.floor_room},
        "bench": {
            "clerk": CLERK,
            "auditor": AUDITOR,
            "judges": cfg.judges,
            "present": judges_present if judges_present is not None else cfg.judges,
            "humans": cfg.judge_users,
        },
        "settings": {
            "intake_closed": state.intake_closed,
            "window_start": cfg.window_start.isoformat(),
            "window_end": cfg.window_end.isoformat(),
            "enforce_deadline": cfg.enforce_deadline,
            "disagreement_gap": cfg.disagreement_gap,
            "remind_minutes": cfg.remind_minutes,
            "share_feedback": cfg.share_feedback,
        },
        "cases": cases,
        "generated_at": time.time(),
    }


async def cmd_up(cfg: config.Config, args: argparse.Namespace) -> None:
    from .web import serve

    rubric = scoring.load_rubric(cfg.rubric_path)
    print(f"⚖️  Jury · rubric: {rubric.name} · server: {cfg.switch_url} ({cfg.source})")
    if not await health(cfg.switch_url):
        _die(f"Switch is not answering at {cfg.switch_url}/health. Start the server (Switch Console → local server) first.")
    state = State.load(cfg.state_dir / "state.json")
    clerk_agent = await ensure_agent(cfg, state, CLERK)
    auditor_agent = await ensure_agent(cfg, state, AUDITOR)
    await open_addressing(clerk_agent, auditor_agent)

    clerk = Clerk(cfg, state, rubric, clerk_agent, AUDITOR)
    aud = AuditorAgent(cfg, rubric, auditor_agent)
    aud.lookup = state.by_room
    aud.case_by_key = lambda k: state.cases.get(k.lower())

    judges = await clerk.present_judges()
    missing = [j for j in cfg.judges if j not in judges]
    print(f"  judges on the bench: {', '.join([AUDITOR, *judges])}")
    if missing:
        print(f"  template '{BENCH_NAME}': {await publish_bench(clerk_agent)}")
        print(f"  ⚠ not on this server yet: {', '.join(missing)}. In Switch Console: Templates → '{BENCH_NAME}' → Create.")
    print(f"  template 'Jury critic': {await publish_template(clerk_agent, 'Jury critic')}")

    tasks = [
        asyncio.create_task(clerk_agent.run(clerk.handle), name="clerk"),
        asyncio.create_task(auditor_agent.run(aud.handle), name="auditor"),
    ]
    if cfg.remind_minutes > 0:
        tasks.append(asyncio.create_task(clerk.remind_loop(), name="reminders"))
    await asyncio.wait_for(clerk_agent._stream_open.wait(), 30)
    await clerk.ensure_rooms()
    if not args.quiet:
        await clerk.greet()
    else:
        await clerk.refresh_floor()

    server = None
    if not args.no_web:
        try:
            server = await serve(cfg.web_host, cfg.web_port, lambda: snapshot(state, rubric, cfg, judges))
            print(f"  leaderboard: http://{cfg.web_host}:{cfg.web_port}")
        except OSError as e:
            print(f"  ⚠ web view off ({e})")
    print(f"  intake room: #{cfg.intake_room} · floor: #{cfg.floor_room}")
    print("  in session. Ctrl+C to adjourn.")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    waiter = asyncio.create_task(stop.wait())
    await asyncio.wait([waiter, *tasks], return_when=asyncio.FIRST_COMPLETED)
    for t in tasks:
        t.cancel()
    if server:
        server.close()
    state.save()
    await clerk_agent.close()
    await auditor_agent.close()
    print("\n  adjourned.")


async def cmd_doctor(cfg: config.Config, args: argparse.Namespace) -> None:
    ok = True

    def line(good: bool, msg: str) -> None:
        nonlocal ok
        ok &= good
        print(("  ✓ " if good else "  ✗ ") + msg)

    print(f"Jury doctor · {cfg.switch_url} (config from {cfg.source})")
    line(await health(cfg.switch_url), "Switch answers /health")
    line(bool(cfg.registration_token), "registration token available" + ("" if cfg.registration_token else "\n      " + TOKEN_HELP))
    try:
        rubric = scoring.load_rubric(cfg.rubric_path)
        line(True, f"rubric '{rubric.name}' loads, weights add to 100")
    except Exception as e:
        line(False, f"rubric {cfg.rubric_path}: {e}")
    import shutil

    line(shutil.which("git") is not None, "git is installed (the Auditor clones with it)")
    state = State.load(cfg.state_dir / "state.json")
    if ok:
        try:
            clerk = await ensure_agent(cfg, state, CLERK)
            agents = {a["name"] for a in await clerk.op("list_agents") or []}
            for j in cfg.judges:
                line(
                    j in agents,
                    f"judge agent '{j}' exists" + ("" if j in agents else " (create it from templates/jury-bench.template.yaml)"),
                )
            bridges = await clerk.op("list_bridges") or []
            names = [b.get("display_name") or b.get("name") for b in bridges]
            line(bool(bridges), "a messaging app is connected: " + (", ".join(map(str, names)) or "none (rooms will be Switch-only)"))
            if cfg.bridge:
                line(cfg.bridge in names, f"JURY_BRIDGE '{cfg.bridge}' is one of them")
            await clerk.close()
        except SwitchError as e:
            line(False, str(e))
    sys.exit(0 if ok else 1)


async def _courier(cfg: config.Config) -> tuple[SwitchAgent, str, State]:
    state = State.load(cfg.state_dir / "state.json")
    intake = state.rooms.get("intake")
    if not intake:
        _die("No intake room yet. Run `jury up` once first.")
    # Its own file: a running `jury up` rewrites state.json and must not drop these.
    courier_state = State.load(cfg.state_dir / "courier.json")
    courier = await ensure_agent(cfg, courier_state, COURIER)
    clerk = state.agents.get(CLERK)
    if clerk:
        try:
            await courier.op("invite_agent_to_room", {"room_id": intake, "agent_name": COURIER})
        except SwitchError:
            cl = SwitchAgent(cfg.switch_url, Credentials(**clerk))
            try:
                await cl.op("invite_agent_to_room", {"room_id": intake, "agent_name": COURIER})
            except SwitchError as e:
                if e.status not in (400, 409):
                    log.warning("invite courier: %s", e)
            await cl.close()
    return courier, intake, state


async def cmd_submit(cfg: config.Config, args: argparse.Namespace) -> None:
    courier, intake, _ = await _courier(cfg)
    urls = []
    for a in args.urls:
        urls += [f"https://github.com/{o}/{r}" for o, r in find_repos(a)] or [a]
    if args.file:
        with open(args.file, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                for cell in row:
                    urls += [f"https://github.com/{o}/{r}" for o, r in find_repos(cell)]
    urls = list(dict.fromkeys(urls))
    if not urls:
        _die("No GitHub repo links found.")
    for u in urls:
        await courier.post_direct(intake, f"New submission: {u}")
        print(f"  filed {u}")
        await asyncio.sleep(args.pace)
    await courier.close()


async def cmd_audit(cfg: config.Config, args: argparse.Namespace) -> None:
    repos = find_repos(args.url)
    if not repos:
        _die("Give a GitHub repo URL.")
    owner, repo = repos[0]
    rep = await auditor.audit(owner, repo, cfg.workdir, cfg.window_start, cfg.window_end, cfg.clone_timeout)
    if args.json:
        print(json.dumps(asdict(rep), indent=2, default=str))
    else:
        print(auditor.render(rep, detailed=args.detailed))
        print("\nscores:", json.dumps(rep.scores))


async def cmd_export(cfg: config.Config, args: argparse.Namespace) -> None:
    rubric = scoring.load_rubric(cfg.rubric_path)
    state = State.load(cfg.state_dir / "state.json")
    snap = snapshot(state, rubric, cfg)
    if args.csv:
        means_cols = [c.id for c in rubric.criteria]
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["rank", "case", "repo", "status", "verdict", "final", "provisional", *means_cols, "judge", "note", "red_flags"])
            for c in state.ranked(rubric.verdicts):
                means = scoring.criterion_means(c.scorecards, rubric)
                w.writerow(
                    [
                        next(x["rank"] for x in snap["cases"] if x["number"] == c.number),
                        c.number,
                        f"{c.owner}/{c.repo}",
                        c.status,
                        (c.verdict or {}).get("decision", ""),
                        c.final,
                        c.provisional,
                        *[round(means[m], 2) if m in means else "" for m in means_cols],
                        (c.verdict or {}).get("judge", ""),
                        (c.verdict or {}).get("note", ""),
                        " | ".join(c.red_flags),
                    ]
                )
        print(f"wrote {args.csv}")
    elif args.md:
        Path(args.md).write_text(results_markdown(state, rubric), encoding="utf-8")
        print(f"wrote {args.md}")
    else:
        print(json.dumps(snap, indent=2, default=str))


DEMO_FILE = Path(__file__).resolve().parent / "static" / "demo.json"


def demo_snapshot() -> dict[str, Any]:
    """The recorded floor the console shows with ``jury demo``: times shift so it always looks fresh."""
    snap = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    shift = time.time() - snap.get("generated_at", time.time())

    def move(x: Any) -> Any:
        if isinstance(x, dict):
            return {
                k: (v + shift if k in ("at", "created_at", "updated_at") and isinstance(v, (int, float)) else move(v)) for k, v in x.items()
            }
        if isinstance(x, list):
            return [move(v) for v in x]
        return x

    snap = move(snap)
    snap["generated_at"] = time.time()
    snap["demo"] = True
    return snap


async def cmd_demo(cfg: config.Config, args: argparse.Namespace) -> None:
    """Serve the console on a recorded floor: no Switch, no keys, nothing to set up."""
    from .web import serve

    server = await serve(cfg.web_host, args.port or cfg.web_port, demo_snapshot)
    print(f"⚖️  Jury demo floor (recorded data, read-only): http://{cfg.web_host}:{args.port or cfg.web_port}")
    print("  Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        server.close()


def results_markdown(state: State, rubric: scoring.Rubric) -> str:
    """A results write-up an organiser can post: the ranking, then why, case by case."""
    ranked = state.ranked(rubric.verdicts)
    decided = [c for c in ranked if c.verdict]
    out = [f"# Results · {rubric.name}", ""]
    out.append(
        f"{len(ranked)} submission(s) judged, {len(decided)} decided. Scores are out of 100 on the event's rubric: "
        + ", ".join(f"{c.label} {c.weight:g}%" for c in rubric.criteria)
        + "."
    )
    out += [
        "",
        "| # | Submission | Verdict | Score | " + " | ".join(c.label for c in rubric.criteria) + " |",
        "|---|---|---|---|" + "---|" * len(rubric.criteria),
    ]
    for i, c in enumerate(ranked, 1):
        means = scoring.criterion_means(c.scorecards, rubric)
        out.append(
            f"| {i} | [{c.owner}/{c.repo}]({c.url}) | {(c.verdict or {}).get('decision', c.status).upper()} | "
            + (f"{c.final:g}" if c.final is not None else "–")
            + " | "
            + " | ".join(f"{means[k.id]:.1f}" if k.id in means else "–" for k in rubric.criteria)
            + " |"
        )
    for c in decided:
        v = c.verdict or {}
        out += [
            "",
            f"## {c.label}: {c.owner}/{c.repo} · {v.get('decision', '').upper()}" + (f" · {c.final:g}/100" if c.final is not None else ""),
        ]
        if v.get("note"):
            out.append(f"> {v['note']} ({v.get('judge', 'judge')})")
        for judge, card in c.scorecards.items():
            note = card.get("summary") or "; ".join(card.get("notes", {}).values())
            if note:
                out.append(f"- **{judge}**: {note}")
        if c.red_flags:
            out.append("- Flags: " + "; ".join(c.red_flags))
    out += ["", "_Generated by [Jury](https://github.com/apexxx01/jury): AI judges gathered the evidence, humans gave every verdict._", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="jury", description="A hackathon judging floor on Switch.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    up = sub.add_parser("up", help="run Clerk and the Auditor")
    up.add_argument("--no-web", action="store_true")
    up.add_argument("--quiet", action="store_true", help="don't post the greeting")
    sub.add_parser("doctor", help="check the setup")
    s = sub.add_parser("submit", help="file submissions into the intake room")
    s.add_argument("urls", nargs="*")
    s.add_argument("--file", help="CSV with repo links in any column")
    s.add_argument("--pace", type=float, default=2.0, help="seconds between filings")
    i = sub.add_parser("import", help="file every repo link in a CSV")
    i.add_argument("file")
    i.add_argument("--pace", type=float, default=2.0)
    a = sub.add_parser("audit", help="run the Auditor locally on one repo")
    a.add_argument("url")
    a.add_argument("--json", action="store_true")
    a.add_argument("--detailed", action="store_true")
    e = sub.add_parser("export", help="dump the leaderboard")
    e.add_argument("--csv", help="write the leaderboard as CSV")
    e.add_argument("--md", help="write a results report as Markdown")
    d = sub.add_parser("demo", help="open the console on a recorded floor (no Switch needed)")
    d.add_argument("--port", type=int, default=0)
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = config.load()
    cmd = args.cmd or "up"
    if cmd == "up" and not hasattr(args, "no_web"):
        args = p.parse_args(["up", *(argv or sys.argv[1:])])
    if cmd == "import":
        args.urls, cmd = [], "submit"
    fn = {"up": cmd_up, "doctor": cmd_doctor, "submit": cmd_submit, "audit": cmd_audit, "export": cmd_export, "demo": cmd_demo}[cmd]
    try:
        asyncio.run(fn(cfg, args))
    except KeyboardInterrupt:
        pass
    except SwitchError as e:
        _die(str(e))


if __name__ == "__main__":
    main()
