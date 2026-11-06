# #HowISwitch posts

## X / Twitter

7,516 people registered for #FlipTheSwitch. Someone has to clone every submission.

So I built Jury: paste a repo link in team chat → a Switch agent opens a room for it → an Auditor, Claude Code and
OpenCode gather evidence and argue → a human gives the verdict. A live console shows the whole floor.

Try it with no setup: uv run jury demo

#HowISwitch @WeMakeDevs [demo video link]

## LinkedIn

Hackathons have a hidden bottleneck: before anyone judges anything, someone has to clone every submission,
check it runs and find the demo video.

For the Flip the Switch hackathon I built **Jury**, a judging floor that runs inside team chat on Switch
(open-source infrastructure for putting AI agents into team chat):

• Paste a GitHub link → a dedicated room opens for that submission
• An Auditor (no LLM) checks hard facts: does it use Switch, setup steps, tests, commits inside the event
  window, leaked API keys
• An Inspector (Claude Code) actually runs it; a Critic (OpenCode) judges the idea and the impact
• When judges disagree, they have to settle it in the room, with evidence
• A human gives the verdict; a live console shows every case, why it scored what it did, and which AI judge to trust

Two of the agents I wrote from scratch on Switch's raw SSE/HTTP protocol. Swap one rubric file and it reviews
hiring take-homes instead.

Repo + demo: [link]

#HowISwitch #FlipTheSwitch #AIagents #opensource
