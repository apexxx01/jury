# Demo video: shot list (3:00)

The organisers asked for about three minutes. Judges score Presentation on "do the demo video and README make
clear how Switch is used?", so every scene shows Switch doing real work. Read the narration from
[`VOICEOVER.md`](VOICEOVER.md); the times there match the scenes here.

Record Chrome at full size, 1080p or more. Cut every wait and put a small "⏩ 3 minutes later" card where you
cut. Keep the cursor slow: judges are reading along.

## Before you hit record

- `git pull`, then `uv run jury up` is running. The console is at http://127.0.0.1:8787.
- Optional, in a second terminal: `uv run jury demo --port 8788` (the recorded 12-case floor, a backup for
  scene 6). Open it in its own tab.
- **Case #001 (Jury judging itself) needs to be waiting on a verdict.** If you already gave one, say
  `@jury-clerk reopen 1` on the floor. The old verdict stays on record and the case goes back to *needs a human*.
- **`sandbox-quantum/switch` is NOT on the floor.** You paste it live in scene 2. If it's there, say
  `@jury-clerk withdraw <n> refiling for the demo` first.
- **Record the screen first, the voice-over after.** The judges' scores aren't scripted, so once the screen
  recording is cut, write scene 3's lines to match exactly what happened, then record the voice over the edit.
- `docker compose -f repro/compose.yaml down -v` has been run, so the repro clip starts clean.
- Have `https://github.com/sandbox-quantum/switch` ready to paste.
- Put Mattermost and the console side by side (two windows, half the screen each) for scenes 2 and 3.
- Close other tabs. Turn on Do Not Disturb.

## Scenes

| # | Time | Screen | Do this |
|---|---|---|---|
| 1 | 0:00–0:15 | Console, live (`:8787`) | Start on the headline and the pipeline. Hold. |
| 2 | 0:15–0:40 | Mattermost `jury-intake` + console, side by side | Paste the Switch link. Live, with no cuts: Clerk replies, the new case room appears, and on the console the case lands on the leaderboard and the pipeline ticks Filed → Audited as the Auditor's scorecard arrives in seconds. Open the room's documents. Keep recording while the judges work. |
| 3 | 0:40–1:15 | The Switch case room, then its case file in the console | Card: *"⏩ 6 minutes later"*. Show what the bench actually did: the scorecards, any ⚔️ split Clerk raised, the red flags. In the console: the scorecard table (splits outlined in red) and the pipeline now at *Needs a human*. Write this scene's narration after you've seen it. |
| 4 | 1:15–2:05 | `case-001-jury` | **Jury judges itself.** Scroll slowly: the Inspector asking before it runs cloned code, your approval, tests passing, the bug it found, the Critic's conflict-of-interest flag, Clerk's tally and `✅ All scorecards are in`. |
| 5 | 2:05–2:35 | Console, live, then `case-001-jury` | Open Jury's case file. Hover the score receipt. In the verdict builder pick **shortlist**, slide to 90, type the note, **Copy**. Paste it in the case room and send. Clerk replies with the rank. Back to the console: the stamp turns green and **Next up** moves on to the next case. |
| 6 | 2:35–2:48 | Console, live (scroll) | Quick pass: the **Needs attention** strip, **panel vs human**, the **timeline**, **Judge lean**. If your live floor is thin, use the demo tab (`:8788`) with the *"Demo floor: sample cases"* card. |
| 7 | 2:48–2:54 | Terminal | The Docker repro, sped way up, ending on `✓ the whole loop works`. |
| 8 | 2:54–3:00 | Console headline | End on it. |

Note to type in scene 5: `judged itself, found its own bug, and still passed`.

### If something misbehaves on camera

- A judge is stuck in a queue: `@jury-clerk summon` in that room.
- Clerk missed a scorecard: `@jury-clerk rescan`.
- Mattermost won't show a new room: refresh the page; the room exists on Switch already.

## After

Upload to YouTube (unlisted is fine). Replace `DEMO_VIDEO_URL` at the top of the README with the link and
push. That also clears the Auditor's "no demo video" flag when Jury judges itself. Add it as the dev.to post's
cover video too.
