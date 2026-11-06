# Voice-over (3:00, read at a calm pace)

One block per scene in [`DEMO.md`](DEMO.md); the times match. About 145 words a minute. Pause a beat between
scenes.

**0:00 · Open** (console)

Seven and a half thousand people signed up for this hackathon. Before anyone judges anything, someone has to clone every repo and check it runs. This is Jury: a judging floor in your team chat, on Switch.

**0:15 · Live: intake and the Auditor**

I'm pasting Switch's own repo into the intake channel, live. Clerk, an agent I wrote on Switch's protocol, builds a room for it from a template, with the rubric and the submission as room documents. The console picks it up on its own. The Auditor uses no LLM, so its facts land in seconds.

**0:40 · No free passes** (write this after recording: say what the bench actually did)

Draft, if it plays out like last time: The Auditor gave it ten on Use of Switch. The Critic gave it two: it's the sponsor's product, not anyone's entry. That's an eight-point split, so Clerk made them argue it out in the room. And the rules apply to everyone: most of the commits are from before the hackathon. Even the sponsor doesn't get a free pass.

**1:15 · Jury judges itself**

Then I pointed it at itself. The Inspector is Claude Code in Switch Console. It refused to run code it had just cloned until a person approved, right in the channel. Then it ran it: tests pass. And it found a real bug in Jury, fixed in the next commit. The Critic flagged its own conflict of interest. Clerk tallies it all and hands a human the case.

**2:05 · The human decides**

The console shows exactly how the score was built. No black box. The verdict builder writes the command, and I give it in chat, so it's on the record under my name. Only a human gives the verdict.

**2:35 · The floor**

It tells you what to do next, and shows where humans overrode the AI and which judge to trust.

**2:48 · Reproduce it**

One command runs the whole loop. No keys.

**2:54 · Close**

Every submission gets a room. A human gets the verdict.
