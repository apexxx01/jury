"""Configuration: environment first, then a local ``.env``, then Switch Console.

Switch Console's local server keeps its settings in a ``.env`` under the app's
data folder. When ``SWITCH_URL`` / ``SWITCH_REGISTRATION_TOKEN`` are not set,
Jury reads that file, so on a machine running Console there is nothing to copy.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CONSOLE_DIR_NAME = "switchdash"  # Console's userData folder name (stable forever)

# Flip the Switch: Nov 4 00:01 IST to Nov 7 23:59 IST
DEFAULT_WINDOW_START = "2026-11-04T00:01:00+05:30"
DEFAULT_WINDOW_END = "2026-11-07T23:59:00+05:30"


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ")
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def console_env_candidates() -> list[Path]:
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        base = [home / "Library" / "Application Support"]
    elif system == "Windows":
        base = [Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))]
    else:
        base = [Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))]
    return [b / CONSOLE_DIR_NAME / "local-switch-server" / ".env" for b in base]


def discover_console() -> tuple[str | None, str | None, Path | None]:
    """(url, registration token, file) from Switch Console's local server, if any."""
    for p in console_env_candidates():
        env = read_env_file(p)
        if env.get("AGENT_REGISTRATION_TOKEN"):
            port = env.get("API_HOST_PORT", "8000")
            bind = env.get("SWITCH_BIND_ADDR", "127.0.0.1")
            host = "127.0.0.1" if bind in ("", "0.0.0.0") else bind
            return f"http://{host}:{port}", env["AGENT_REGISTRATION_TOKEN"], p
    return None, None, None


def _split(v: str) -> list[str]:
    return [x.strip().lstrip("@") for x in v.split(",") if x.strip()]


@dataclass
class Config:
    switch_url: str
    registration_token: str | None
    source: str
    state_dir: Path
    rubric_path: Path
    bridge: str | None = None
    intake_room: str = "jury-intake"
    floor_room: str = "jury-floor"
    judges: list[str] = field(default_factory=lambda: ["jury-inspector", "jury-critic"])
    judge_users: list[str] = field(default_factory=list)
    web_host: str = "127.0.0.1"
    web_port: int = 8787
    window_start: datetime = datetime.fromisoformat(DEFAULT_WINDOW_START)
    window_end: datetime = datetime.fromisoformat(DEFAULT_WINDOW_END)
    clone_timeout: int = 120
    disagreement_gap: float = 4.0
    public_url: str | None = None

    @property
    def workdir(self) -> Path:
        return self.state_dir / "repos"


def load(cwd: Path | None = None) -> Config:
    cwd = cwd or Path.cwd()
    file_env = read_env_file(cwd / ".env")

    def get(key: str, default: str | None = None) -> str | None:
        return os.environ.get(key) or file_env.get(key) or default

    url = get("SWITCH_URL")
    token = get("SWITCH_REGISTRATION_TOKEN")
    source = "environment / .env"
    if not url or not token:
        c_url, c_token, c_file = discover_console()
        if c_url:
            url = url or c_url
            token = token or c_token
            source = f"Switch Console ({c_file})"
    url = (url or "http://127.0.0.1:8000").rstrip("/")

    pkg_root = Path(__file__).resolve().parent.parent
    rubric = Path(get("JURY_RUBRIC", str(pkg_root / "rubrics" / "flip-the-switch.yaml")))
    if not rubric.is_absolute():
        rubric = (cwd / rubric).resolve()

    cfg = Config(
        switch_url=url,
        registration_token=token,
        source=source,
        state_dir=Path(get("JURY_STATE_DIR", str(cwd / ".jury"))).expanduser(),
        rubric_path=rubric,
        bridge=get("JURY_BRIDGE") or None,
        intake_room=get("JURY_INTAKE_ROOM", "jury-intake") or "jury-intake",
        floor_room=get("JURY_FLOOR_ROOM", "jury-floor") or "jury-floor",
        judges=_split(get("JURY_JUDGES", "jury-inspector,jury-critic") or ""),
        judge_users=_split(get("JURY_JUDGE_USERS", "") or ""),
        web_host=get("JURY_WEB_HOST", "127.0.0.1") or "127.0.0.1",
        web_port=int(get("JURY_WEB_PORT", "8787") or 8787),
        clone_timeout=int(get("JURY_CLONE_TIMEOUT", "120") or 120),
        disagreement_gap=float(get("JURY_DISAGREEMENT_GAP", "4") or 4),
        public_url=get("JURY_PUBLIC_URL"),
    )
    if ws := get("JURY_WINDOW_START"):
        cfg.window_start = datetime.fromisoformat(ws)
    if we := get("JURY_WINDOW_END"):
        cfg.window_end = datetime.fromisoformat(we)
    return cfg
