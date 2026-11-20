# Nyanjou / T3 Code integration (first milestone)

The `feature/nyanjou-live-t3` branch adds an opt-in **/t3task** command to the existing Python/Pycord bot; it does **not** replace its existing cogs. The command checks `allowed_users.sqlite` (managed by `/usr-mgmt`) and supports an optional `NYANJOU_OWNER_ID`. It launches work in the configured T3 Code project through the official MCP tool `t3_thread_launch`. T3 handles the actual coding work; no extra Discord approval is requested for ordinary tasks.

## Install

Run `uv add 'mcp>=1.0'` and `uv sync`, then configure:

```env
T3_MCP_URL=https://your-t3-environment.example/mcp
T3_PROJECT_ID=your-t3-project-id
NYANJOU_OWNER_ID=your-discord-user-id
# Optional: only when provisioned by T3's official sign-in process:
T3_MCP_ACCESS_TOKEN=
```

You must authorize the client in T3 Code with a write-capable access level. T3 Code's MCP endpoint uses OAuth; **a URL and a ChatGPT subscription alone do not authenticate this bot**. This implementation accepts a valid existing bearer token but does **not yet implement OAuth discovery, PKCE, token refresh, or an interactive sign-in**. A token issued for another application may not work. Without proper authorization the command fails closed. Never store credentials in Git.

Run the bot normally and invoke `/t3task`. The response confirms whether `t3_thread_launch` succeeded, and shows the launch details. It does **not yet wait for the finished run or read results**.

## Remaining implementation

- Integrate a proper OAuth client with persistent encrypted token storage and refresh.
- Handle `t3_thread_wait`/`t3_thread_read` with durable thread state and status reporting.
- Add `/join` and `/leave` plus actual per-speaker Discord audio receive / PCM resampling / GPT-Live audio output. **No live-voice audio bridge is implemented in this milestone.**
- Apply a voice-speaker policy: the person invoking `/join` is not necessarily the person speaking. Check identity for tool execution, and only authorize task dispatch from verified users.
- Test on the real Discord guild and T3 environment.

T3 MCP docs: https://github.com/pingdotgg/t3code/blob/main/docs/user/outside-agents.md

OpenAI Live docs: https://developers.openai.com/api/docs/guides/voice-websockets

### Security

T3 permissions still bound what the downstream coding agent can do. Keep the T3 MCP endpoint private, and give the bot the narrowest access you need. Discord replies are ephemeral to avoid exposing task descriptions to other users.
