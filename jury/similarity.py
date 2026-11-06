"""Copy detection across submissions.

Two cheap, explainable signals, kept per case in ``.jury/fingerprints.json``:

* **shared history**: two repos that contain the same commit came from the same
  base (a fork, a shared starter, one repo pushed twice)
* **shared code**: the share of one repo's meaningful code lines that also
  appear in another. Lines that show up in many submissions (imports, framework
  boilerplate, code copied from the official examples by everyone) are dropped
  before comparing, so a common starter doesn't make everyone look like a copy.

It flags; a human decides. Two teams building on the same official example will
share some lines, and the message says how many, against which case.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

CODE_EXT = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
    ".swift",
    ".dart",
    ".vue",
    ".svelte",
    ".c",
    ".cpp",
    ".h",
    ".sh",
    ".yaml",
    ".yml",
}
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__", ".next", "target"}
SKIP_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", "poetry.lock", "go.sum", "cargo.lock"}
MIN_LINE = 24  # shorter lines are mostly syntax (`}`, `return x`) and say nothing
TRIVIAL = re.compile(r"^(import |from \S+ import |#|//|/\*|\*|@|export \{|require\(|use |package |\}|\]|\))")
MAX_LINES = 40_000

FLAG_SHARED_CODE = 0.40  # share of this repo's distinctive lines found in another one
COMMON_IN = 3  # a line in this many other repos is boilerplate, not evidence


def _norm(line: str) -> str | None:
    s = re.sub(r"\s+", " ", line.strip())
    if len(s) < MIN_LINE or TRIVIAL.match(s):
        return None
    return s


def fingerprint_tree(root: Path) -> list[str]:
    """Short hashes of the repo's meaningful code lines (deduplicated)."""
    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            p = Path(dirpath) / f
            if p.suffix.lower() not in CODE_EXT or f.lower() in SKIP_NAMES:
                continue
            try:
                if p.stat().st_size > 300_000:
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                n = _norm(line)
                if n:
                    seen.add(hashlib.blake2b(n.encode(), digest_size=8).hexdigest())
                    if len(seen) >= MAX_LINES:
                        return sorted(seen)
    return sorted(seen)


@dataclass
class Match:
    other: str  # case key ("owner/repo")
    shared_code: float  # share of THIS repo's distinctive lines also in the other
    shared_lines: int
    shared_commits: int

    def describe(self, label: str) -> str:
        parts = []
        if self.shared_commits:
            parts.append(f"shares {self.shared_commits} commit(s) of git history")
        if self.shared_code >= FLAG_SHARED_CODE:
            parts.append(f"{self.shared_code:.0%} of its distinctive code lines ({self.shared_lines}) also appear")
        return f"Possible copy: {' and '.join(parts)} with {label}. A human should compare them."


class FingerprintIndex:
    """Every audited case's line hashes and commit ids, persisted as JSON."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, dict[str, list[str]]] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".fp-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def put(self, key: str, lines: list[str], commits: list[str]) -> None:
        self.data[key.lower()] = {"lines": lines, "commits": commits}
        self.save()

    def drop(self, key: str) -> None:
        if self.data.pop(key.lower(), None) is not None:
            self.save()

    def compare(self, key: str) -> list[Match]:
        """Other cases that look like copies of ``key``, worst first."""
        key = key.lower()
        me = self.data.get(key)
        if not me:
            return []
        others = {k: v for k, v in self.data.items() if k != key}
        if not others:
            return []
        # Boilerplate: lines that many *other* submissions also contain.
        counts: Counter[str] = Counter()
        for v in others.values():
            counts.update(set(v["lines"]))
        mine = {h for h in me["lines"] if counts[h] < COMMON_IN}
        my_commits = set(me.get("commits", []))
        out = []
        for k, v in others.items():
            theirs = set(v["lines"])
            shared = len(mine & theirs)
            ratio = shared / len(mine) if mine else 0.0
            commits = len(my_commits & set(v.get("commits", [])))
            if commits or (ratio >= FLAG_SHARED_CODE and shared >= 30):
                out.append(Match(other=k, shared_code=ratio, shared_lines=shared, shared_commits=commits))
        return sorted(out, key=lambda m: (-m.shared_commits, -m.shared_code))
