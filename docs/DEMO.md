# Demo video script (about 2:45)

The judges score Presentation on "do the demo video and README make clear how Switch is used?", so every beat
below shows a Switch feature doing real work. Record at 1080p or higher: Discord on the left two-thirds of the
screen, a terminal or the web leaderboard on the right.

**Prep (before you hit record):**
- `jury up` is running, and the bench exists (see RUNBOOK).
- Pre-run **two** cases so the floor isn't empty, e.g. `uv run jury submit https://github.com/pallets/click`
  (a great library with no Switch in it, which gets red flags) and one strong Switch project.
- Have the repo you'll submit live in your clipboard: **this repo, Jury itself**.
- LLM judges take minutes. Record the waits and cut them. Put a "⏩ 4 minutes later" card where you cut.

---

**0:00–0:15 · Hook** (on the Discord intake channel)
> "This hackathon had seven and a half thousand people register. Someone has to clone every submission,
> check it runs, and find the video. That's a hundred-plus hours. Jury does the first pass inside Discord,
> on Switch, and leaves the call to a human."

**0:15–0:35 · Intake → a room is born**
Paste the Jury repo link in `#jury-intake`. Clerk replies in the thread and a new channel `#case-00N-jury`
appears in the sidebar. **Point at the new channel.**
> "Clerk is a custom agent I wrote on Switch's raw protocol. It built this room from a YAML template, with
> its own instructions and documents, and linked it to the judging floor."

**0:35–1:05 · The Auditor**
Open the case channel. Show the kickoff, then the Auditor's report: Switch wiring, setup steps, commit window,
secrets scan. Click the room's documents to show **Submission**, **Rubric** and **Auditor report**.
> "The Auditor is deterministic, no LLM. It clones the repo and checks hard facts, including whether the
> commits fall inside the hackathon window and whether anyone committed an API key."

**1:05–1:40 · The LLM judges, and the argument** *(cut the wait)*
Show the Inspector's run report (exact commands it ran) and the Critic's critique, each with a scorecard.
Then show Clerk's tally line, and **if Clerk raised a disagreement, linger on it** and on the judges'
replies to each other.
> "Inspector is Claude Code, and it actually ran the project. Critic is OpenCode, so it's a different agent on
> purpose. When they disagree, Clerk makes them settle it in the room, with evidence, before a human looks."

**1:40–2:05 · The human decides**
Ask one follow-up yourself: `@jury-inspector did the tests pass on a clean clone?` and show the reply (cut the
wait). Then type:
`@jury-clerk verdict shortlist 91 judged itself and still passed`
Show Clerk's verdict line with the rank.

**2:05–2:30 · The floor**
Switch to `#jury-floor`: the verdict post and the **Leaderboard** document. Then the web leaderboard at
`localhost:8787`, with cases ranked and red flags visible on the click case.
> "Every verdict lands on the floor and on a live leaderboard. Export it to CSV when judging's done."

**2:30–2:45 · Why Switch** (README "How Switch is used" table on screen)
> "Rooms, templates, documents, links, addressing, three kinds of agent on two models, and a custom protocol
> client. Take Switch away and Jury is a script with nobody to talk to. It also reviews hiring take-homes:
> swap one rubric file. Jury: every submission gets a room, a human gets the verdict."

---

Upload to YouTube (unlisted is fine) and put the link where `DEMO_VIDEO_URL` sits at the top of the README.
That also turns the Auditor's "demo video" check green when Jury judges itself.
