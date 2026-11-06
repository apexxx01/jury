# Runbook: Jury on a Mac, from zero to demo

About 45 minutes the first time, most of it waiting on Docker and Discord settings.

## 0. Before you start

- **Docker Desktop**, installed and running (Switch Console runs its local server in it)
- **uv** and **git**: `brew install uv git`
- **Claude Code** signed in (`claude` works in a terminal) and **Codex** signed in (`codex` works)
- A **Discord server you own**. Make a fresh one called "Jury" for the demo so it looks clean.

## 1. Switch Console and its local server

1. Download Switch Console for Apple Silicon:
   https://github.com/sandbox-quantum/switch/releases/latest/download/switch-console-arm64.dmg
2. Open it and choose the **local server**. It pulls the images and starts Postgres + Switch in Docker.
3. Sign in to the local server when it asks.

Check: `curl -s localhost:8000/health` should print `{"status":"ok"}`. If Console picked another port, Jury
finds it anyway.

## 2. Connect Discord (about 10 minutes)

Full guide: https://docs.switchagents.ai/flintai/switch/deploy/messaging-apps/discord

1. https://discord.com/developers/applications → **New Application** → name it `Jury`.
2. **Bot** tab → copy the token (reset it if it's hidden). Under **Privileged Gateway Intents** turn on
   **Server Members** and **Message Content** → **Save Changes**.
3. **OAuth2 → URL Generator**: scopes `bot` **and** `applications.commands`. Bot permissions: View Channels,
   Send Messages, Send Messages in Threads, Manage Webhooks, Manage Channels, Manage Roles, Read Message
   History, Attach Files. Open the generated URL and add the bot to your server.
4. Discord → User Settings → Advanced (called Developer in some versions) → **Developer Mode** on. Right-click your server icon → **Copy Server ID**.
5. Switch Console → your server's **Home** → **Messaging apps** → **Connect** → **Discord**. Name it `Discord`,
   then paste the token and the Server ID (Guild Id) → **Connect**.
6. When it asks which Discord account is yours, find yourself → **This is me**. Don't skip this: it's what
   puts you into every case room.

## 3. Jury

Newer Console builds keep the server's secrets in an encrypted store, so Jury needs a registration key from
you once. In Switch Console: **Settings → API keys** → label it `jury` → **Create registration key** → copy it.

```bash
cd ~/Developer/jury          # wherever the repo is
echo 'SWITCH_REGISTRATION_TOKEN=<paste the key>' >> .env
uv sync
uv run jury doctor
```

Jury finds the server's port by itself (from Console's `ports.json`).

**No Discord?** Console's local server comes with Mattermost already connected, and Jury works the same
there. Skip section 2. To see the channels in a browser, open Console → **Home → Messaging apps** → the ⋯
menu on Mattermost → **Sign-in details…**, then log in at the address it shows.

`doctor` should show ✓ on everything except the two judges, which don't exist yet. If you connected more
than one messaging app, run `echo 'JURY_BRIDGE=Discord' >> .env`.

```bash
uv run jury up
```

Leave it running. `#jury-intake` and `#jury-floor` appear in Discord, Clerk posts "Jury is in session", and
`jury up` prints `template 'Jury bench': published`.

## 4. The judges (one click)

Switch Console → **Templates** → **Jury bench** → check that *Inspector runs on* says Claude Code and *Critic
runs on* says Codex → **Create**.

`#jury-chambers` appears, and both judges introduce themselves there. Now restart `jury up` (Ctrl+C, then run
it again) so Clerk puts them on the bench: it should print
`judges on the bench: jury-auditor, jury-inspector, jury-critic`.

## 5. Dry run

Paste this in `#jury-intake`:

```
https://github.com/sandbox-quantum/switch
```

You should see:
- a reply in the thread: `📂 Case #001 opened … → #case-001-switch`
- a new channel `#case-001-switch` with the kickoff, the Auditor's report and scorecard, then the Inspector and
  the Critic working (the LLM judges take a few minutes)
- Clerk's tally after each scorecard, and `✅ All scorecards are in` at the end

Then type in the case channel: `@jury-clerk verdict shortlist 90 the reference implementation`

The verdict shows up on `#jury-floor` and at http://127.0.0.1:8787.

## If something's off

| Symptom | Fix |
|---|---|
| `doctor`: Switch not answering | Console → local server → Start. Check Docker Desktop is running. |
| `doctor`: no registration token | Console's local server hasn't been set up on this Mac. Or set `SWITCH_URL` + `SWITCH_REGISTRATION_TOKEN` in `.env`. |
| No Discord channels appear | Run `doctor`: is a messaging app listed? Check that channel creation is allowed on the Discord connection, and that the bot has **Manage Channels**. |
| Inspector or Critic never speaks in a case room | Check their sessions in Console. Say `@jury-inspector please start on this case` in the room to wake one by hand. |
| A judge posted but Clerk didn't tally it | The scorecard needs a ```` ```jury ```` fenced JSON block. Ask the judge: "file your scorecard in the jury block format from the Rubric doc". |
| Inspector asks for approval in the channel | That's Console's auto mode guarding untrusted code. Approve it or tell it to skip that step. It looks good on video. |
| Start over | Ctrl+C `jury up`, `rm -rf .jury`, archive the old channels, run `jury up` again. |
