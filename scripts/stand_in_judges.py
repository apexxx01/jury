"""Scripted stand-ins for the LLM judges, for testing Jury without Claude Code or Codex.

Registers ``jury-inspector`` and ``jury-critic`` as plain protocol agents on a
Switch server and answers every case kickoff with a canned scorecard. When
Clerk raises a disagreement, the inspector revises its score, so the whole
loop (kickoff → scorecards → disagreement → revision → verdict) can be
exercised end to end.

    uv run python scripts/stand_in_judges.py

Needs SWITCH_URL and SWITCH_REGISTRATION_TOKEN (or Switch Console's local
server on this machine), like Jury itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jury import config
from jury.state import State, find_repos
from jury.switch import Credentials, Event, SwitchAgent, SwitchError, register_agent

log = logging.getLogger("stand-in")

CARDS = {
    "jury-inspector": {
        "scores": {"technical_execution": 4, "creativity_impact": 7},
        "notes": {
            "technical_execution": "README setup failed on a clean clone: missing env var (stand-in).",
            "creativity_impact": "Solves a real review bottleneck (stand-in).",
        },
        "summary": "Stand-in inspector: ran into one setup snag; core idea is solid.",
        "red_flags": [],
    },
    "jury-critic": {
        "scores": {"use_of_switch": 9, "creativity_impact": 8, "presentation": 7},
        "notes": {"use_of_switch": "Rooms, links and docs carry the workflow (stand-in)."},
        "summary": "Stand-in critic: Switch is central; presentation could show the flow sooner.",
        "red_flags": [],
    },
}


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    cfg = config.load()
    state = State.load(cfg.state_dir / "stand-ins.json")
    agents: list[SwitchAgent] = []
    for name in CARDS:
        saved = state.agents.get(name)
        if not saved:
            try:
                creds = await register_agent(
                    cfg.switch_url,
                    cfg.registration_token or "",
                    name=name,
                    display_name=name.split("-")[1].title(),
                    description="stand-in judge",
                )
            except SwitchError as e:
                if e.status != 409:
                    raise
                creds = await register_agent(
                    cfg.switch_url,
                    cfg.registration_token or "",
                    name=name,
                    display_name=name.split("-")[1].title(),
                    description="stand-in judge",
                    overwrite=True,
                )
            state.agents[name] = creds.to_json()
            state.save()
        agents.append(SwitchAgent(cfg.switch_url, Credentials(**state.agents[name])))

    # anyone may address them, as the real template sets
    for a, b in ((agents[0], agents[1]), (agents[1], agents[0])):
        try:
            await a.op("update_agent_detail", {"agent_id": b.agent_id, "addressing": "anyone"})
        except SwitchError as e:
            log.warning("addressing: %s", e)

    def handler_for(agent: SwitchAgent):
        revised: set[str] = set()
        seen: set[str] = set()

        async def handle(ev: Event) -> None:
            if ev.kind != "message" or not ev.room_id:
                return
            mid = ev.payload.get("message_id") or ""
            if mid in seen:
                return
            seen.add(mid)
            body = ev.payload.get("body") or ""
            if not re.search(rf"@{agent.name}\b", body):
                return
            if find_repos(body) and "is open" in body:
                await asyncio.sleep(random.uniform(2, 4))
                card = CARDS[agent.name]
                await agent.post(ev.room_id, f"{card['summary']}\n\n```jury\n{json.dumps(card)}\n```")
            elif "disagree" in body and agent.name == "jury-inspector" and ev.room_id not in revised:
                revised.add(ev.room_id)
                await asyncio.sleep(2)
                card = json.loads(json.dumps(CARDS[agent.name]))
                card["scores"]["technical_execution"] = 6
                card["summary"] = "Revised after comparing with the auditor: the snag is one missing line in .env.example."
                await agent.post(
                    ev.room_id, f"@jury-auditor fair point, the setup gap is minor. Revising.\n\n```jury\n{json.dumps(card)}\n```"
                )

        return handle

    await asyncio.gather(*(a.run(handler_for(a)) for a in agents))


if __name__ == "__main__":
    asyncio.run(main())
