## Learned User Preferences

- Prefer managing dependencies with `uv sync` and `pyproject.toml` / `uv.lock`; running `uv add -r requirements.txt` without declaring local path packages can drop them from the environment.
- For Git commit signing with GPG: if signing fails with "No secret key" even though a key exists for the email, set `user.signingkey` to the key fingerprint (OpenPGP UID display name may not match `git config user.name` exactly).

## Learned Workspace Facts

- Optional `purrbot-site-api-wrapper` is an editable path dependency at `../purrbot_site_api_wrapper` via `[tool.uv.sources]` (sibling repo layout).
- The repo is configured as a uv application (`[tool.uv] package = false`), not an installable Python package.
- Bot log files are written under `logs/` next to `bot.py`.
- `cogs/ytdlp-stuff.py`: on `yt_dlp.DownloadError`, the bot upgrades `yt-dlp` in the running environment and retries once.
- YouTube notifications use the channel uploads playlist and track `last_video_published_at` to reduce duplicate or late notifications.
- RSS forwarding uses stable entry IDs (prefer link) and claims `FeedForwards` before sending to reduce double posts.
- AI cogs: Gemini model IDs and Pro system prompts live in `cogs/google_ai.py` and `cogs/google_ai_sys_prompts/gemini_2_5_pro_exp_03_25*.md`; Perplexity uses `sonar*` model names in `cogs/pplx-ai.py`; `cogs/bot-dm.py` honors `OPENAI_ASSISTANT_ID` and `OPENAI_TOKEN_COUNT_MODEL` from the environment (see `.env.example`).
