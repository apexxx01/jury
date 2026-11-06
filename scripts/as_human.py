"""Post into a room as a human Gateway user (for local tests without a chat app).

    uv run python scripts/as_human.py <room id> "message" [--email E --password P]

Defaults to the seeded admin from the Switch server's .env
(GATEWAY_ADMIN_EMAIL / GATEWAY_ADMIN_PASSWORD).
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jury import config


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("room_id")
    p.add_argument("body")
    p.add_argument("--email", default=os.environ.get("GATEWAY_ADMIN_EMAIL", "admin@switch.local"))
    p.add_argument("--password", default=os.environ.get("GATEWAY_ADMIN_PASSWORD"))
    a = p.parse_args()
    base = config.load().switch_url
    with httpx.Client(base_url=base, timeout=20) as c:
        r = c.post("/gateway/auth/login", json={"email": a.email, "password": a.password})
        r.raise_for_status()
        me = r.json().get("id") or r.json().get("user", {}).get("id")
        r = c.post(f"/gateway/chats/{a.room_id}/messages", json={"body": a.body, "request_id": str(uuid.uuid4())})
        if r.status_code == 403 and "NOT_A_MEMBER" in r.text and me:
            # a manager may add themselves to a room they manage
            c.post(f"/gateway/chats/{a.room_id}/members", json={"user_id": me})
        else:
            print(r.status_code, r.text[:300])
            return
        r = c.post(f"/gateway/chats/{a.room_id}/messages", json={"body": a.body, "request_id": str(uuid.uuid4())})
        print(r.status_code, r.text[:300])


if __name__ == "__main__":
    main()
