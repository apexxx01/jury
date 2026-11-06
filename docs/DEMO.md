# Demo video: shot list (about 4:45)

Judges score Presentation on "do the demo video and README make clear how Switch is used?", so every scene
shows Switch doing real work, and the console shows the whole floor at once. Read the narration from
[`VOICEOVER.md`](VOICEOVER.md); the times there match the scenes here.

Record Chrome at full size, 1080p or more. Cut every wait and put a small "⏩ 3 minutes later" card where you
cut. Keep the cursor slow: judges are reading along.

## Before you hit record

- `git pull`, then `uv run jury up` is running. The console is at http://127.0.0.1:8787.
- In a second terminal: `uv run jury demo --port 8788`. That's the recorded 12-case floor, for the console
  tour in scene 8. Open it in its own tab.
- **Case #001 (Jury judging itself) is fully scored but has NO verdict yet.** You give it live in scene 7.
- `docker compose -f repro/compose.yaml down -v` has been run, so the repro clip starts clean.
- Have these ready to paste:
  - `https://github.com/pallets/click` (a great library with no Switch in it, so the bench has something to catch)
  - `https://github.com/jqlang/jq https://github.com/stedolan/jq` (the same project under two names, for the copy check)
- Press `t` in the console if you want it dark. Close other tabs. Turn on Do Not Disturb.

## Scenes

| # | Time | Screen | Do this |
|---|---|---|---|
| 1 | 0:00–0:25 | Console, live (`:8787`) | Start on the headline ("N cases on the floor. N need you."). Hold. |
| 2 | 0:25–0:45 | Console, live | Slowly move left to right along the pipeline: Filed, Audited, Panel, Needs a human, Verdict. |
| 3 | 0:45–1:07 | Mattermost, `jury-intake` | Paste the click link. Clerk replies in the thread and a new `case-…-click` room appears in the sidebar. Point at it, open it, open its documents (Rubric, submission). |
| 4 | 1:07–1:33 | The click case room | The Auditor's report: *"No trace of Switch in the repository"*, the commit-window line, the secrets line. Open the **Auditor report** document. |
| 5 | 1:33–1:49 | `jury-intake`, then the stedolan/jq room | Paste both jq links in one message. ⏩ cut. Show the **Possible copy** warning in the stedolan room and the matching warning in the jqlang room. |
| 6 | 1:49–2:47 | `case-001-jury` | **The heart of the video.** Scroll slowly: (1) the Inspector refusing to run cloned code until a human approves; (2) your approval and its real run, tests passing, ruff clean; (3) the bug it found ("a second copy would hijack the live Clerk"), then the fix commit on GitHub; (4) the Critic's critique and its conflict-of-interest flag; (5) Clerk's tally lines and `✅ All scorecards are in`. |
| 7 | 2:47–3:19 | Console, live, then `case-001-jury` | In the console, click Jury's row. The case file opens: hover the **score receipt**, change the radar to "vs" another case. Scroll to the verdict builder: pick **shortlist**, slide to 90, type the note, press **Copy**. Switch to the case room, paste, send. Clerk replies with the verdict and the rank. Switch back: the console's stamp turns green on its own. |
| 8 | 3:19–3:59 | Console, demo (`:8788`) | Put up a card: *"Demo floor: 12 sample cases (`jury demo`)"*. Then: the **Needs attention** strip (click the copy alert, close it); hover the **score distribution** and the **panel vs human** dots; scroll the **timeline**; filter the leaderboard to **Needs a human**; open the **Bench** and hover **Judge lean**; end on **Talk to Clerk** and copy one command. |
| 9 | 3:59–4:11 | Mattermost, `jury-floor` | Type `@jury-clerk stats`, then `@jury-clerk calibration`. Show the replies. |
| 10 | 4:11–4:24 | Terminal | `docker compose -f repro/compose.yaml up --build --abort-on-container-exit --exit-code-from smoke`, sped up, ending on `✓ the whole loop works`. |
| 11 | 4:24–4:45 | README, "How Switch is used" table, then the console headline | Hold on the table, then end on the console. |

### Scene 7 note

The note to type: `judged itself, found its own bug, and still passed`. The command the builder copies is
`@jury-clerk verdict shortlist 90 judged itself, found its own bug, and still passed`. If you'd rather not
paste, type it; the point is that the console builds it and the verdict is still given in chat, on the record.

### If something misbehaves on camera

- A judge is stuck in a queue: `@jury-clerk summon` in that room.
- Clerk missed a scorecard: `@jury-clerk rescan`.
- Mattermost won't show a new room: refresh the page; the room exists on Switch already.

## After

Upload to YouTube (unlisted is fine). Replace `DEMO_VIDEO_URL` at the top of the README with the link and
push. That also clears the Auditor's "no demo video" flag when Jury judges itself.
