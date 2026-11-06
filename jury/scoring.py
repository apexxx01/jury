"""Rubrics, scorecards and the arithmetic of a verdict.

Pure functions only: everything here is unit-tested without a server.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Criterion:
    id: str
    label: str
    weight: float
    question: str
    judges: list[str]
    guide: dict[Any, str] = field(default_factory=dict)


@dataclass
class Rubric:
    name: str
    criteria: list[Criterion]
    verdicts: list[str]
    shortlist_at: float = 70
    event_url: str | None = None

    @property
    def ids(self) -> list[str]:
        return [c.id for c in self.criteria]

    def get(self, cid: str) -> Criterion | None:
        return next((c for c in self.criteria if c.id == cid), None)

    def criteria_for(self, judge: str) -> list[Criterion]:
        return [c for c in self.criteria if judge in c.judges]

    def to_markdown(self) -> str:
        lines = [f"# Rubric: {self.name}", ""]
        if self.event_url:
            lines += [f"Source: {self.event_url}", ""]
        lines += ["| Criterion | Weight | Question | Scored by |", "|---|---|---|---|"]
        for c in self.criteria:
            lines.append(f"| `{c.id}` {c.label} | {c.weight:g}% | {c.question} | {', '.join(c.judges)} |")
        lines.append("")
        for c in self.criteria:
            lines.append(f"## {c.label} (`{c.id}`)")
            for k, v in c.guide.items():
                lines.append(f"- **{k}**: {v}")
            lines.append("")
        lines += [
            f"Verdicts a human judge can give: {', '.join(self.verdicts)}.",
            "",
            SCORECARD_HELP,
        ]
        return "\n".join(lines)


SCORECARD_HELP = """## How to file a scorecard

Post ONE message in this room ending with a fenced block tagged `jury`:

```jury
{"scores": {"<criterion id>": 0-10, ...},
 "notes": {"<criterion id>": "one line of evidence", ...},
 "summary": "two sentences a busy judge can read",
 "red_flags": ["anything that breaks a rule or blocks a run"]}
```

Score only the criteria you are listed against. Post a new block to revise;
the latest one counts."""


def load_rubric(path: Path) -> Rubric:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    crits = [
        Criterion(
            id=c["id"],
            label=c.get("label", c["id"]),
            weight=float(c["weight"]),
            question=c.get("question", ""),
            judges=list(c.get("judges", [])),
            guide=dict(c.get("guide", {})),
        )
        for c in raw["criteria"]
    ]
    total = sum(c.weight for c in crits)
    if abs(total - 100) > 0.01:
        raise ValueError(f"rubric weights in {path} add up to {total:g}, not 100")
    return Rubric(
        name=raw.get("name", path.stem),
        criteria=crits,
        verdicts=[v.lower() for v in raw.get("verdicts", ["shortlist", "maybe", "pass"])],
        shortlist_at=float(raw.get("shortlist_at", 70)),
        event_url=raw.get("event_url"),
    )


# ── scorecards ──────────────────────────────────────────────────────────────

_FENCE = re.compile(r"```[ \t]*(jury|json)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_NUM = re.compile(r"(-?\d+(?:\.\d+)?)")


def _to_score(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    elif isinstance(v, str):
        m = _NUM.search(v)
        if not m:
            return None
        x = float(m.group(1))
        if "/100" in v:
            x /= 10
    elif isinstance(v, dict) and "score" in v:
        return _to_score(v["score"])
    else:
        return None
    return max(0.0, min(10.0, x))


def parse_scorecard(body: str, rubric: Rubric) -> dict[str, Any] | None:
    """The last ```jury block in a message, normalised, or None.

    A ```json block counts too when it has a "scores" object, since models
    sometimes ignore the tag.
    """
    found = None
    for tag, inner in _FENCE.findall(body or ""):
        text = inner.strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            # tolerate trailing commas, the usual LLM slip
            try:
                obj = json.loads(re.sub(r",\s*([}\]])", r"\1", text))
            except json.JSONDecodeError:
                continue
        if not isinstance(obj, dict):
            continue
        if tag.lower() != "jury" and "scores" not in obj:
            continue
        found = obj
    if found is None:
        return None
    raw_scores = found.get("scores") or {}
    scores: dict[str, float] = {}
    if isinstance(raw_scores, dict):
        for k, v in raw_scores.items():
            key = str(k).strip().lower().replace(" ", "_")
            if rubric.get(key) is None:
                # accept labels as well as ids ("Use of Switch")
                match = next((c for c in rubric.criteria if c.label.lower().replace(" ", "_") == key), None)
                if match is None:
                    continue
                key = match.id
            s = _to_score(v)
            if s is not None:
                scores[key] = s
    if not scores:
        return None
    notes = found.get("notes") if isinstance(found.get("notes"), dict) else {}
    flags = found.get("red_flags") or []
    if isinstance(flags, str):
        flags = [flags]
    return {
        "scores": scores,
        "notes": {str(k): str(v) for k, v in notes.items()},
        "summary": str(found.get("summary", "")).strip(),
        "red_flags": [str(f) for f in flags if str(f).strip()],
        "runs": found.get("runs"),
    }


def criterion_means(scorecards: dict[str, dict[str, Any]], rubric: Rubric) -> dict[str, float]:
    sums: dict[str, list[float]] = {}
    for card in scorecards.values():
        for cid, s in card.get("scores", {}).items():
            if rubric.get(cid):
                sums.setdefault(cid, []).append(float(s))
    return {cid: sum(v) / len(v) for cid, v in sums.items() if v}


def provisional_score(scorecards: dict[str, dict[str, Any]], rubric: Rubric) -> tuple[float | None, bool]:
    """(score out of 100, complete?). Missing criteria are renormalised away."""
    means = criterion_means(scorecards, rubric)
    if not means:
        return None, False
    covered = [c for c in rubric.criteria if c.id in means]
    weight = sum(c.weight for c in covered)
    score = sum(c.weight * means[c.id] / 10 for c in covered) * 100 / weight
    return round(score, 1), len(covered) == len(rubric.criteria)


def disagreements(scorecards: dict[str, dict[str, Any]], rubric: Rubric, gap: float) -> list[tuple[str, str, float, str, float]]:
    """(criterion, judge a, score a, judge b, score b) where two judges are ``gap`` apart."""
    out = []
    for c in rubric.criteria:
        scored = [(j, card["scores"][c.id]) for j, card in scorecards.items() if c.id in card.get("scores", {})]
        if len(scored) < 2:
            continue
        lo = min(scored, key=lambda t: t[1])
        hi = max(scored, key=lambda t: t[1])
        if hi[1] - lo[1] >= gap:
            out.append((c.id, lo[0], lo[1], hi[0], hi[1]))
    return out


# ── verdicts ────────────────────────────────────────────────────────────────

_ALIASES = {
    "yes": 0,
    "accept": 0,
    "advance": 0,
    "shortlisted": 0,
    "ship": 0,
    "maybe": 1,
    "hold": 1,
    "borderline": 1,
    "no": 2,
    "reject": 2,
    "decline": 2,
    "pass": 2,
}
_VERDICT = re.compile(
    r"\bverdict\b\s*[:=\-–—]?\s*([a-z]+)(?:\s+(\d{1,3}(?:\.\d+)?)(?:\s*/\s*100)?)?\s*[-–—:,]?\s*(.*)",
    re.IGNORECASE | re.DOTALL,
)


def parse_verdict(body: str, rubric: Rubric) -> dict[str, Any] | None:
    m = _VERDICT.search(body or "")
    if not m:
        return None
    word = m.group(1).lower()
    if word in rubric.verdicts:
        decision = word
    elif word in _ALIASES and len(rubric.verdicts) >= 3:
        decision = rubric.verdicts[_ALIASES[word]]
    else:
        return None
    score = float(m.group(2)) if m.group(2) else None
    if score is not None:
        score = max(0.0, min(100.0, score))
    return {"decision": decision, "score": score, "note": m.group(3).strip()[:500]}


def bar(score: float | None, width: int = 10) -> str:
    if score is None:
        return "·" * width
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)
