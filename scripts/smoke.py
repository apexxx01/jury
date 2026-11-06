"""End-to-end smoke test against a live Switch server.

Files a submission, waits for every judge's scorecard, gives a human verdict
through the Gateway, and checks the case lands decided on the leaderboard.

    # terminal 1                 # terminal 2                              # terminal 3
    uv run jury up               uv run python scripts/stand_in_judges.py  uv run python scripts/smoke.py --password <gateway admin password>

Exit code 0 means the whole loop works.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jury import config  # noqa: E402


def state() -> dict:
    cfg = config.load()
    return json.loads((cfg.state_dir / "state.json").read_text())


def wait(pred, what: str, timeout: float) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            s = state()
            if (hit := pred(s)) is not None:
                print(f"  ✓ {what} ({time.time() - t0:.0f}s)")
                return hit
        except (OSError, ValueError):
            pass
        time.sleep(2)
    print(f"  ✗ timed out waiting for: {what}")
    sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default="https://github.com/jqlang/jq")
    p.add_argument("--email", default=os.environ.get("GATEWAY_ADMIN_EMAIL", "admin@switch.local"))
    p.add_argument("--password", default=os.environ.get("GATEWAY_ADMIN_PASSWORD"))
    p.add_argument("--timeout", type=float, default=240)
    a = p.parse_args()
    owner, repo = a.repo.rstrip("/").split("/")[-2:]
    key = f"{owner}/{repo}".lower()
    print(f"Jury smoke test · {a.repo}")
    if key in state().get("cases", {}):
        sys.exit(f"  {key} already has a case; pass --repo with a fresh one")

    subprocess.run([sys.executable, "-m", "jury", "submit", a.repo, "--pace", "0"], check=True, cwd=ROOT)
    case = wait(lambda s: s["cases"].get(key) if s["cases"].get(key, {}).get("room_id") else None, "case room opened", 60)
    print(f"    room #{case['room_name']}")
    wait(lambda s: s["cases"][key] if "jury-auditor" in s["cases"][key]["scorecards"] else None, "auditor scorecard filed", a.timeout)
    case = wait(
        lambda s: s["cases"][key] if s["cases"][key]["status"] in ("scored", "decided") else None, "every judge's scorecard in", a.timeout
    )
    print(f"    provisional {case['provisional']}/100 from {', '.join(case['scorecards'])}")

    base = config.load().switch_url
    with httpx.Client(base_url=base, timeout=20) as c:
        r = c.post("/gateway/auth/login", json={"email": a.email, "password": a.password})
        r.raise_for_status()
        me = r.json().get("id")
        c.post(f"/gateway/chats/{case['room_id']}/members", json={"user_id": me})
        r = c.post(
            f"/gateway/chats/{case['room_id']}/messages",
            json={"body": "@jury-clerk verdict maybe 61 smoke test", "request_id": os.urandom(8).hex()},
        )
        r.raise_for_status()
    case = wait(lambda s: s["cases"][key] if s["cases"][key]["status"] == "decided" else None, "human verdict recorded", 60)
    assert case["verdict"]["decision"] == "maybe" and case["verdict"]["score"] == 61, case["verdict"]
    print("  ✓ the whole loop works")


if __name__ == "__main__":
    main()
