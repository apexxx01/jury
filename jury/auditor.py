"""The Auditor: a deterministic judge.

It clones the submission and collects facts a model should not be trusted to
guess: whether the README has setup steps and a demo video, whether Switch is
wired in (templates, protocol calls, room features), whether the commits fall
inside the hackathon window, and whether anyone committed a secret.

Its scores are evidence-based heuristics. They are a floor for the human
judge and the LLM judges to argue with, not the last word.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .similarity import fingerprint_tree

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__", ".next", "target", ".turbo"}
TEXT_EXT = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
    ".swift",
    ".sh",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".md",
    ".mdx",
    ".txt",
    ".env",
    ".ini",
    ".cfg",
    ".html",
    ".vue",
    ".svelte",
    ".dart",
    ".ex",
    ".exs",
    ".c",
    ".cpp",
    ".h",
}
LANG = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".swift": "Swift",
    ".dart": "Dart",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".ex": "Elixir",
    ".c": "C",
    ".cpp": "C++",
}
MAX_FILES = 6000
MAX_BYTES = 400_000

SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{30,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("Discord bot token", re.compile(r"\b[MN][A-Za-z\d]{23,25}\.[\w-]{6}\.[\w-]{27,38}\b")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
]
PLACEHOLDER = re.compile(r"x{6,}|your[_-]|example|placeholder|changeme|<[^>]+>|\*{4,}|dummy|fake|test", re.IGNORECASE)

VIDEO = re.compile(
    r"(youtube\.com/watch|youtu\.be/|loom\.com/share|vimeo\.com/|drive\.google\.com|github\.com/user-attachments/assets|\.mp4\b|\.mov\b)",
    re.IGNORECASE,
)
INSTALL = re.compile(
    r"^\s*(?:\$\s*)?(pip|pipx|uv|poetry|npm|npx|pnpm|yarn|bun|docker|docker-compose|cargo|go|make|just|brew|python3?|node|deno)\b",
    re.MULTILINE,
)
PROTOCOL = re.compile(
    r"/agents/[^\s\"'`]*?/(?:events|ops|connection/beat|message)\b|\b(?:connect_to_room|post_message|send_targeted_message|"
    r"create_room_from_yaml|read_context|create_room_document|run_template|link_rooms|invite_agent_to_room)\b"
)
SWITCH_WORD = re.compile(r"\bswitch(?:agents\.ai| console|-core|_core)?\b|sandbox-quantum/switch", re.IGNORECASE)


@dataclass
class Report:
    owner: str
    repo: str
    ok: bool = True
    error: str | None = None
    head: str | None = None
    files: int = 0
    languages: list[tuple[str, int]] = field(default_factory=list)
    readme: dict[str, Any] = field(default_factory=dict)
    switch: dict[str, Any] = field(default_factory=dict)
    repro: dict[str, Any] = field(default_factory=dict)
    commits: dict[str, Any] = field(default_factory=dict)
    secrets: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: list[str] = field(default_factory=list, repr=False)
    commit_ids: list[str] = field(default_factory=list, repr=False)
    copies: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    red_flags: list[str] = field(default_factory=list)


async def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return 124, "timed out"
    return proc.returncode or 0, out.decode(errors="replace")


async def clone(url: str, dest: Path, timeout: int) -> tuple[bool, str]:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    code, out = await _run(
        ["git", "clone", "--depth", "300", "--no-tags", "--single-branch", "--filter=blob:limit=1m", url, str(dest)],
        timeout=timeout,
    )
    if code != 0:  # older git or a server without partial clone
        code, out = await _run(["git", "clone", "--depth", "300", "--no-tags", url, str(dest)], timeout=timeout)
    return code == 0, out.strip().splitlines()[-1] if out.strip() else ""


def _walk(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            out.append(Path(dirpath) / f)
            if len(out) >= MAX_FILES:
                return out
    return out


def _read(p: Path) -> str:
    try:
        if p.stat().st_size > MAX_BYTES:
            return ""
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _readme(root: Path) -> Path | None:
    for name in ("README.md", "readme.md", "Readme.md", "README.MD", "README.mdx", "README.rst", "README.txt", "README"):
        if (root / name).exists():
            return root / name
    return None


def analyze_tree(root: Path, owner: str, repo: str) -> Report:
    rep = Report(owner=owner, repo=repo)
    files = _walk(root)
    rel = [f.relative_to(root) for f in files]
    rep.files = len(files)
    langs = Counter(LANG[f.suffix] for f in files if f.suffix in LANG)
    rep.languages = langs.most_common(4)

    # README
    rp = _readme(root)
    text = _read(rp) if rp else ""
    words = len(re.findall(r"\w+", text))
    headings = re.findall(r"^#{1,4}\s+(.+)$", text, re.MULTILINE)
    rep.readme = {
        "present": rp is not None,
        "words": words,
        "headings": len(headings),
        "video": bool(VIDEO.search(text)),
        "images": len(re.findall(r"!\[[^\]]*\]\(|<img\s", text)),
        "diagram": "```mermaid" in text or bool(re.search(r"\.(svg|png|gif)\)", text)),
        "setup_commands": len(INSTALL.findall("\n".join(re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)))),
        "switch_mentions": len(SWITCH_WORD.findall(text)),
        "switch_section": any(SWITCH_WORD.search(h) for h in headings),
    }

    # Switch evidence and secrets, one pass over text files
    templates, protocol_files, features = [], set(), Counter()
    for f, r in zip(files, rel, strict=True):
        name = f.name.lower()
        if f.suffix not in TEXT_EXT and not name.startswith(".env"):
            continue
        body = _read(f)
        if not body:
            continue
        if f.suffix in (".yaml", ".yml"):
            top = set(re.findall(r"^([a-z_]+):", body, re.MULTILINE))
            if name.endswith(".template.yaml") or ("kickoff" in top) or ({"room", "agents"} <= top) or ({"group", "rooms"} <= top):
                templates.append(str(r))
                if "links" in top:
                    features["room links"] += 1
                if re.search(r"^\s+roles:", body, re.MULTILINE) or "roles" in top:
                    features["roles"] += 1
                if re.search(r"^\s+docs:", body, re.MULTILINE):
                    features["room docs"] += 1
                if "kickoff" in top or re.search(r"^\s+kickoff:", body, re.MULTILINE):
                    features["kickoff"] += 1
                if len(re.findall(r"^\s*-\s*name:", body, re.MULTILINE)) >= 2 and "agents" in top:
                    features["multi-agent team"] += 1
        if f.suffix not in (".md", ".mdx", ".txt") and PROTOCOL.search(body):
            protocol_files.add(str(r))
            for word, feat in (
                ("link_rooms", "room links"),
                ("create_room_document", "room docs"),
                ("define_role", "roles"),
                ("create_room_from_yaml", "rooms from templates"),
                ("run_template", "rooms from templates"),
                ("send_targeted_message", "agent-to-agent handoff"),
                ("/events", "event stream"),
            ):
                if word in body:
                    features[feat] += 1
        # secrets: skip docs and obvious examples
        if name.endswith((".example", ".sample", ".md")) or "test" in str(r).lower():
            continue
        for label, pat in SECRET_PATTERNS:
            for m in pat.finditer(body):
                hit = m.group(0)
                if PLACEHOLDER.search(hit):
                    continue
                line = body.count("\n", 0, m.start()) + 1
                rep.secrets.append({"kind": label, "file": str(r), "line": line, "masked": hit[:4] + "…" + hit[-2:]})
                break
        if name == ".env" and re.search(r"^[A-Z_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*=\S{8,}", body, re.MULTILINE):
            rep.secrets.append({"kind": "committed .env", "file": str(r), "line": 1, "masked": "(values)"})
    rep.switch = {
        "templates": templates[:8],
        "protocol_files": sorted(protocol_files)[:8],
        "features": sorted(features),
        "mentions": rep.readme["switch_mentions"],
    }

    # Reproducibility signals
    names = {str(r).lower() for r in rel}
    base = {r.name.lower() for r in rel}
    manifests = [
        m
        for m in ("pyproject.toml", "requirements.txt", "package.json", "go.mod", "cargo.toml", "gemfile", "pom.xml", "build.gradle")
        if m in base
    ]
    locks = [
        m
        for m in ("uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "go.sum", "cargo.lock")
        if m in base
    ]
    tests = [str(r) for r in rel if re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test|\.test|\.spec|test_)[^/]*\.\w+$", str(r).lower())]
    rep.repro = {
        "manifests": manifests,
        "lockfiles": locks,
        "containers": [m for m in ("dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml") if m in base],
        "task_runner": [m for m in ("makefile", "justfile", "taskfile.yml") if m in base],
        "tests": len(tests),
        "ci": any(n.startswith(".github/workflows/") for n in names),
        "env_example": any(b in base for b in (".env.example", ".env.sample", "env.example")),
        "license": any(b.startswith(("license", "licence", "copying")) for b in base),
    }
    return rep


def score(rep: Report) -> None:
    r, s, x = rep.readme, rep.switch, rep.repro
    notes, flags = rep.notes, rep.red_flags

    # use_of_switch: evidence that Switch carries the workflow
    u = 0.0
    if s["mentions"]:
        u += 1.5
    if s["templates"]:
        u += 2.5
    if s["protocol_files"]:
        u += 2.5
    u += min(3.5, 0.7 * len(s["features"]))
    if r.get("switch_section"):
        u += 0.5
    rep.scores["use_of_switch"] = round(min(10.0, u), 1)
    ev = []
    if s["templates"]:
        ev.append(f"{len(s['templates'])} Switch template(s)")
    if s["protocol_files"]:
        ev.append(f"protocol calls in {len(s['protocol_files'])} file(s)")
    if s["features"]:
        ev.append("features: " + ", ".join(s["features"]))
    notes["use_of_switch"] = "; ".join(ev) or ("README mentions Switch, no wiring found" if s["mentions"] else "no Switch usage found")
    if not s["mentions"] and not s["templates"] and not s["protocol_files"]:
        flags.append("No trace of Switch in the repository (rule: Switch must be central).")

    # technical_execution: can a stranger reproduce it?
    t = 1.0
    t += 2.0 if r.get("setup_commands", 0) >= 2 else (1.0 if r.get("setup_commands") else 0)
    t += 1.5 if x["manifests"] else 0
    t += 1.0 if x["lockfiles"] else 0
    t += 1.0 if (x["containers"] or x["task_runner"]) else 0
    t += 1.5 if x["tests"] >= 3 else (0.75 if x["tests"] else 0)
    t += 1.0 if x["ci"] else 0
    t += 0.5 if x["env_example"] else 0
    t += 0.5 if x["license"] else 0
    if rep.secrets:
        t -= 3
    rep.scores["technical_execution"] = round(max(0.0, min(10.0, t)), 1)
    notes["technical_execution"] = (
        f"{r.get('setup_commands', 0)} setup commands in README, manifests {x['manifests'] or 'none'}, "
        f"{'lockfile' if x['lockfiles'] else 'no lockfile'}, {x['tests']} test files, CI {'yes' if x['ci'] else 'no'}"
    )

    # presentation: README and demo video
    p = 0.0
    p += 3.0 if r.get("words", 0) >= 800 else (2.0 if r.get("words", 0) >= 300 else (0.5 if r.get("present") else 0))
    p += 1.0 if r.get("headings", 0) >= 4 else 0
    p += 3.0 if r.get("video") else 0
    p += 1.5 if (r.get("images") or r.get("diagram")) else 0
    p += 1.5 if r.get("switch_section") else (0.5 if r.get("switch_mentions") else 0)
    rep.scores["presentation"] = round(min(10.0, p), 1)
    notes["presentation"] = (
        f"README {r.get('words', 0)} words, {r.get('headings', 0)} sections, "
        f"video {'found' if r.get('video') else 'missing'}, visuals {'yes' if (r.get('images') or r.get('diagram')) else 'no'}"
    )
    if not r.get("video"):
        flags.append("No demo video link in the README.")

    # take-home rubric criteria from the same facts
    rep.scores["testing"] = round(min(10.0, (4.0 if x["tests"] else 0) + min(4.0, x["tests"] * 0.5) + (2.0 if x["ci"] else 0)), 1)
    notes["testing"] = f"{x['tests']} test files, CI {'yes' if x['ci'] else 'no'}"
    rep.scores["communication"] = rep.scores["presentation"]
    notes["communication"] = notes["presentation"]

    for sct in rep.secrets[:5]:
        flags.append(f"Possible {sct['kind']} committed at `{sct['file']}:{sct['line']}` ({sct['masked']}). Rotate it.")


async def commit_window(root: Path, start: datetime, end: datetime) -> dict[str, Any]:
    code, out = await _run(["git", "log", "--format=%H|%aI|%an"], cwd=root, timeout=30)
    if code != 0:
        return {"error": out[:200]}
    before = during = after = 0
    authors: Counter[str] = Counter()
    first = last = None
    for line in out.strip().splitlines():
        try:
            _, iso, author = line.split("|", 2)
            when = datetime.fromisoformat(iso)
        except ValueError:
            continue
        authors[author] += 1
        first = when if first is None or when < first else first
        last = when if last is None or when > last else last
        if when < start:
            before += 1
        elif when > end:
            after += 1
        else:
            during += 1
    total = before + during + after
    return {
        "total": total,
        "before": before,
        "during": during,
        "after": after,
        "authors": len(authors),
        "first": first.isoformat() if first else None,
        "last": last.isoformat() if last else None,
        "shallow": total >= 300,
    }


async def audit(owner: str, repo: str, workdir: Path, start: datetime, end: datetime, timeout: int = 120) -> Report:
    url = f"https://github.com/{owner}/{repo}.git"
    dest = workdir / f"{owner}__{repo}".lower()
    ok, msg = await clone(url, dest, timeout)
    if not ok:
        rep = Report(owner=owner, repo=repo, ok=False, error=f"git clone failed: {msg}")
        rep.red_flags.append("The repository could not be cloned (private, renamed or missing?).")
        return rep
    rep = await asyncio.to_thread(analyze_tree, dest, owner, repo)
    code, head = await _run(["git", "rev-parse", "--short", "HEAD"], cwd=dest)
    rep.head = head.strip() if code == 0 else None
    rep.commits = await commit_window(dest, start, end)
    rep.fingerprint = await asyncio.to_thread(fingerprint_tree, dest)
    code, out = await _run(["git", "rev-list", "--max-count=300", "HEAD"], cwd=dest, timeout=30)
    rep.commit_ids = out.split() if code == 0 else []
    score(rep)
    c = rep.commits
    if c.get("total") and c.get("during", 0) == 0:
        rep.red_flags.append("No commits inside the hackathon window (rule: the Switch workflow must be built during the event).")
    elif c.get("after"):
        rep.red_flags.append(f"{c['after']} commit(s) after the submission deadline.")
    return rep


def render(rep: Report, *, detailed: bool = False) -> str:
    """Markdown for the room. The short form fits one chat message."""
    if not rep.ok:
        return f"**Auditor** could not open `{rep.owner}/{rep.repo}`: {rep.error}"
    r, s, x, c = rep.readme, rep.switch, rep.repro, rep.commits
    tick = lambda b: "✅" if b else "❌"
    lines = [
        f"**Auditor report: `{rep.owner}/{rep.repo}`** @ `{rep.head or '?'}`, {rep.files} files"
        + (f", {', '.join(n for n, _ in rep.languages)}" if rep.languages else ""),
        "",
        f"**Switch wiring** {tick(s['templates'] or s['protocol_files'])}: {rep.notes.get('use_of_switch')}",
        f"**Reproducible?** {tick(r.get('setup_commands') and x['manifests'])}: {rep.notes.get('technical_execution')}",
        f"**Presentation** {tick(r.get('video'))}: {rep.notes.get('presentation')}",
    ]
    if c.get("total"):
        lines.append(
            f"**Hackathon window**: {c['during']} of {c['total']} commits inside it"
            + (f", {c['before']} before" if c["before"] else "")
            + (f", {c['after']} after" if c["after"] else "")
            + f", {c['authors']} author(s)"
        )
    lines.append(f"**Secrets scan** {tick(not rep.secrets)}: " + (f"{len(rep.secrets)} possible secret(s)" if rep.secrets else "clean"))
    lines.append(f"**Copy check** {tick(not rep.copies)}: " + ("; ".join(rep.copies) if rep.copies else "no overlap with earlier cases"))
    if rep.red_flags:
        lines += ["", "🚩 " + "\n🚩 ".join(rep.red_flags)]
    if detailed:
        lines += ["", "### Evidence", ""]
        if s["templates"]:
            lines.append("Templates: " + ", ".join(f"`{t}`" for t in s["templates"]))
        if s["protocol_files"]:
            lines.append("Protocol calls: " + ", ".join(f"`{p}`" for p in s["protocol_files"]))
        lines.append(f"README: {r}")
        lines.append(f"Repro: {x}")
        lines.append(f"Commits: {c}")
        for sct in rep.secrets:
            lines.append(f"- {sct['kind']} at `{sct['file']}:{sct['line']}`")
    return "\n".join(lines)
