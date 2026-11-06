"""The Auditor as a Switch agent: woken by an addressed message, answers in the room."""

from __future__ import annotations

import asyncio
import json
import logging
import re

from . import auditor
from .config import Config
from .scoring import Rubric
from .similarity import FingerprintIndex, Match
from .state import find_repos
from .switch import Event, SwitchAgent, SwitchError

log = logging.getLogger("jury.auditor")


class AuditorAgent:
    """Wakes on an addressed message with a repo link, audits it, files a scorecard."""

    def __init__(self, cfg: Config, rubric: Rubric, agent: SwitchAgent) -> None:
        self.cfg = cfg
        self.rubric = rubric
        self.agent = agent
        self._sem = asyncio.Semaphore(2)
        self._seen: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._last: dict[str, tuple[str, str]] = {}
        self._reports: dict[str, str] = {}
        self.lookup = None  # room id -> Case, set by the runner
        self.case_by_key = None  # "owner/repo" -> Case, set by the runner
        self.index = FingerprintIndex(cfg.state_dir / "fingerprints.json")

    @property
    def name(self) -> str:
        return self.agent.name

    async def handle(self, ev: Event) -> None:
        if ev.kind != "message":
            return
        p = ev.payload
        if (p.get("sender_name") == self.name) or not ev.room_id:
            return
        body = p.get("body") or ""
        if not re.search(rf"@{re.escape(self.name)}\b", body, re.IGNORECASE):
            return
        mid = p.get("message_id") or ""
        if mid in self._seen:
            return
        self._seen.add(mid)
        repos = find_repos(body)
        last = self._last.get(ev.room_id)
        if last is None and self.lookup and (case := self.lookup(ev.room_id)) is not None:
            last = (case.owner, case.repo)
        if not repos and last and re.search(r"\b(re-?audit|recheck|again)\b", body, re.IGNORECASE):
            repos = [last]
        if not repos:
            if last and re.search(r"\bevidence\b", body, re.IGNORECASE) and ev.room_id in self._reports:
                await self.agent.post(ev.room_id, self._reports[ev.room_id][:3800], mid)
            elif last:
                # In a debate: hold the line, point at the evidence.
                await self.agent.post(
                    ev.room_id,
                    "I'm the deterministic judge: arguments don't move my scores, evidence in the repo does. "
                    f"Every fact I checked is in the room document **Auditor report {last[0]}/{last[1]}**. "
                    f"Say `@{self.name} evidence` to see it here, or `@{self.name} reaudit` after the repo changes.",
                    mid,
                )
            else:
                await self.agent.post(
                    ev.room_id, f"Give me a GitHub repo link and I'll audit it: `@{self.name} audit https://github.com/owner/repo`", mid
                )
            return
        owner, repo = repos[0]
        self._last[ev.room_id] = (owner, repo)
        t = asyncio.create_task(self._audit(ev.room_id, owner, repo, mid))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _audit(self, room_id: str, owner: str, repo: str, thread: str | None) -> None:
        async with self._sem:
            await self.agent.post(room_id, f"🔎 Cloning `{owner}/{repo}` and auditing…", thread)
            try:
                rep = await auditor.audit(owner, repo, self.cfg.workdir, self.cfg.window_start, self.cfg.window_end, self.cfg.clone_timeout)
            except Exception as e:  # never leave the room hanging
                log.exception("audit failed")
                await self.agent.post(room_id, f"⚠️ Audit of `{owner}/{repo}` crashed: {e}", thread)
                return
            matches = self._copy_check(owner, repo, rep) if rep.ok else []
            owned = {c.id for c in self.rubric.criteria_for(self.name)}
            scores = {k: v for k, v in rep.scores.items() if k in owned}
            notes = {k: v for k, v in rep.notes.items() if k in owned}
            card = {
                "scores": scores,
                "notes": notes,
                "summary": (
                    f"Deterministic audit of {owner}/{repo}@{rep.head}. "
                    + ("Clean on secrets. " if not rep.secrets else f"{len(rep.secrets)} possible secret(s). ")
                    + (
                        f"{rep.commits.get('during', 0)}/{rep.commits.get('total', 0)} commits in the hackathon window."
                        if rep.commits.get("total")
                        else ""
                    )
                ).strip(),
                "red_flags": rep.red_flags,
            }
            if not rep.ok:
                # A repo the judges can't clone can't be reproduced: say so on the record.
                card = {
                    "scores": {k: 0 for k in owned},
                    "notes": {k: rep.error or "clone failed" for k in owned},
                    "summary": f"Could not clone {owner}/{repo}: private, renamed or missing.",
                    "red_flags": rep.red_flags,
                }
                scores = card["scores"]
            text = auditor.render(rep)
            await self._warn_other_rooms(owner, repo, matches)
            if scores:
                text += "\n\n```jury\n" + json.dumps(card, ensure_ascii=False) + "\n```"
            await self.agent.post(room_id, text)
            self._reports[room_id] = auditor.render(rep, detailed=True)
            if rep.ok:
                try:
                    await self.agent.room_op(
                        room_id,
                        "create_room_document",
                        {
                            "name": f"Auditor report {owner}/{repo}",
                            "description": "Every fact the auditor checked, with file paths",
                            "instructions": "Evidence for the judges. Cite it when you score.",
                            "content": auditor.render(rep, detailed=True)[:60000],
                        },
                    )
                except SwitchError as e:
                    log.warning("auditor doc: %s", e)

    def _label_for(self, key: str) -> str:
        case = self.case_by_key(key) if self.case_by_key else None
        if case is None:
            return f"`{key}`"
        return f"**{case.label}** `{case.owner}/{case.repo}`" + (f" (#{case.room_name})" if case.room_name else "")

    def _copy_check(self, owner: str, repo: str, rep: auditor.Report) -> list[Match]:
        key = f"{owner}/{repo}".lower()
        self.index.put(key, rep.fingerprint, rep.commit_ids)
        matches = self.index.compare(key)
        for m in matches[:3]:
            msg = m.describe(self._label_for(m.other))
            rep.copies.append(msg)
            rep.red_flags.append(msg)
        return matches

    async def _warn_other_rooms(self, owner: str, repo: str, matches: list[Match]) -> None:
        """Tell the earlier case's room too, so its judges see it without looking."""
        here = self._label_for(f"{owner}/{repo}".lower())
        for m in matches[:3]:
            other = self.case_by_key(m.other) if self.case_by_key else None
            if other is None or not other.room_id:
                continue
            try:
                await self.agent.post(other.room_id, f"⚠️ Copy check: {here} overlaps with this case. {m.describe('this one')}")
            except SwitchError as e:
                log.warning("copy warning to %s: %s", other.room_id, e)
