"""Jury's own state: credentials, rooms and cases, in one JSON file.

Switch holds the conversation; this file holds the bookkeeping Switch has no
place for (which room is which case, the scorecards parsed so far). Writes
are atomic so a crash never leaves half a file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GITHUB_URL = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})", re.IGNORECASE)
NOT_REPOS = {"orgs", "settings", "features", "marketplace", "sponsors", "topics", "collections", "about", "login", "apps"}


def find_repos(text: str) -> list[tuple[str, str]]:
    """Unique (owner, repo) pairs in a message, in order.

    Tolerates whatever chat apps wrap a link in: markdown bold, angle
    brackets, trailing punctuation, ``.git``, deep links into the tree.
    """
    seen: list[tuple[str, str]] = []
    for owner, repo in GITHUB_URL.findall(text or ""):
        repo = re.sub(r"\.git$", "", repo.rstrip("."), flags=re.IGNORECASE).rstrip(".")
        if not repo or owner.lower() in NOT_REPOS:
            continue
        key = (owner, repo)
        if any(k[0].lower() == owner.lower() and k[1].lower() == repo.lower() for k in seen):
            continue
        seen.append(key)
    return seen


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].strip("-") or "case"


@dataclass
class Case:
    number: int
    owner: str
    repo: str
    submitter: str
    intake_message_id: str | None
    room_id: str | None = None
    room_name: str | None = None
    status: str = "open"  # open | scored | decided
    scorecards: dict[str, dict[str, Any]] = field(default_factory=dict)
    verdict: dict[str, Any] | None = None
    provisional: float | None = None
    complete: bool = False
    flagged: list[str] = field(default_factory=list)  # disagreements already raised
    red_flags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.owner}/{self.repo}".lower()

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def label(self) -> str:
        return f"Case #{self.number:03d}"

    @property
    def final(self) -> float | None:
        if self.verdict and self.verdict.get("score") is not None:
            return float(self.verdict["score"])
        return self.provisional


@dataclass
class State:
    path: Path
    agents: dict[str, dict[str, str]] = field(default_factory=dict)
    rooms: dict[str, str] = field(default_factory=dict)  # role -> room id
    floor_doc_id: str | None = None
    floor_doc_hash: str | None = None
    cases: dict[str, Case] = field(default_factory=dict)  # key -> case
    seen: list[str] = field(default_factory=list)  # message ids already handled
    intake_closed: bool = False

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        cases = {k: Case(**v) for k, v in raw.get("cases", {}).items()}
        return cls(
            path=path,
            agents=raw.get("agents", {}),
            rooms=raw.get("rooms", {}),
            floor_doc_id=raw.get("floor_doc_id"),
            floor_doc_hash=raw.get("floor_doc_hash"),
            intake_closed=bool(raw.get("intake_closed", False)),
            cases=cases,
            seen=raw.get("seen", [])[-2000:],
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "agents": self.agents,
            "rooms": self.rooms,
            "floor_doc_id": self.floor_doc_id,
            "floor_doc_hash": self.floor_doc_hash,
            "intake_closed": self.intake_closed,
            "cases": {k: asdict(v) for k, v in self.cases.items()},
            "seen": self.seen[-2000:],
        }
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)  # it holds API keys
        except OSError:
            pass

    def mark_seen(self, message_id: str | None) -> bool:
        """True the first time a message id is seen."""
        if not message_id:
            return True
        if message_id in self.seen:
            return False
        self.seen.append(message_id)
        return True

    def next_number(self) -> int:
        return max((c.number for c in self.cases.values()), default=0) + 1

    def by_room(self, room_id: str | None) -> Case | None:
        return next((c for c in self.cases.values() if c.room_id == room_id), None)

    def by_number(self, n: int) -> Case | None:
        return next((c for c in self.cases.values() if c.number == n), None)

    def ranked(self, verdicts: list[str] | None = None) -> list[Case]:
        """Best verdicts first, then undecided (full panels before partial), rejections last.

        With ``shortlist, maybe, pass``: shortlist, maybe, undecided, partial, pass.
        Within a tier, by score.
        """
        order = verdicts or ["shortlist", "maybe", "pass"]

        def tier(c: Case) -> int:
            if c.verdict:
                i = order.index(c.verdict["decision"]) if c.verdict.get("decision") in order else len(order) - 1
                return i if i < len(order) - 1 else len(order) + 1
            return len(order) - 1 if c.complete else len(order)

        return sorted(self.cases.values(), key=lambda c: (tier(c), -(c.final if c.final is not None else -1), c.number))
