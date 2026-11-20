# Nyanjou: GPT-Live voice assistant with T3 Code

Nyanjou adds a live voice assistant to the existing Pycord bot. Invite it into a voice channel, talk to it like
ChatGPT's voice mode, and ask for development work ("Nyanjou, build me a Minecraft website"). It hands that work to
[T3 Code](https://github.com/pingdotgg/t3code) and keeps talking with you while T3 works. When the task finishes,
it tells you the result in voice and posts it in Discord.

None of the existing features (music/yt-dlp, YouTube, RSS, Minecraft, AI cogs, user management) are changed. Nyanjou
lives in two new cogs plus `services/` and `bridge/`.

## Architecture

```
Discord voice (UDP, DAVE E2EE)
        │
┌───────▼─────────┐  ws://127.0.0.1 (token)   ┌───────────────────────── Python bot (bot.py) ─────────────────────────┐
│ bridge/ (Node)  │◄─────────────────────────►│ services/discord_audio.py  BridgeVoiceProtocol (Pycord VoiceProtocol) │
│ @discordjs/voice│  PCM 48k stereo + JSON    │ services/voice_session.py  jitter buffers → SpeakerGate → 24 kHz mono │
└─────────────────┘                           │ services/live_client.py    GPT-Live WebSocket (wss://…/v1/live/…)     │
                                              │ services/tools.py          function tools + security checks           │
                                              │ services/task_manager.py   queue, monitors, recovery (SQLite)         │
                                              │ services/t3_mcp_client.py  MCP over streamable HTTP + OAuth           │
                                              └───────────────────────────────────────┬───────────────────────────────┘
                                                                                      │ MCP (OAuth bearer)
                                                                                  T3 Code /mcp
```

### Why a Node voice bridge?

Discord requires end-to-end encryption (DAVE) for voice. Pycord 2.8 can't *receive* voice under DAVE. Its
`start_recording()` warns: "Voice reception is currently broken due to Discord's DAVE (End-to-End Encryption)
protocol" ([pycord#3139](https://github.com/Pycord-Development/pycord/issues/3139), still open with no fix release as
of Nov 2026). `@discordjs/voice` 0.19 implements DAVE for both sending and receiving.

The Python bot still owns the Discord gateway. `BridgeVoiceProtocol` is a normal Pycord `VoiceProtocol`, the
extension point Pycord documents for external voice backends such as Lavalink. It sends voice-state updates
through Pycord and forwards `VOICE_STATE_UPDATE` and `VOICE_SERVER_UPDATE` to the bridge. The bridge then handles
UDP, transport encryption, DAVE and Opus.

- The bot starts and supervises the bridge (`NYANJOU_BRIDGE_AUTOSTART=true`). It listens on loopback only and
  requires a random per-start token. It exits when the bot goes away.
- The bridge never receives the Discord bot token, the OpenAI key or T3 credentials.
- When Pycord ships working DAVE receive, only `discord_audio.py` and `bridge/` need replacing.

### GPT-Live

Nyanjou uses **GPT-Live 1.1** (`gpt-live-1.1`). OpenAI released it in the API on 2026-11-20; `gpt-live-1` can be
selected with `/model`. It connects over the documented WebSocket transport
(`wss://api.openai.com/v1/live/sessions`) with PCM16 mono at 24 kHz. Speech goes straight in and comes straight
out; there is no separate STT or TTS service.

- **Turn-taking and interruptions** are handled by GPT-Live, which is full duplex. In addition, if someone talks
  over Nyanjou for more than `NYANJOU_BARGE_IN_MS`, the bot flushes the audio it has already buffered locally and
  drops further model audio until that person pauses (barge-in).
- **Tools**: GPT-Live delegates to a Responses backend model (`NYANJOU_BACKEND_MODEL`) configured with Nyanjou's
  function tools (`t3_start_task`, `t3_update_task`, `t3_list_tasks`, `t3_task_status`, `t3_cancel_task`,
  `t3_list_projects`) and optional `web_search`. Function calls arrive as `response.event` /
  `response.output_item.done`. Results go back via `response.item.create` + `response.create`.
- **Async results**: finished T3 tasks are injected with `session.commentary.append`, and GPT-Live tells the
  channel.
- **Context**: one Live session per guild keeps the conversation. Live and task context is never shared between
  guilds. Tasks are stored per guild and per user. On reconnect, or when a session reaches its duration limit, a
  new session starts with the recent transcript as `session.input`.
- **Reconnects**: exponential backoff. Input audio is discarded while disconnected, so stale speech is never sent
  after a reconnect.
- **No recording**: audio is only kept in memory (≤ 500 ms jitter buffer per speaker) and is never written to disk.
  Transcripts are only logged with `NYANJOU_DEBUG=true` at DEBUG level.

## Security model

| Action | Who |
|---|---|
| `/join`, `/leave`, `/voice`, `/model` | authorized users (bot owner or `allowed_users.sqlite`) |
| Talk with Nyanjou in the channel | everyone, or only authorized users with `NYANJOU_ALLOW_GUEST_CONVERSATION=false` |
| Start, update, cancel or list T3 tasks by voice | **the identified speaker** must be authorized |
| `/t3task`, `/tasks`, `/cancel` | authorized users (interaction author) |
| Manage someone else's task | only the bot owner |

Authorization uses Discord **user IDs** from the existing `/usr-mgmt` table. The bot owner (application owner/team,
or `NYANJOU_OWNER_ID`) is always authorized.

**An authorized `/join` does not authorize anyone else in the channel.** Every executing tool call is attributed
to a Discord user based on audio provenance, not on what was said:

1. The bridge tags every received audio frame with the Discord user ID, using Discord's SSRC→user mapping.
2. In `floor` mode (the default), only one person's audio is forwarded to GPT-Live at a time. Each utterance
   therefore has exactly one source.
3. Each 20 ms of audio sent is recorded on the Live session clock. GPT-Live reports when it delegated
   (`session.delegation.created.offset_ms`).
4. A tool call may only run if **exactly one** user produced speech in the window before that point
   (`NYANJOU_ATTRIBUTION_WINDOW_MS`, default 8 s), and that user is authorized. If several people spoke, nobody
   spoke, or the delegation can't be matched, the call is refused and nothing is executed.
5. Updating, cancelling or reading a task also checks that it belongs to the speaker and the same guild.

Bots, including Nyanjou itself, are never listened to. Discord also doesn't send the bot its own audio.

**Risky work** needs an explicit spoken confirmation. This covers irreversible deletion, production changes,
publishing private data and security-critical actions. It triggers when the backend flags `risk_level=high` or a
keyword heuristic matches. The first call returns a single-use `confirmation_id`. Only a second call that quotes
it, comes from the same speaker and arrives within 3 minutes starts the task. Normal development tasks start
without confirmation.

T3's own access level is the final boundary. Threads Nyanjou starts can never exceed the level you approved for the
bot in T3.

## Requirements

- Ubuntu 22.04/24.04 (or any Linux), Python ≥ 3.12, [uv](https://docs.astral.sh/uv/)
- **Node.js ≥ 22.12** (for `@discordjs/voice`), `npm`
- `ffmpeg` (already used by the music cog)
- An **OpenAI API account** with billing (platform.openai.com). GPT-Live is not available on the free tier.
  **A ChatGPT Plus/Pro subscription can't be used.** ChatGPT subscriptions and API usage are billed separately,
  and there is no officially supported way to use a ChatGPT login for the API. GPT-Live costs $0.05/min while
  active (billed per second). Backend model and web-search usage is billed separately.
- A T3 Code environment with its MCP URL reachable from the bot host: `localhost`, Tailscale HTTPS, or T3 Connect.

## Installation (Ubuntu server)

```bash
# 1. System packages
sudo apt update && sudo apt install -y git ffmpeg curl ca-certificates
# Node 22 LTS (NodeSource) – or use fnm/nvm
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
node --version   # must be >= v22.12

# 2. Service user + checkout
sudo useradd --system --create-home --shell /bin/bash somebot
sudo mkdir -p /opt/some_bot && sudo chown somebot: /opt/some_bot
sudo -iu somebot
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/Snupai/some_bot.git /opt/some_bot
git clone <purrbot_site_api_wrapper repo> /opt/purrbot_site_api_wrapper   # sibling path dependency
cd /opt/some_bot

# 3. Dependencies
uv sync --frozen --no-dev
(cd bridge && npm ci --omit=dev)

# 4. Configuration
cp .env.example .env && chmod 600 .env
$EDITOR .env      # BOT_TOKEN, OPENAI_API_KEY, NYANJOU_OWNER_ID, T3_MCP_URL, ...

# 5. One-time T3 sign-in (see below)
uv run python tools/nyanjou_t3_login.py --paste
```

### T3 Code sign-in (OAuth)

T3's MCP server uses standard MCP OAuth, which was checked against a T3 server build from 2026-11-19. It offers
dynamic client registration, PKCE `S256`, public clients, and the scopes `orchestration:read` and
`orchestration:operate`. Nyanjou uses the official MCP Python SDK OAuth client for this.

1. In T3 Code: **Settings → Environments → ⋯ → Copy MCP URL** and put it in `T3_MCP_URL`. Use `https://…/mcp` or
   `http://localhost:…/mcp`. T3 only sends credentials over https or to localhost.
2. Run `uv run python tools/nyanjou_t3_login.py`, or add `--paste` on a headless server.
3. Open the printed URL, sign in, and approve **"Nyanjou Discord Bot"** with an access level that can start threads
   (*Supervised* or higher). *Read only* can't run tasks.
4. With `--paste`: the browser lands on `http://localhost:33418/callback?...`. If that page doesn't load, copy the
   full URL from the address bar and paste it into the terminal.
5. The script stores the token in `data/t3_oauth.json` (mode 0600, gitignored). It then lists T3's tools and your
   projects, so you can set `T3_PROJECT_ID`.

**Token refresh:** T3 currently advertises only `authorization_code`, so it issues no refresh tokens, and sign-ins
last 30 days. Nyanjou refreshes automatically if a server ever returns a refresh token. Otherwise `/status` shows
"Anmeldung nötig" when the sign-in expires. Running tasks report `auth_required`, and you rerun the login script.
Revoke access anytime in T3 under **Settings → Sharing & access**.

`T3_MCP_ACCESS_TOKEN` is only for a bearer token issued by T3's official sign-in. Never commit it.

### systemd

```bash
sudo cp /opt/some_bot/deploy/some-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now some-bot
journalctl -u some-bot -f          # logs (also written to /opt/some_bot/logs/)
```

The unit restarts on failure, gives Nyanjou 20 s to leave voice cleanly, and applies basic sandboxing. The bridge is
a child process of the bot, in the same cgroup, and stops with it. Set `NYANJOU_DEBUG=true` for verbose
Nyanjou/bridge logs.

### Running the bridge separately (optional)

Set `NYANJOU_BRIDGE_AUTOSTART=false` and a fixed `NYANJOU_BRIDGE_TOKEN` (≥ 16 chars). Then run
`NYANJOU_BRIDGE_TOKEN=… node bridge/index.mjs`, for example as its own systemd unit. It binds `127.0.0.1` only,
unless `NYANJOU_BRIDGE_ALLOW_REMOTE=true` is set.

## Discord commands

| Command | |
|---|---|
| `/join` | Join your voice channel and start a GPT-Live session |
| `/leave` | Leave and end the session (T3 tasks keep running) |
| `/status` | Voice/Live/bridge/T3 status and active tasks |
| `/tasks [alle]` | Your tasks (the owner sees all tasks of the server) |
| `/cancel <nummer>` | Cancel a running task (`t3_thread_interrupt`) |
| `/voice <name>` | Pick one of the 23 GPT-Live voices (restarts the Live session; the conversation is kept) |
| `/model <name>` | `gpt-live-1.1` or `gpt-live-1` |
| `/t3task <aufgabe> [projekt] [neues_projekt] [bestaetigt]` | Start a T3 task by text |

The bot needs the **Connect** and **Speak** permissions in the voice channel. The `voice_states` and `members`
intents are already enabled in `bot.py`.

## Example flow

> **You:** "Nyanjou, bau mir eine Webseite." → **Nyanjou:** "Was für eine Webseite?"
> **You:** "Eine für meinen Minecraft-Server." → GPT-Live delegates → `t3_start_task`
> → speaker = you (authorized) → task #12 queued → `t3_thread_launch` → **Nyanjou:** "Okay, T3 Code arbeitet daran."
> **You:** "Mach das Design bitte in Catppuccin Mocha." → `t3_update_task` → `t3_thread_send` (mode `auto`) on the
> same thread
> … T3 finishes → `t3_thread_wait` returns `completed` → `t3_thread_read` → commentary → **Nyanjou** tells you, and
> an embed is posted in the text channel.

T3 tools used, all taken from T3's outside-agent docs and their published schemas: `t3_thread_launch`,
`t3_thread_wait`, `t3_thread_read`, `t3_thread_send`, `t3_thread_interrupt`, `t3_project_list`. Tools are discovered
at connect time, and arguments are filtered against each tool's `inputSchema`. The task ID is passed as
`clientRequestId`, so a launch retried after a crash can't create a duplicate thread.

Tasks are stored in `data/nyanjou_tasks.sqlite`. After a restart, running tasks are monitored again and queued tasks
are re-launched. At most `NYANJOU_MAX_PARALLEL_TASKS` run at once; the rest wait in the queue.

## Configuration reference

All variables, with defaults, are in [`.env.example`](../.env.example). The most important ones:

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | (falls back to `OPENAI_TOKEN`) | OpenAI API key |
| `NYANJOU_VOICE_MODEL` / `NYANJOU_VOICE_NAME` | `gpt-live-1.1` / `marin` | GPT-Live model and voice |
| `NYANJOU_BACKEND_MODEL` | `gpt-5.6-luna` | Responses model used for delegated tool work |
| `NYANJOU_OWNER_ID` | – | Always-authorized Discord user ID (in addition to the application owner) |
| `NYANJOU_ALLOW_GUEST_CONVERSATION` | `true` | Unauthorized users may chat (never execute) |
| `NYANJOU_SPEAKER_MODE` | `floor` | `floor` = one speaker at a time; `mix` = mix all (more natural, but tasks are refused whenever two people overlap) |
| `NYANJOU_ATTRIBUTION_WINDOW_MS` | `8000` | Look-back window for speaker attribution |
| `NYANJOU_RESULTS_TO` | `channel` | Where task results are posted (`channel`/`dm`/`none`) |
| `T3_MCP_URL` / `T3_PROJECT_ID` | – | T3 MCP endpoint and default project |
| `T3_ALLOW_SCRATCH` | `true` | Brand-new apps go to a fresh T3 scratch folder |
| `NYANJOU_MAX_PARALLEL_TASKS` | `4` | Concurrent T3 tasks |

## Tests

```bash
uv sync                       # includes the dev group (pytest)
uv run pytest                 # 75 tests, ~15 s
(cd bridge && npm test)       # bridge unit tests
```

What is covered without real credentials:

- **Audio**: resampling 48k↔24k (frequency/level preserved, anti-aliasing, chunk-boundary continuity, odd byte
  carries), VAD, mixing.
- **Speaker gate**: floor control, release, mix mode, and attribution (single, multiple, none, blips, horizon).
- **GPT-Live client** against a fake Live WebSocket server: `session.start` payload, audio both ways, delegation
  → function call → `response.item.create` + `response.create`, tool exceptions, reconnect with history, auth
  failure fails fast, commentary.
- **T3 MCP client** against a real MCP server (FastMCP, streamable HTTP) exposing T3-shaped tools: launch (incl.
  idempotent retry), wait, read, send, interrupt, project list, tool errors, argument filtering, unreachable server.
- **OAuth** against a mock authorization server shaped like T3's metadata: discovery, DCR, PKCE S256 verification,
  token stored with 0600, bot reuses token without a browser, missing or expired sign-in fails fast, grant-type
  discovery.
- **Task manager**: queue, parallel limit, retries, auth failure, updates (running and finished), cancel, restart
  recovery without duplicate launches, timeout, per-guild/per-user isolation.
- **Security**: unattributed or multi-speaker calls refused, guests refused even after an authorized `/join`,
  ownership across users and guilds, risky-task confirmation (wrong user, single-use), heuristic, strict tool
  schemas.
- **Voice session** with a fake bridge and fake Live: floor forwarding, bots and non-members ignored, guest policy,
  end-to-end delegation attribution, guest speech blocking execution, barge-in flush and suppression, 20 ms clock
  output, session reset, task events limited to the right guild, live restart keeps history, bridge loss.
- **Bridge**: wire format, Python client auth/routing/reconnect, `BridgeVoiceProtocol` gateway forwarding, and the
  **real Node bridge process**: token rejection, `join` producing a correct op-4 voice-state payload through the
  adapter, playback before ready, leave.
- All existing cogs still load next to the two new ones, with no slash-command name collisions.

**Not tested here** (needs real Discord/OpenAI/T3 access and should be checked once on the real server):

- An actual Discord voice connection (UDP, DAVE handshake) and real audio receive/playback through
  `@discordjs/voice`.
- A real GPT-Live session: audio quality, latency, interruption behaviour, the exact delegation timing on the
  session clock, and the backend model name `gpt-5.6-luna`. The protocol follows OpenAI's docs and the
  `openai` 3.57 SDK types.
- A real T3 OAuth approval in the browser, and the exact JSON returned by `t3_thread_launch`, which is parsed
  defensively. `t3_thread_wait`, `t3_thread_read` and `t3_project_list` were checked against a live T3 server.

## Troubleshooting

- **`/join` says the voice bridge is not running**: check `node --version` (≥ 22.12) and run `npm ci` in `bridge/`.
  Then look in the logs for lines starting with `[bridge]`.
- **Nyanjou joins but doesn't hear anyone**: set `NYANJOU_DEBUG=true` and check for `decrypt` messages from the
  bridge (DAVE). Make sure the bot has the Connect permission and isn't server-muted or deafened.
- **"Sprecher nicht eindeutig"**: someone else spoke in the last 8 seconds. Let the authorized person repeat the
  request alone, or lower `NYANJOU_ATTRIBUTION_WINDOW_MS`.
- **T3 "Anmeldung nötig"**: rerun `uv run python tools/nyanjou_t3_login.py`.
