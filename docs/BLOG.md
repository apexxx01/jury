---
title: "I built a hackathon judging floor in Discord on Switch, and wrote my first agent from scratch on its protocol"
tags: switch, ai, agents, hackathon
---

# I built a hackathon judging floor in Discord on Switch

*Flip the Switch hackathon, Nov 2026 · repo: https://github.com/apexxx01/jury*

Flip the Switch had 7,516 people registered. Somebody has to open every submission, clone it, check whether
it runs and find the demo video, before any real judging starts. At five minutes a repo that's days of work,
and it's the most mechanical part of the job.

So I built **Jury**. You paste a repo link in a Discord channel. A Switch agent opens a dedicated room for that
submission, three AI judges gather evidence and argue in it, and a human gives the verdict. This post covers
what I built and, more usefully if you're new to Switch, **what I learned writing an agent directly against
the Switch protocol**. Most of it isn't hard, but a few things will cost you an hour if nobody tells you.

## What Switch is, in one paragraph

Switch turns each channel in your team chat (Slack, Discord, Teams, Mattermost, Telegram) into a **room**: a
shared log of messages, documents, roles and links that every person and agent in the channel reads from.
Agents join rooms like teammates, under their own name and avatar. Switch Console, the desktop app, runs your
coding agents (Claude Code, Codex, OpenCode…) and a local Switch server in Docker. Anything else can join by
speaking the **agent protocol**: server-sent events down, HTTP up.

## The design: one room per submission

The first idea was "a bot that scores repos". That's the wrong shape for Switch. The right shape is **a room
per case**, because a room gives you exactly what a judging panel needs:

- **shared context**: the Inspector's error output is right there when the Critic argues and when the
  human decides
- **documents**: the rubric and the submission travel with the room, so every agent reads the same brief
- **addressing**: agents wake when they're mentioned, so the clerk can hand work to a specific judge
- **links**: every case room points at the judging floor and back at intake, so agents can find their way

The bench:

| Agent | Runs on | Job |
|---|---|---|
| Clerk | my Python, on the raw protocol | opens case rooms, tallies scorecards, raises disagreements, records verdicts |
| Auditor | my Python, on the raw protocol | clones the repo and reports facts: Switch wiring, setup steps, tests, video, commit window, leaked secrets |
| Inspector | Claude Code via Console | actually installs and runs the submission from the README |
| Critic | OpenCode via Console | judges the idea, the impact, and whether Switch is central |

Two different models on purpose: a bench of one model agreeing with itself isn't a panel.

## Building it: the bits worth knowing

### 1. Let Switch Console hand you the keys

To register a custom agent you need the server's **registration token**. On a Mac running Console's local
server it's sitting in:

```
~/Library/Application Support/switchdash/local-switch-server/.env
```

with `AGENT_REGISTRATION_TOKEN` and `API_HOST_PORT` in it. Jury reads that file, so `uv run jury up` works on
any machine running Console with no configuration at all. (`%APPDATA%\switchdash\…` on Windows,
`~/.config/switchdash/…` on Linux.)

### 2. Registering an always-on agent

```python
r = httpx.post(f"{base}/agents", headers={"Authorization": f"Bearer {registration_token}"}, json={
    "name": "jury-clerk", "display_name": "Clerk", "description": "Runs the judging floor",
    "connector_type": "jury",
    "integration_profile": {"connection_model": "always_on", "message_exchange": True,
                            "pre_invocation_mediation": [], "post_invocation_mediation": [],
                            "event_reporting": []},
})
agent_id, api_key = r.json()["id"], r.json()["api_key"]   # the key comes back ONCE: store it
```

The registration token only registers. Everything after that uses the agent's own API key.

### 3. The stream: send `Accept: text/event-stream`

This one cost me twenty minutes. `GET /agents/{id}/events` **without** `Accept: text/event-stream` answers
with a one-shot JSON poll (`{"events": [...]}`) and closes. Your heartbeat then gets a 404, *"connection … is
not open; reconnect"*, and it looks like the server is broken. Send the header and you get a proper stream
whose first frame is `connection_state`.

Two more stream rules:

- **You choose the connection id** (a UUID), and you **reuse it across reconnects**. That's what lets a
  connection survive a dropped socket. Resume with `Last-Event-ID`.
- **Read the heartbeat interval from `connection_state`**, don't hard-code it. The server I ran announced
  `heartbeat_interval_seconds: 10`. A 404 or 409 from `/connection/beat` means *reopen the stream*, not back off.

### 4. Room-bound operations need a room claim

Operations live at `POST /agents/{id}/ops/{name}`, with the arguments object as the body. Many of them act on
"the connected room", which the server resolves from your connection. So you call `connect_to_room` first and
send `X-Switch-Connection-Id` on the next call. `list_participants`, for example, rejects a `room_id` argument
outright. With one connection juggling many rooms, I put a lock around *claim, then call* so two rooms never
interleave.

### 5. Rooms from YAML are the superpower

Every case room is generated:

```python
doc = {"room": {"name": "case-007-yourrepo", "description": "...", "instructions": CASE_RULES,
                "agents": ["jury-clerk", "jury-auditor", "jury-inspector", "jury-critic"],
                "users": ["{$creator}"],
                "docs": [{"name": "Rubric", ...}, {"name": "Submission", ...}]},
       "kickoff": "@jury-inspector @jury-critic Case #007 is open: ..."}
await clerk.op("create_room_from_yaml", {"yaml": yaml.safe_dump(doc)})
```

That one call creates a Discord channel, puts the right agents and me in it, attaches the documents and posts
the kickoff. Two things to know:

- `description` is required on a room.
- `users:` needs a messaging app behind the room. On a server with no app connected the run is refused with a
  clear message. Jury retries without `users` so the same code works on a bare server.

### 6. A kickoff speaks for the agent, so open addressing

When an *agent* runs a template, the kickoff is posted with the agent's authority, not its owner's. The agents
it mentions wake only if their addressing policy lets that agent in, and the server checks this **before
creating anything**. My judges use `addressing: anyone` in their template. For my own two protocol agents I
call `update_agent_detail(addressing="anyone")` on each other: an agent can't change its own settings, but it
can change a sibling with the same owner.

### 7. Document edits are announced

Every `update_room_document` posts a little "📄 updated room document" notice in the channel. That's great for
transparency and terrible if you rewrite a leaderboard after every scorecard. Jury debounces: a burst of
scorecards becomes one edit.

### 8. Publish your template where people will click it

Agent templates are YAML too. Mine creates the Inspector and the Critic on two different providers, plus a
`#jury-chambers` room where they introduce themselves. Before shipping it I ran it through Switch's own
linter (`switch_core.template_lint.lint_template`): 0 errors, 0 warnings. Then `jury up` calls `save_template`,
so it shows up in Console's **Templates** page. The person setting up Jury never touches a YAML file.

## Making judges argue

The feature I like most is small. Each judge files a scorecard as a fenced JSON block in the room. When two
judges are four or more points apart on a criterion, Clerk addresses both of them:

> ⚔️ You disagree on **Use of Switch**: jury-auditor says 0, jury-critic says 9. Compare your evidence here,
> then each file a revised scorecard or say why you're keeping yours.

The Auditor is deterministic, and when it's argued with it says so: arguments don't move its scores, evidence
in the repo does. The LLM judges have to bring receipts. By the time a human opens the room the argument has
already happened, with evidence, in the same thread.

## Reproducing it without any LLM

Judges will clone this repo, so it had to run without my laptop's Claude Code and OpenCode logins. There are scripted
stand-in judges (`scripts/stand_in_judges.py`) and a smoke test (`scripts/smoke.py`) that drive the whole loop
against any Switch server: intake, case room, three scorecards, a human verdict posted through the Gateway,
leaderboard. Then it checks the result. Plus 37 unit tests, including the protocol client against a fake Switch server.

## What I'd tell a first-time Switch builder

1. **Start from the room, not the bot.** Ask what a room per *thing* gives you: context, docs, links, the right
   people. That's where Switch earns its place.
2. **Read `connection_state`** and trust it over your assumptions.
3. **Templates beat code** for anything a person sets up. Lint them with Switch's own linter.
4. **Humans decide.** Agents gather evidence. Switch is built for that split.

Jury is Apache-2.0. Swap `rubrics/flip-the-switch.yaml` for `rubrics/take-home.yaml` and it reviews hiring
take-homes. If you run a hackathon, I'd love to hear what it gets wrong.

#HowISwitch
