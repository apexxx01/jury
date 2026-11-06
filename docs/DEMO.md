# Demo video script (about 2:45)

Judges score Presentation on "do the demo video and README make clear how Switch is used?", so every beat
shows a Switch feature doing real work. Record the Mattermost window in Chrome at full size, at 1080p or more.
Cut every wait and put a small "⏩ 3 minutes later" card where you cut.

## Before you hit record

- `jury up` is running (`git pull` first).
- **Case #001 (Jury judging itself) is fully scored but has NO verdict yet.** You give it live in the video.
- `docker compose -f repro/compose.yaml down -v` has been run, so the repro clip starts clean.
- Have this in your clipboard: `https://github.com/pallets/click`. It's a great library with no Switch in
  it, so the bench has something to catch.
- Close other tabs. Turn on Do Not Disturb.

## The script

**0:00–0:15 · Hook** (on `jury-intake`)
> "Seventy-five hundred people registered for this hackathon. Before anyone judges anything, someone has to
> clone every repo, check it runs and find the video. Jury does that first pass inside the team chat, on
> Switch, and leaves the call to a human."

**0:15–0:35 · Intake → a room is born**
Paste the click link in `jury-intake`. Clerk replies in the thread, and `case-002-click` appears in the
sidebar. Point at it.
> "Clerk is an agent I wrote directly on Switch's protocol. It just built this room from a template, with its
> own instructions, the rubric and the submission as room documents, linked to the judging floor."

**0:35–0:55 · The Auditor catches it**
Open `case-002-click`. Show the Auditor's report: *"No trace of Switch in the repository."* Open the room's
documents.
> "The Auditor uses no LLM. It clones the repo and checks facts: Switch wiring, setup steps, whether the
> commits fall inside the hackathon window, and whether anyone committed an API key."

**0:55–1:35 · Jury judging itself** (cut to `case-001-jury`; this is the heart of the video)
Scroll through it slowly:
1. **The Inspector's first message:** it refused to run code it had just cloned until a human approved.
   > "Inspector is Claude Code. Switch Console's auto mode stopped it from running untrusted code, so it
   > asked a person, right here in the channel."
2. **Your approval reply**, then **its real run**: `38 passed`, ruff clean.
3. **The bug it found:** "`jury up` is unsafe on this server… could knock out the live Clerk."
   > "And it found a real bug in Jury. A second copy would hijack the live Clerk. I fixed it, and the commit
   > credits the Inspector."
4. **Clerk's tally lines** after each scorecard, and `✅ All scorecards are in`.

**1:35–1:55 · The human decides** (live)
In `case-001-jury` type:
`@jury-clerk verdict shortlist 90 judged itself, found its own bug, and still passed`
Show Clerk's verdict line with the rank.
> "Agents gather evidence and argue. Only a human gives the verdict."

**1:55–2:15 · The floor**
`jury-floor` shows the verdict post. Then the web leaderboard at `localhost:8787`: Jury shortlisted, and click
being judged with its red flag.

**2:15–2:30 · Anyone can reproduce it** (terminal)
Show `docker compose -f repro/compose.yaml up --build --abort-on-container-exit --exit-code-from smoke`
finishing with `✓ the whole loop works`. Speed the clip up.
> "One command runs the whole loop on a fresh Switch server. No keys, no setup."

**2:30–2:45 · Why Switch** (README's "How Switch is used" table on screen)
> "Rooms from templates, room documents, links, addressing, Claude Code and two protocol agents in one
> channel, and people in the loop. Take Switch away and Jury is a script with nobody to talk to. Swap one
> rubric file and it reviews hiring take-homes instead. Jury: every submission gets a room, a human gets the
> verdict."

## After

Upload to YouTube (unlisted is fine). Replace `DEMO_VIDEO_URL` at the top of the README with the link and
push. That also clears the Auditor's video flag when Jury judges itself.
