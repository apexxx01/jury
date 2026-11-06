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
• Paste a GitHub repo link in **#{intake}** and I open a case room for it.
• In a case room, judges post scorecards; I tally them and flag disagreements.
• A human judge decides: `@{clerk} verdict shortlist 88 great use of rooms` (verdicts: {verdicts}; the score is optional).
• `@{clerk} leaderboard` · `@{clerk} status` · `@{clerk} case 7` · `@{clerk} rescore` (in a case room) · `@{clerk} help`"""

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
        self.doc_debounce = 20.0

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
        """create_room_from_yaml, trying with the configured bridge and people first."""
        attempts = []
        full = dict(room)
        if self.cfg.bridge:
            full["bridge"] = self.cfg.bridge
        users = ["{$creator}", *self.cfg.judge_users]
        if users:
            full["users"] = users
        attempts.append(full)
        if users:
            attempts.append({k: v for k, v in full.items() if k != "users"})
        if self.cfg.bridge:
            attempts.append({k: v for k, v in attempts[-1].items() if k != "bridge"})
        last: Exception | None = None
        for i, body in enumerate(attempts):
            doc: dict[str, Any] = {"room": body}
            if kickoff:
                doc["kickoff"] = kickoff
            try:
                return await self.agent.op("create_room_from_yaml", {"yaml": yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)})
            except SwitchError as e:
                last = e
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
        submitter = p.get("on_behalf_of") or p.get("sender_name") or "someone"
        case = Case(number=self.state.next_number(), owner=owner, repo=repo, submitter=submitter, intake_message_id=thread)
        self.state.cases[key] = case
        self.state.save()
        judges = await self.present_judges()
        case.extra["judges"] = [self.auditor_name, *judges]
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
            return
        if self._addressed_to_me(p, body) or re.match(r"\s*verdict\b", body, re.IGNORECASE):
            verdict = scoring.parse_verdict(body, self.rubric)
            if verdict:
                await self._record_verdict(case, sender, verdict, p.get("message_id"))
                return
            if self._addressed_to_me(p, body):
                await self._command(case.room_id or "", p, body, case)

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
            await self._ask_for_verdict(case)
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
        people = " ".join(f"@{u}" for u in self.cfg.judge_users)
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
        rank = next((i for i, c in enumerate(self.state.ranked(), 1) if c.key == case.key), None)
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
        self._changed()

    # ── commands ───────────────────────────────────────────────────────────

    async def _command(self, room_id: str, p: dict[str, Any], body: str, case: Case | None) -> None:
        text = re.sub(rf"@{re.escape(self.name)}\b", "", body, flags=re.IGNORECASE).strip().lower()
        thread = p.get("message_id")
        if text.startswith(("leaderboard", "board", "rank", "top")):
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
        else:
            await self.agent.post(
                room_id,
                HELP.format(intake=self.cfg.intake_room, clerk=self.name, verdicts=" / ".join(self.rubric.verdicts)),
                thread,
            )

    def _label(self, cid: str) -> str:
        c = self.rubric.get(cid)
        return c.label if c else cid

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
        ranked = self.state.ranked()
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
        try:
            if self.state.floor_doc_id:
                await self.agent.room_op(floor, "update_room_document", {"document_id": self.state.floor_doc_id, "content": content})
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
