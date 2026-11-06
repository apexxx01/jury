# Voice-over script (about 5:30, read at a calm pace)

Each block is one scene. Times are from the start of the video. Pause a beat between scenes.

**0:00 · Open**

Jury: a hackathon judging floor that runs in your team chat, built on Switch.

**0:08 · The problem**

Flip the Switch had 7,516 people register. If one in five submits, that's about 1,500 repos to clone, run and check. At five minutes each, that's 125 hours before anyone judges anything.

**0:25 · How it works**

Paste a repo link in chat. Clerk opens a dedicated room for that submission. Three AI judges gather evidence in that room and argue it out in the open. A human gives the verdict. It lands on the judging floor and a live leaderboard.

**0:49 · Intake**

Someone drops a submission link in #jury-intake. Clerk, an agent written directly on Switch's protocol, opens a case room. The room is built from a YAML template: its own instructions, the rubric and the submission as room documents, linked to the floor.

**1:13 · The Auditor**

The kickoff wakes the judges through Switch's addressing. The Auditor uses no LLM. It clones the repo and checks hard facts. A great library with 1,600 commits from 199 people, but no Switch in it at all. The rule gets flagged. Every fact lands in a room document the other judges can cite.

**1:43 · Copy check**

Someone files the same project twice, under its old name. The Auditor fingerprints every submission: shared git history and shared code. Boilerplate everyone uses doesn't count. It warns the earlier case's room too, so those judges see it without looking. It flags; a human decides.

**2:11 · Jury judges itself**

Now the real test: Jury judging its own repo, on a live bench. The Inspector is Claude Code, running through Switch Console. Console's auto mode stopped it from running code it had just cloned, so it asked a human, right in the channel. A person approves, with limits. It actually ran it: 38 tests pass, lint clean. And it found a real bug in Jury: a second copy would hijack the live Clerk. Fixed in the next commit, credited to the Inspector. Clerk reads every scorecard out of the room and tallies it live. The Critic judges the idea, the impact, and whether Switch is central. It even flags its own conflict of interest. When the panel is complete, Clerk hands a human the case file.

**3:37 · The human decides**

Agents gather evidence and argue. Only a human gives the verdict. The score is optional and can override the panel's. Both are kept on record.

**3:59 · The floor**

Every verdict lands on the judging floor and on a live leaderboard. Per-criterion scores against the event's own rubric, red flags, copies and verdicts in one place. Median time from filing to a full panel: the number an organiser actually cares about.

**4:25 · Reproduce it**

Anyone can check it. One command runs the whole loop on a fresh Switch server. No Console, no chat app, no API keys. Stand-in judges, a human verdict through the Gateway, then a check. Exit code zero: the whole loop works.

**4:49 · Why Switch**

Switch carries the whole workflow. Take Switch away and Jury is a script with nobody to talk to. And it's built for a real judging team: assigned judges, appeals, bulk import and export.

**5:17 · Close**

Swap one rubric file and it reviews hiring take-homes instead. Jury: every submission gets a room. A human gets the verdict.
