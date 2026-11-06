# Voice-over (about 4:45 with pauses, read at a calm pace)

One block per scene in [`DEMO.md`](DEMO.md); the times match. About 145 words a minute. Pause a beat between
scenes, and let the screen breathe where a line is short.

**0:00 · Open** (console headline)

Flip the Switch had seven and a half thousand people register. If one in five submits, that's fifteen hundred repos. Five minutes each just to clone it, check it runs and find the video, and you've spent a hundred and twenty-five hours before anyone judges anything. This is Jury: a judging floor that runs in your team chat, on Switch.

**0:25 · The pipeline**

Every submission moves through five stages. It's filed. A deterministic Auditor checks the facts. Two AI judges, one running the code and one weighing the idea, score it and argue when they disagree. Then it waits for a human, because only a human gives the verdict.

**0:45 · Intake**

Someone pastes a GitHub link in the intake channel. Clerk, an agent I wrote directly on Switch's protocol, opens a dedicated room for it. The room is built from a YAML template, with its own instructions, the rubric and the submission as room documents, and it's linked to the judging floor.

**1:07 · The Auditor**

The Auditor uses no LLM. It clones the repo and checks hard facts. Here's a great library: sixteen hundred commits, almost two hundred authors, and no Switch in it at all. That breaks the rule, so it's flagged. It also checks the commit window and scans for leaked keys. Every fact goes into a room document the other judges can cite.

**1:33 · Copy check**

Now the same project, filed twice under two names. The Auditor fingerprints every submission, its git history and its code, and ignores boilerplate everyone shares. It warns both rooms. It flags; a human decides.

**1:49 · Jury judges itself**

Now the real test: Jury judging its own repo, on a live bench. The Inspector is Claude Code, running through Switch Console. Console's auto mode stopped it from running code it had just cloned, so it asked a person, right here in the channel. I approved, with limits. It actually ran it: the tests pass, lint is clean. And it found a real bug in Jury. A second copy would hijack the live Clerk. Fixed in the next commit, credited to the Inspector. The Critic judges the idea, the impact and whether Switch is central, and it even flags its own conflict of interest: the bench is judging the repo its instructions come from. Clerk reads every scorecard out of the room and tallies it live. When the panel's complete, it hands a human the case file.

**2:47 · The human decides**

Here's that case file in the console. The score receipt shows the arithmetic: each criterion, which judges scored it, the mean and the weight. No black box. The verdict builder writes the command for me. Shortlist, ninety, a note. I paste it in the room, so the verdict is on the record in Switch under my name. Clerk records it and tells me the rank, and the console updates on its own.

**3:19 · The whole floor**

This is a busier floor: twelve sample cases. Up top, what needs attention: a possible copy, two judges who disagree by five points, cases nobody has decided yet. The score distribution against the shortlist bar. Every dot here is a decided case, the AI panel's score against the human's, so you can see where people overrode the machine. A timeline of every case, from filing to verdict. And the bench: which judge owns which criterion, how fast each one replies, and how generous each is compared with the humans.

**3:59 · Built for a real team**

It's built for an organiser at fifteen hundred submissions: assigned judges, reminders, appeals, bulk import, a results write-up, and stats and calibration in chat.

**4:11 · Reproduce it**

And anyone can check it. One command runs the whole loop on a fresh Switch server, with no Console, no chat app and no API keys. Exit code zero.

**4:24 · Close**

Switch carries the whole workflow: rooms, documents, links, agents and people in one place. Take Switch away and Jury is a script with nobody to talk to. Jury: every submission gets a room. A human gets the verdict.
