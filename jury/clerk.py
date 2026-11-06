"""Clerk: runs the judging floor.

* watches ``#jury-intake`` for GitHub links and opens one case room per
  submission, from a Switch room template, with the judges and the rubric in it
* links every case room to the judging floor and back
* reads the judges' scorecards out of the case room, raises disagreements
  between judges back to them, and asks a human for the verdict
* keeps the leaderboard: a room document on the floor, a message per verdict,
  and a live web page
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Any

import yaml

from . import scoring
from .config import Config
from .scoring import Rubric
from .state import Case, State, find_repos, slug
from .switch import Event, SwitchAgent, SwitchError

log = logging.getLogger("jury.clerk")

HELP = """**Jury Clerk** · how this floor works
• Paste a GitHub repo link in **#{intake}** and I open a case room for it. Reply `appeal <why>` in that thread to appeal a verdict.
• In a case room, judges post scorecards; I tally them and flag disagreements.
• A human judge decides: `@{clerk} verdict shortlist 88 great use of rooms` (verdicts: {verdicts}; the score is optional).
• `@{clerk} explain 7` · `@{clerk} calibration` · `@{clerk} close intake` / `open intake`
• `@{clerk} leaderboard` · `@{clerk} status` · `@{clerk} stats` · `@{clerk} my queue` · `@{clerk} assign 7 to @name` · `@{clerk} case 7` · `@{clerk} rescore` / `rescan` / `summon` (in a case room) · `@{clerk} help`"""

INTAKE_INSTRUCTIONS = """This is the Jury intake room. People paste GitHub repository links of
submissions here. The agent named {clerk} opens one case room per submission.
Agents in this room: do not reply to submissions yourselves; {clerk} handles them."""

FLOOR_INSTRUCTIONS = """This is the Jury judging floor: the leaderboard of every case and the
verdicts as they land. Each case has its own linked room. Ask {clerk} for
`leaderboard`, `status` or `case <n>`."""

CASE_INSTRUCTIONS = """This is a Jury case room for ONE hackathon submission: {url}

Who is here and what they do:
- {auditor}: a deterministic auditor. It clones the repo and reports hard facts (Switch wiring, setup steps, tests, demo video, commit window, leaked secrets).
- jury-inspector (if present): clones the repo into its own directory and actually tries to install and run it by following the README. It reports what happened, with exact commands and output.
- jury-critic (if present): judges the idea, the real-world impact, the presentation and whether Switch is central. It challenges the other judges where they are too generous or too harsh.
- {clerk}: tallies scorecards, raises disagreements, records the human verdict.
- Human judges: ask the agents anything, then give the verdict.

Rules for agent judges:
1. Read the room documents "Submission" and "Rubric" first.
2. Score only the criteria the Rubric lists you against, 0 to 10, with evidence.
3. File exactly one scorecard message ending in a ```jury fenced JSON block (format in the Rubric doc). File a new one to revise; the latest counts.
4. If {clerk} says two judges disagree, address the other judge by name, compare evidence, and each file a revised scorecard (or keep yours and say why).
5. Never post the verdict. Humans decide."""


class Clerk:
    def __init__(self, cfg: Config, state: State, rubric: Rubric, agent: SwitchAgent, auditor_name: str) -> None:
        self.cfg = cfg
        self.state = state
        self.rubric = rubric
        self.agent = agent
        self.auditor_name = auditor_name
        self._agents_cache: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})
        self._lock = asyncio.Lock()
        self.on_change: list[Any] = []
        self._doc_task: asyncio.Task[None] | None = None
        self._doc_dirty = False
        self.doc_debounce = 45.0
        self._creator_unlinked = False

    @property
    def name(self) -> str:
        return self.agent.name

    # ── setup ──────────────────────────────────────────────────────────────

    async def known_agents(self, refresh: bool = False) -> dict[str, dict[str, Any]]:
        ts, cached = self._agents_cache
        if refresh or time.time() - ts > 60 or not cached:
            try:
                agents = await self.agent.op("list_agents")
                cached = {a["name"]: a for a in agents or []}
                self._agents_cache = (time.time(), cached)
            except SwitchError as e:
                log.warning("list_agents failed: %s", e)
        return cached

    async def present_judges(self) -> list[str]:
        known = await self.known_agents(refresh=True)
        return [j for j in self.cfg.judges if j in known]

    async def ensure_rooms(self) -> None:
        """Find or create the intake and floor rooms; link them."""
        mine = {r["name"]: r["room_id"] for r in (await self.agent.op("list_rooms") or [])}
        judges = await self.present_judges()
        for role, name, instructions, docs in (
            ("intake", self.cfg.intake_room, INTAKE_INSTRUCTIONS, []),
            ("floor", self.cfg.floor_room, FLOOR_INSTRUCTIONS, [self._rubric_doc()]),
        ):
            rid = self.state.rooms.get(role)
            if rid and rid in mine.values():
                continue
            if name in mine:
                self.state.rooms[role] = mine[name]
                continue
            room: dict[str, Any] = {
                "name": name,
                "description": "Jury: drop submission repo links here" if role == "intake" else "Jury: leaderboard and verdicts",
                "instructions": instructions.format(clerk=self.name),
                "channel_type": "channel_public",
                "agents": [self.name, self.auditor_name] + (judges if role == "floor" else []),
            }
            if docs:
                room["docs"] = docs
            kickoff = None
            if role == "intake":
                kickoff = None
            res = await self._create_room(room, kickoff)
            self.state.rooms[role] = res["room_id"]
            log.info("created room %s (%s)", name, res["room_id"])
        self.state.save()
        intake, floor = self.state.rooms["intake"], self.state.rooms["floor"]
        for src, dst, label in ((floor, intake, "intake: where submissions arrive"), (intake, floor, "judging floor: leaderboard")):
            try:
                await self.agent.op("link_rooms", {"source_room_id": src, "target_room_id": dst, "label": label})
            except SwitchError as e:
                if e.status not in (400, 409):
                    log.warning("link_rooms: %s", e)

    async def greet(self) -> None:
        intake = self.state.rooms["intake"]
        await self.agent.post(
            intake,
            f"⚖️ **Jury is in session.** Paste a GitHub repo link here and I'll open a case room for it, "
            f"with {self._judge_phrase(await self.present_judges())} on the bench. Say `@{self.name} help` for commands.",
        )
        await self.refresh_floor(now=True)

    def _judge_phrase(self, judges: list[str]) -> str:
        names = [self.auditor_name, *judges]
        return ", ".join(f"**{n}**" for n in names[:-1]) + (f" and **{names[-1]}**" if len(names) > 1 else f"**{names[0]}**")

    def _rubric_doc(self) -> dict[str, str]:
        return {
            "name": "Rubric",
            "description": f"How submissions are scored: {self.rubric.name}",
            "instructions": "Agent judges: read this before scoring. Score only the criteria you are listed against.",
            "content": self.rubric.to_markdown(),
        }

    async def _create_room(self, room: dict[str, Any], kickoff: str | None) -> dict[str, Any]:
        """create_room_from_yaml, degrading gracefully.

        Tries, in order: the deployer ({$creator}) plus named judges; only the
        named judges; nobody; and finally no bridge. ``{$creator}`` fails when
        the agents' owner has no account linked on the messaging app (common
        when the registration token belongs to the server's bootstrap user),
        and named people keep the human judges in the room anyway.
        """
        base = dict(room)
        if self.cfg.bridge:
            base["bridge"] = self.cfg.bridge
        named = list(self.cfg.judge_users)
        attempts: list[dict[str, Any]] = []
        if not self._creator_unlinked:
            attempts.append({**base, "users": ["{$creator}", *named]})
        if named:
            attempts.append({**base, "users": named})
        attempts.append(dict(base))
        if self.cfg.bridge:
            attempts.append({k: v for k, v in base.items() if k != "bridge"})
        last: Exception | None = None
        for i, body in enumerate(attempts):
            doc: dict[str, Any] = {"room": body}
            if kickoff:
                doc["kickoff"] = kickoff
            try:
                return await self.agent.op("create_room_from_yaml", {"yaml": yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)})
            except SwitchError as e:
                last = e
                if "{$creator}" in e.detail or "no linked account" in e.detail:
                    if not self._creator_unlinked:
                        self._creator_unlinked = True
                        hint = (
                            "" if named else " Set JURY_JUDGE_USERS=<your username on the messaging app> in .env to be added to every room."
                        )
                        log.warning("the agents' owner has no linked account on the messaging app.%s", hint)
                    continue
                if e.status in (400, 403, 404, 422) and i + 1 < len(attempts):
                    log.warning("room create refused (%s); retrying with less", e.detail[:200])
                    continue
                raise
        raise last or RuntimeError("room create failed")

    # ── event handling ─────────────────────────────────────────────────────

    async def handle(self, ev: Event) -> None:
        if ev.kind != "message":
            return
        p = ev.payload
        sender_name = p.get("sender_name") or ""
        if sender_name == self.name or p.get("on_behalf_of") == self.name:
            return
        if p.get("sender_kind") == "platform" and not p.get("on_behalf_of"):
            return  # Switch speaking in its own voice (joins, notices)
        if not self.state.mark_seen(p.get("message_id")):
            return
        body = p.get("body") or ""
        room = ev.room_id
        async with self._lock:
            try:
                if room == self.state.rooms.get("intake"):
                    await self._on_intake(p, body)
                elif room == self.state.rooms.get("floor"):
                    await self._on_floor(p, body)
                elif (case := self.state.by_room(room)) is not None:
                    await self._on_case(case, p, body)
            finally:
                self.state.save()

    def _addressed_to_me(self, p: dict[str, Any], body: str) -> bool:
        return bool(re.search(rf"@{re.escape(self.name)}\b", body, re.IGNORECASE)) or (bool(p.get("addressed")) and f"@{self.name}" in body)

    async def _is_agent(self, sender_name: str) -> bool:
        if sender_name in (self.name, self.auditor_name):
            return True
        known = await self.known_agents()
        if sender_name in known:
            return True
        return sender_name in await self.known_agents(refresh=True)

    async def _on_intake(self, p: dict[str, Any], body: str) -> None:
        thread = p.get("thread_id")
        if thread and re.search(r"\bappeal\b", body, re.I) and not await self._is_agent(p.get("sender_name") or ""):
            case = next((c for c in self.state.cases.values() if c.intake_message_id == thread), None)
            if case is not None:
                await self._appeal(case, p, body)
                return
        repos = find_repos(body)
        if not repos:
            if self._addressed_to_me(p, body):
                await self._command(self.state.rooms["intake"], p, body, None)
            return
        if await self._is_agent(p.get("sender_name") or "") and p.get("sender_name") != "jury-courier":
            return  # agents chatting in intake don't file submissions
        for owner, repo in repos[:5]:
            await self.open_case(owner, repo, p)

    async def open_case(self, owner: str, repo: str, p: dict[str, Any]) -> Case | None:
        key = f"{owner}/{repo}".lower()
        intake = self.state.rooms["intake"]
        thread = p.get("message_id")
        if (existing := self.state.cases.get(key)) is not None:
            where = f"#{existing.room_name}" if existing.room_name else "its room"
            await self.agent.post(intake, f"📎 `{owner}/{repo}` is already on file as **{existing.label}** → {where}.", thread)
            return existing
        if self.state.intake_closed:
            await self.agent.post(
                intake,
                f"🚪 Intake is closed, so `{owner}/{repo}` was not filed. A judge can reopen it with `@{self.name} open intake`.",
                thread,
            )
            return None
        submitter = p.get("on_behalf_of") or p.get("sender_name") or "someone"
        case = Case(number=self.state.next_number(), owner=owner, repo=repo, submitter=submitter, intake_message_id=thread)
        if self.cfg.enforce_deadline and time.time() > self.cfg.window_end.timestamp():
            case.red_flags.append(f"Filed after the submission deadline ({self.cfg.window_end:%b %d, %H:%M %Z}).")
        self.state.cases[key] = case
        self.state.save()
        judges = await self.present_judges()
        case.extra["judges"] = [self.auditor_name, *judges]
        if assignee := self._next_assignee():
            case.extra["assignee"] = assignee
        room_name = f"case-{case.number:03d}-{slug(repo, 60)}"
        submission_md = (
            f"# {case.label}: {owner}/{repo}\n\n"
            f"- Repository: {case.url}\n- Filed by: {submitter}\n- Filed at: {time.strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"- Rubric: {self.rubric.name}\n- Judges on this case: {', '.join(case.extra['judges'])}\n\n"
            f"Original intake message:\n\n> " + (p.get("body") or "").replace("\n", "\n> ")[:1500]
        )
        room = {
            "name": room_name,
            "description": f"Jury {case.label}: {owner}/{repo}",
            "instructions": CASE_INSTRUCTIONS.format(url=case.url, clerk=self.name, auditor=self.auditor_name),
            "channel_type": "channel_public",
            "agents": [self.name, *case.extra["judges"]],
            "docs": [
                {
                    "name": "Submission",
                    "description": f"What is being judged in {case.label}",
                    "instructions": "The repository under review. Clone it into your own working directory.",
                    "content": submission_md,
                },
                self._rubric_doc(),
            ],
        }
        mentions = " ".join(f"@{j}" for j in judges)
        kickoff = (
            f"{mentions}{' ' if mentions else ''}⚖️ **{case.label} is open: {case.url}** (filed by {submitter}).\n"
            f"Read the **Submission** and **Rubric** documents in this room, then each file one scorecard "
            f"for the criteria you own. Inspector: clone it and actually run it. Critic: judge the idea, the impact "
            f"and whether Switch is central. {self.auditor_name} is posting hard facts now. Disagree in the open; a human gives the verdict."
        )
        try:
            res = await self._create_room(room, kickoff)
        except SwitchError as e:
            del self.state.cases[key]
            await self.agent.post(intake, f"⚠️ Couldn't open a case room for `{owner}/{repo}`: {e.detail[:300]}", thread)
            return None
        case.room_id, case.room_name = res["room_id"], res.get("room_name", room_name)
        case.extra["documents"] = res.get("created_document_ids", [])
        floor = self.state.rooms["floor"]
        for src, dst, label in (
            (case.room_id, floor, "judging floor: leaderboard and other cases"),
            (floor, case.room_id, f"{case.label}: {owner}/{repo}"),
            (case.room_id, intake, "intake: where this submission came from"),
        ):
            try:
                await self.agent.op("link_rooms", {"source_room_id": src, "target_room_id": dst, "label": label})
            except SwitchError as e:
                log.debug("link %s: %s", label, e)
        self.state.save()
        await self.agent.post(
            intake,
            f"📂 **{case.label}** opened for `{owner}/{repo}` → **#{case.room_name}**\n"
            f"On the bench: {', '.join(case.extra['judges'])}. I'll post the result on #{self.cfg.floor_room}.",
            thread,
        )
        # Wake the auditor through Switch: an addressed message in the case room.
        await self.agent.mention(case.room_id, [self.auditor_name], f"audit {case.url}")
        await self.refresh_floor(note=f"📂 New: **{case.label}** `{owner}/{repo}` → #{case.room_name}")
        self._changed()
        return case

    async def _on_floor(self, p: dict[str, Any], body: str) -> None:
        if not self._addressed_to_me(p, body):
            return
        repos = find_repos(body)
        if repos and not await self._is_agent(p.get("sender_name") or ""):
            for owner, repo in repos[:5]:
                await self.open_case(owner, repo, p)
            return
        await self._command(self.state.rooms["floor"], p, body, None)

    async def _on_case(self, case: Case, p: dict[str, Any], body: str) -> None:
        sender = p.get("on_behalf_of") or p.get("sender_name") or ""
        is_agent = await self._is_agent(sender)
        if is_agent:
            card = scoring.parse_scorecard(body, self.rubric)
            if card:
                await self._record_scorecard(case, sender, card, p.get("message_id"))
            elif await self._needs_nudge(case, sender, body):
                await self.agent.mention(
                    case.room_id or "",
                    [sender],
                    (
                        "your ```jury block didn't parse as JSON. "
                        if "```jury" in body
                        else "thanks, but I couldn't find a scorecard in that. "
                    )
                    + "End a message with one fenced block tagged "
                    '`jury`: ```jury {"scores": {...}, "notes": {...}, "summary": "..."}``` with the criteria the '
                    "Rubric document assigns you, 0 to 10.",
                    p.get("message_id"),
                )
            return
        if self._addressed_to_me(p, body) or re.match(r"\s*verdict\b", body, re.IGNORECASE):
            verdict = scoring.parse_verdict(body, self.rubric)
            if verdict:
                await self._record_verdict(case, sender, verdict, p.get("message_id"))
                return
            if self._addressed_to_me(p, body):
                await self._command(case.room_id or "", p, body, case)

    async def _needs_nudge(self, case: Case, judge: str, body: str) -> bool:
        """A judge on this case wrote a long report with no scorecard and hasn't filed one: nudge once."""
        if judge not in (case.extra.get("judges") or []) or judge == self.auditor_name or judge in case.scorecards:
            return False
        if len(body) < 400 and "```jury" not in body:
            return False
        nudged = case.extra.setdefault("nudged", [])
        if judge in nudged:
            return False
        nudged.append(judge)
        return True

    # ── scorecards and verdicts ────────────────────────────────────────────

    async def _record_scorecard(self, case: Case, judge: str, card: dict[str, Any], message_id: str | None) -> None:
        owned = {c.id for c in self.rubric.criteria_for(judge)}
        if owned:
            card["scores"] = {k: v for k, v in card["scores"].items() if k in owned} or card["scores"]
        card["at"] = time.time()
        card["message_id"] = message_id
        revised = judge in case.scorecards
        case.scorecards[judge] = card
        for f in card.get("red_flags", []):
            if f not in case.red_flags:
                case.red_flags.append(f)
        case.provisional, case.complete = scoring.provisional_score(case.scorecards, self.rubric)
        case.updated_at = time.time()
        expected = case.extra.get("judges") or [self.auditor_name]
        missing = [j for j in expected if j not in case.scorecards]
        parts = ", ".join(f"{self._label(k)} {v:g}" for k, v in card["scores"].items())
        msg = (
            f"🧾 {'Revised' if revised else 'Scorecard'} from **{judge}** recorded: {parts}. "
            f"Running score **{case.provisional:g}/100**"
            + (" (not every criterion is covered yet)" if not case.complete else "")
            + (f". Waiting on: {', '.join(missing)}." if missing else ".")
        )
        await self.agent.post(case.room_id or "", msg, message_id)

        # Judges who disagree hard settle it in the room, before a human looks.
        for cid, ja, sa, jb, sb in scoring.disagreements(case.scorecards, self.rubric, self.cfg.disagreement_gap):
            tag = f"{cid}:{ja}:{jb}"
            if tag in case.flagged:
                continue
            case.flagged.append(tag)
            names = [n for n in (ja, jb) if n != self.auditor_name] or [ja, jb]
            await self.agent.mention(
                case.room_id or "",
                names,
                f"⚔️ You disagree on **{self._label(cid)}**: {ja} says {sa:g}, {jb} says {sb:g}. "
                f"Compare your evidence here, then each file a revised scorecard or say why you're keeping yours.",
            )

        if not missing and case.status == "open":
            case.status = "scored"
            case.extra.setdefault("first_pass_s", round(time.time() - case.created_at))
            await self._ask_for_verdict(case)
            # The floor's document moves when a case is ready for a verdict, not on
            # every scorecard: Switch announces each edit in the channel. The web
            # view stays live either way.
            await self.refresh_floor()
        self._changed()

    async def _ask_for_verdict(self, case: Case) -> None:
        rows = []
        means = scoring.criterion_means(case.scorecards, self.rubric)
        for c in self.rubric.criteria:
            m = means.get(c.id)
            rows.append(
                f"`{scoring.bar(None if m is None else m * 10)}` {c.label} ({c.weight:g}%): "
                + (f"**{m:.1f}**/10" if m is not None else "n/a")
            )
        suggestion = (
            self.rubric.verdicts[0]
            if (case.provisional or 0) >= self.rubric.shortlist_at
            else self.rubric.verdicts[min(1, len(self.rubric.verdicts) - 1)]
        )
        flags = ("\n🚩 " + "\n🚩 ".join(case.red_flags[:6])) if case.red_flags else ""
        open_fights = scoring.disagreements(case.scorecards, self.rubric, self.cfg.disagreement_gap)
        if open_fights:
            flags += "\n⚔️ Judges still split on: " + ", ".join(
                f"{self._label(c)} ({ja} {sa:g} vs {jb} {sb:g})" for c, ja, sa, jb, sb in open_fights
            )
        assignee = case.extra.get("assignee")
        people = f"@{assignee}" if assignee else " ".join(f"@{u}" for u in self.cfg.judge_users)
        await self.agent.post(
            case.room_id or "",
            f"{people + ' ' if people else ''}✅ **All scorecards are in for {case.label}.** Provisional **{case.provisional:g}/100**.\n"
            + "\n".join(rows)
            + flags
            + f"\n\nHuman judge, over to you. Ask the agents anything, then: `@{self.name} verdict {suggestion} [score] [note]` "
            f"(choices: {', '.join(self.rubric.verdicts)}).",
        )

    async def _record_verdict(self, case: Case, judge: str, verdict: dict[str, Any], message_id: str | None) -> None:
        verdict["judge"] = judge
        verdict["at"] = time.time()
        verdict["provisional"] = case.provisional
        case.verdict = verdict
        case.status = "decided"
        case.updated_at = time.time()
        final = case.final
        rank = next((i for i, c in enumerate(self.state.ranked(self.rubric.verdicts), 1) if c.key == case.key), None)
        override = verdict.get("score") is not None and case.provisional is not None and abs(verdict["score"] - case.provisional) >= 0.05
        await self.agent.post(
            case.room_id or "",
            f"🔨 **Verdict recorded for {case.label}: {verdict['decision'].upper()}**"
            + (f", {final:g}/100" if final is not None else "")
            + (f" (judge override of the panel's {case.provisional:g})" if override else "")
            + f", by {judge}."
            + (f" Note: {verdict['note']}" if verdict.get("note") else "")
            + (f" Now #{rank} on the floor." if rank else ""),
            message_id,
        )
        await self.refresh_floor(
            note=f"🔨 **{case.label}** `{case.owner}/{case.repo}` → **{verdict['decision'].upper()}**"
            + (f" ({final:g}/100)" if final is not None else "")
            + f" by {judge}"
        )
        if self.cfg.share_feedback and case.intake_message_id and not case.extra.get("feedback_sent"):
            await self.agent.post(self.state.rooms["intake"], self.feedback_md(case), case.intake_message_id)
            case.extra["feedback_sent"] = True
        self._changed()

    # ── explanations ───────────────────────────────────────────────────────

    def explain_md(self, case: Case) -> str:
        """How the score was computed, judge by judge, so nobody has to trust a number."""
        lines = [f"**How {case.label} got its score** · {self.rubric.name}"]
        means = scoring.criterion_means(case.scorecards, self.rubric)
        covered = 0.0
        for c in self.rubric.criteria:
            parts = [f"{j} {card['scores'][c.id]:g}" for j, card in case.scorecards.items() if c.id in card.get("scores", {})]
            if c.id in means:
                covered += c.weight
                pts = c.weight * means[c.id] / 10
                lines.append(f"• **{c.label}** ({c.weight:g}%): mean of {', '.join(parts)} = **{means[c.id]:.1f}**/10 → {pts:.1f} pts")
            else:
                lines.append(f"• **{c.label}** ({c.weight:g}%): no scorecard yet")
        if case.provisional is not None:
            raw = sum(c.weight * means[c.id] / 10 for c in self.rubric.criteria if c.id in means)
            if covered < 100:
                lines.append(
                    f"Covered {covered:g}% of the rubric: {raw:.1f} pts scaled to 100 = **{case.provisional:g}/100** (provisional)"
                )
            else:
                lines.append(f"Panel total: **{case.provisional:g}/100**")
        if case.verdict:
            v = case.verdict
            lines.append(
                f"Human verdict: **{v['decision'].upper()}** by {v.get('judge', '?')}"
                + (f", score set to {v['score']:g}" if v.get("score") is not None else ", panel score kept")
                + (f". Note: {v['note']}" if v.get("note") else "")
            )
        if case.red_flags:
            lines.append("Red flags: " + "; ".join(case.red_flags[:5]))
        return "\n".join(lines)

    def calibration_md(self) -> str:
        """How each AI judge's scores compare with the humans' final scores."""
        diffs: dict[str, list[float]] = {}
        for case in self.state.cases.values():
            v = case.verdict or {}
            human = v.get("score")
            if human is None:
                continue
            for judge, card in case.scorecards.items():
                own = scoring.provisional_score({judge: card}, self.rubric)[0]
                if own is not None:
                    diffs.setdefault(judge, []).append(own - float(human))
        if not diffs:
            return "No calibration yet: it needs verdicts that carry a human score (`verdict shortlist 88 …`)."
        lines = ["**Judge calibration** · each AI judge's own score vs the human's final score"]
        for judge, d in sorted(diffs.items(), key=lambda kv: -abs(sum(kv[1]) / len(kv[1]))):
            mean = sum(d) / len(d)
            spread = (sum((x - mean) ** 2 for x in d) / len(d)) ** 0.5
            lean = "about right" if abs(mean) < 3 else ("too generous" if mean > 0 else "too harsh")
            lines.append(f"• **{judge}**: {mean:+.1f} pts on average over {len(d)} case(s), ±{spread:.1f}: {lean}")
        return "\n".join(lines)

    def feedback_md(self, case: Case) -> str:
        """What the participant gets back: the judges' reasons, not just a number."""
        v = case.verdict or {}
        lines = [f"📬 **Feedback on {case.label} `{case.owner}/{case.repo}`** from the Jury bench"]
        if case.final is not None:
            lines.append(f"Score: **{case.final:g}/100** · {v.get('decision', '').upper()}")
        for judge, card in case.scorecards.items():
            note = card.get("summary") or "; ".join(card.get("notes", {}).values())
            if note:
                lines.append(f"• **{judge}**: {note[:300]}")
        if case.red_flags:
            lines.append("Worth fixing: " + "; ".join(case.red_flags[:4]))
        if v.get("note"):
            lines.append(f"From the human judge: “{v['note']}”")
        lines.append("Reply `appeal <why>` in this thread if you think something was missed.")
        return "\n".join(lines)

    # ── commands ───────────────────────────────────────────────────────────

    async def _command(self, room_id: str, p: dict[str, Any], body: str, case: Case | None) -> None:
        text = re.sub(rf"@{re.escape(self.name)}\b", "", body, flags=re.IGNORECASE).strip().lower()
        thread = p.get("message_id")
        if text.startswith(("my queue", "queue")):
            who = p.get("on_behalf_of") or p.get("sender_name") or ""
            m = re.search(r"queue\s+@?(\S+)", text)
            if m and m.group(1) not in ("for", "me"):
                who = m.group(1)
            await self.agent.post(room_id, self.queue_md(who), thread)
        elif m := re.match(r"assign\s+(?:case\s*)?#?(\d+)\s+(?:to\s+)?@?(\S+)", text):
            c = self.state.by_number(int(m.group(1)))
            if not c:
                await self.agent.post(room_id, f"No case #{m.group(1)} on file.", thread)
            else:
                c.extra["assignee"] = m.group(2)
                await self.agent.post(room_id, f"👤 {c.label} is now with @{m.group(2)}.", thread)
                if c.room_id and c.room_id != room_id:
                    await self.agent.post(c.room_id, f"👤 @{m.group(2)} is now the human judge on this case.")
                self._changed()
        elif text.startswith(("explain", "why")):
            m = re.search(r"#?(\d+)", text)
            c = self.state.by_number(int(m.group(1))) if m else case
            await self.agent.post(room_id, self.explain_md(c) if c else "Which case? `explain 7`, or ask in its room.", thread)
        elif text.startswith(("calibration", "calibrate", "bias")):
            await self.agent.post(room_id, self.calibration_md(), thread)
        elif text.startswith(("feedback",)) and case:
            await self.agent.post(room_id, self.feedback_md(case), thread)
        elif text.startswith(("close intake", "lock intake")):
            self.state.intake_closed = True
            await self.agent.post(self.state.rooms["intake"], "🚪 **Intake is closed.** New links won't be filed. Judging continues.")
            if room_id != self.state.rooms["intake"]:
                await self.agent.post(room_id, "Intake closed.", thread)
        elif text.startswith(("open intake", "reopen intake", "unlock intake")):
            self.state.intake_closed = False
            await self.agent.post(self.state.rooms["intake"], "🚪 **Intake is open again.** Paste a repo link to file it.")
            if room_id != self.state.rooms["intake"]:
                await self.agent.post(room_id, "Intake open.", thread)
        elif text.startswith(("stats", "metrics")):
            await self.agent.post(room_id, self.stats_md(), thread)
        elif text.startswith(("leaderboard", "board", "rank", "top")):
            await self.agent.post(room_id, self.leaderboard_md(limit=15), thread)
        elif text.startswith("status"):
            if case:
                await self.agent.post(room_id, self.case_md(case), thread)
            else:
                await self.agent.post(room_id, self.status_md(), thread)
        elif m := re.match(r"case\s*#?(\d+)", text):
            c = self.state.by_number(int(m.group(1)))
            await self.agent.post(room_id, self.case_md(c) if c else f"No case #{m.group(1)} on file.", thread)
        elif text.startswith(("rescore", "recount")) and case:
            case.provisional, case.complete = scoring.provisional_score(case.scorecards, self.rubric)
            await self.agent.post(room_id, f"Recounted: **{case.provisional}/100** from {len(case.scorecards)} scorecard(s).", thread)
            await self.refresh_floor()
        elif text.startswith(("reaudit", "re-audit")) and case:
            await self.agent.mention(case.room_id or "", [self.auditor_name], f"audit {case.url}")
        elif text.startswith(("summon", "call")) and case:
            added = await self.summon(case)
            await self.agent.post(
                room_id,
                f"📣 Summoned {', '.join(added)} to this case." if added else "📣 Every judge on the bench is already in this case.",
                thread,
            )
        elif text.startswith(("rescan", "re-scan")) and case:
            found = await self.rescan(case)
            if found:
                await self.agent.post(room_id, f"🔁 Re-read this room: picked up {', '.join(found)}.", thread)
            else:
                await self.agent.post(room_id, "🔁 Re-read this room: no scorecards I hadn't already counted.", thread)
        else:
            await self.agent.post(
                room_id,
                HELP.format(intake=self.cfg.intake_room, clerk=self.name, verdicts=" / ".join(self.rubric.verdicts)),
                thread,
            )

    async def summon(self, case: Case) -> list[str]:
        """Bring judges into an open case that it was opened without (a judge
        created or re-created later), and kick them off there."""
        judges = await self.present_judges()
        expected = case.extra.setdefault("judges", [self.auditor_name])
        try:
            present = {p.get("name") for p in (await self.agent.room_op(case.room_id or "", "list_participants") or [])}
        except SwitchError:
            present = set(expected)
        added = []
        for j in judges:
            # In the room, expected and already scored: nothing to do. A judge
            # deleted and re-created under the same name is in the room and
            # expected but has never scored, so it gets the kickoff again.
            if j in present and j in expected and j in case.scorecards:
                continue
            if j not in present:
                try:
                    await self.agent.op("invite_agent_to_room", {"room_id": case.room_id, "agent_name": j})
                except SwitchError as e:
                    if e.status not in (400, 409):
                        log.warning("invite %s: %s", j, e)
                        continue
            if j not in expected:
                expected.append(j)
            added.append(j)
        if added:
            if case.status == "scored":
                case.status = "open"
            await self.agent.mention(
                case.room_id or "",
                added,
                f"⚖️ You're on the bench for **{case.label}: {case.url}**. Read the **Submission** and **Rubric** documents "
                "in this room, review the repo, and file one scorecard for the criteria you own, in a ```jury block.",
            )
            await self.refresh_floor()
            self._changed()
        return added

    async def rescan(self, case: Case) -> list[str]:
        """Re-read the case room and count every judge's latest scorecard.

        For scorecards that arrived in a shape an older Clerk couldn't parse,
        or while Clerk was down. Threads included; newest per judge wins.
        """
        res = await self.agent.op("read_context", {"room_id": case.room_id, "limit": 200})
        latest: dict[str, tuple[int, dict[str, Any], str | None]] = {}
        for t in (res or {}).get("threads", []):
            for m in [t.get("root") or {}, *(t.get("replies") or [])]:
                sender = m.get("on_behalf_of") or m.get("sender_name") or ""
                if sender == self.name or not await self._is_agent(sender):
                    continue
                card = scoring.parse_scorecard(m.get("body") or "", self.rubric)
                if not card:
                    continue
                ts = int(m.get("timestamp") or 0)
                if sender not in latest or ts >= latest[sender][0]:
                    latest[sender] = (ts, card, m.get("id"))
        picked = []
        for judge, (_, card, mid) in latest.items():
            have = case.scorecards.get(judge)
            if have and have.get("message_id") == mid:
                continue
            await self._record_scorecard(case, judge, card, mid)
            picked.append(judge)
        return picked

    def _label(self, cid: str) -> str:
        c = self.rubric.get(cid)
        return c.label if c else cid

    def _next_assignee(self) -> str | None:
        """Deal cases to the human judges in turn, skipping whoever holds the most open ones."""
        judges = list(self.cfg.judge_users)
        if not judges:
            return None
        load = {j: 0 for j in judges}
        for c in self.state.cases.values():
            a = c.extra.get("assignee")
            if a in load and c.status != "decided":
                load[a] += 1
        n = len(self.state.cases)
        return min(judges, key=lambda j: (load[j], (judges.index(j) - n) % len(judges)))

    async def _appeal(self, case: Case, p: dict[str, Any], body: str) -> None:
        who = p.get("on_behalf_of") or p.get("sender_name") or "someone"
        text = re.sub(r"^\s*@\S+\s*", "", body).strip()[:600]
        history = case.extra.setdefault("appeals", [])
        history.append({"by": who, "text": text, "at": time.time(), "previous": case.verdict})
        if case.verdict:
            case.status = "scored"  # back to awaiting a human verdict; the old one is kept in history
            case.verdict = None
        assignee = case.extra.get("assignee")
        people = f"@{assignee}" if assignee else " ".join(f"@{u}" for u in self.cfg.judge_users)
        await self.agent.post(
            self.state.rooms["intake"], f"📮 Appeal for **{case.label}** logged. A human judge will take another look.", p.get("message_id")
        )
        if case.room_id:
            await self.agent.post(
                case.room_id,
                f"{people + ' ' if people else ''}📮 **Appeal from {who}:** {text}\n"
                + (f"The earlier verdict ({history[-1]['previous']['decision'].upper()}) is set aside. " if history[-1]["previous"] else "")
                + f"Judges, weigh the appeal here; then a human gives the verdict again with `@{self.name} verdict …`.",
            )
        await self.refresh_floor(note=f"📮 Appeal on **{case.label}** `{case.owner}/{case.repo}` by {who}")
        self._changed()

    def queue_md(self, who: str) -> str:
        mine = [c for c in self.state.cases.values() if c.extra.get("assignee") == who and c.status != "decided"]
        if not mine:
            return f"@{who} has nothing waiting. 🎉"
        mine.sort(key=lambda c: (c.status != "scored", c.number))
        rows = [f"**@{who}'s queue** ({len(mine)})"]
        for c in mine:
            state = "ready for your verdict" if c.status == "scored" else "judges still working"
            rows.append(f"• {c.label} `{c.owner}/{c.repo}` · {state}" + (f" · #{c.room_name}" if c.room_name else ""))
        return "\n".join(rows)

    def stats_md(self) -> str:
        cases = list(self.state.cases.values())
        passes = sorted(c.extra["first_pass_s"] for c in cases if c.extra.get("first_pass_s") is not None)
        flagged = sum(1 for c in cases if c.red_flags)
        copies = sum(1 for c in cases if any(f.startswith("Possible copy") for f in c.red_flags))
        overrides = sum(
            1
            for c in cases
            if c.verdict
            and c.verdict.get("score") is not None
            and c.provisional is not None
            and abs(c.verdict["score"] - c.provisional) >= 5
        )
        lines = [f"**Jury stats** · {len(cases)} case(s)"]
        if passes:
            med = passes[len(passes) // 2]
            lines.append(
                f"• first pass (filed → every scorecard in): median **{med // 60}m {med % 60:02d}s**, fastest {passes[0] // 60}m {passes[0] % 60:02d}s"
            )
        lines.append(f"• {flagged} case(s) with red flags, {copies} possible copies")
        lines.append(f"• {sum(1 for c in cases if c.verdict)} verdict(s), {overrides} where the human moved the panel's score by 5+")
        lines.append(f"• {sum(len(c.extra.get('appeals', [])) for c in cases)} appeal(s)")
        return "\n".join(lines)

    def status_md(self) -> str:
        cases = list(self.state.cases.values())
        by = {s: sum(1 for c in cases if c.status == s) for s in ("open", "scored", "decided")}
        return (
            f"**Jury status**: {len(cases)} case(s): {by['open']} being judged, {by['scored']} awaiting a verdict, {by['decided']} decided."
        )

    def case_md(self, case: Case) -> str:
        lines = [f"**{case.label}** `{case.owner}/{case.repo}` · {case.status}" + (f" · #{case.room_name}" if case.room_name else "")]
        for judge, card in case.scorecards.items():
            lines.append(
                f"• {judge}: "
                + ", ".join(f"{self._label(k)} {v:g}" for k, v in card["scores"].items())
                + (f" | {card['summary'][:160]}" if card.get("summary") else "")
            )
        if case.provisional is not None:
            lines.append(f"Provisional: **{case.provisional:g}/100**")
        if case.verdict:
            lines.append(
                f"Verdict: **{case.verdict['decision'].upper()}** by {case.verdict['judge']}"
                + (f" ({case.final:g}/100)" if case.final is not None else "")
            )
        return "\n".join(lines)

    def leaderboard_md(self, limit: int = 50) -> str:
        ranked = self.state.ranked(self.rubric.verdicts)
        if not ranked:
            return "The floor is empty. Paste a repo link in #" + self.cfg.intake_room + "."
        lines = [f"**🏛️ Jury leaderboard · {self.rubric.name}**", "```"]
        lines.append(f"{'#':>2}  {'case':<8} {'repo':<34} {'score':>6}  status")
        for i, c in enumerate(ranked[:limit], 1):
            sc = f"{c.final:g}" if c.final is not None else "-"
            st = c.verdict["decision"].upper() if c.verdict else c.status
            name = f"{c.owner}/{c.repo}"
            lines.append(f"{i:>2}  #{c.number:03d}    {name[:34]:<34} {sc:>6}  {st}")
        lines.append("```")
        return "\n".join(lines)

    async def refresh_floor(self, note: str | None = None, *, now: bool = False) -> None:
        """Post ``note`` on the floor and rewrite the Leaderboard document.

        Switch announces every document edit in the room, so the rewrite is
        debounced: many scorecards in a burst become one edit.
        """
        floor = self.state.rooms.get("floor")
        if not floor:
            return
        if note:
            await self.agent.post(floor, note)
        if now:
            await self._write_leaderboard_doc(floor)
            return
        if self._doc_task and not self._doc_task.done():
            self._doc_dirty = True
            return
        self._doc_task = asyncio.create_task(self._debounced_doc(floor))

    async def _debounced_doc(self, floor: str) -> None:
        while True:
            self._doc_dirty = False
            await asyncio.sleep(self.doc_debounce)
            if not self._doc_dirty:
                break
        await self._write_leaderboard_doc(floor)

    async def _write_leaderboard_doc(self, floor: str) -> None:
        content = self.leaderboard_md() + "\n\nFull detail: " + (self.cfg.public_url or f"http://{self.cfg.web_host}:{self.cfg.web_port}")
        digest = hashlib.sha256(content.encode()).hexdigest()
        if self.state.floor_doc_id and digest == self.state.floor_doc_hash:
            return  # nothing changed: no edit, no "updated room document" notice
        try:
            if self.state.floor_doc_id:
                await self.agent.room_op(floor, "update_room_document", {"document_id": self.state.floor_doc_id, "content": content})
                self.state.floor_doc_hash = digest
            else:
                res = await self.agent.room_op(
                    floor,
                    "create_room_document",
                    {
                        "name": "Leaderboard",
                        "description": "Every Jury case ranked by score, kept current by the clerk",
                        "instructions": "Read-only. The clerk rewrites it after every scorecard and verdict.",
                        "content": content,
                    },
                )
                self.state.floor_doc_id = (res or {}).get("id") or (res or {}).get("document_id")
                self.state.floor_doc_hash = digest
            self.state.save()
        except SwitchError as e:
            if e.status == 404 and self.state.floor_doc_id:
                self.state.floor_doc_id = None  # someone deleted it in the UI; recreate next time
            log.warning("leaderboard doc: %s", e)

    def _changed(self) -> None:
        for cb in self.on_change:
            try:
                cb()
            except Exception:  # pragma: no cover
                log.exception("change hook")
