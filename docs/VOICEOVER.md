# Voice-over (3:00, read at a calm pace)

One block per scene in [`DEMO.md`](DEMO.md); the times match. About 145 words a minute. Pause a beat between
scenes.

**0:00 · Open** (console)

Seven and a half thousand people signed up for this hackathon. Before anyone judges anything, someone has to clone every repo and check it runs. This is Jury: a judging floor in your team chat, on Switch.

**0:15 · Intake and the Auditor**

Paste a GitHub link. Clerk, an agent I wrote on Switch's protocol, builds a room for it from a template, with the rubric and the submission as room documents. The Auditor uses no LLM. It checks the facts: this great library has no Switch in it at all, so it's flagged.

**0:45 · Jury judges itself**

Now Jury judging its own repo. The Inspector is Claude Code in Switch Console. It refused to run code it had just cloned until a person approved, right in the channel. Then it ran it: tests pass. And it found a real bug in Jury, which I fixed in the next commit. The Critic weighs the idea and even flags its own conflict of interest. When two judges disagree, Clerk makes them argue it out with evidence. Then it tallies everything and hands a human the case.

**1:40 · The human decides**

The console shows exactly how the score was built. No black box. The verdict builder writes the command, and I give it in chat, so it's on the record under my name. Only a human gives the verdict.

**2:10 · The whole floor**

On a busy floor, it tells you what to do next: a possible copy, a split, a case nobody's decided. And it shows where humans overrode the AI, and which judge to trust.

**2:35 · Reproduce it**

One command runs the whole loop on a fresh Switch server. No keys.

**2:47 · Close**

Take Switch away and Jury is a script with nobody to talk to. Every submission gets a room. A human gets the verdict.
