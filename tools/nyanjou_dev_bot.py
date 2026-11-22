#!/usr/bin/env python3
"""Run Nyanjou on its own, next to a running production bot that uses the same token.

    uv run python tools/nyanjou_dev_bot.py --guild <test-server-id> [--env-file ../some_bot/.env]

* Loads only the Nyanjou cogs (no RSS, YouTube, music … so nothing is posted twice).
* Registers Nyanjou's slash commands as **guild commands in one server** and never runs a global
  command sync, so the production bot's global commands stay as they are.
* The production bot's Pycord auto-sync removes guild commands whenever it reconnects. A watcher
  re-registers them within about 20 s.
* `--cleanup` removes the guild commands again and exits.

Configuration comes from the environment / an env file (same variables as the bot, see .env.example).
OPENAI_API_KEY and the T3 settings can be set in a separate env file with `--extra-env`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from dotenv import load_dotenv  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--guild", type=int, required=True, help="server (guild) ID to register the commands in")
    ap.add_argument("--env-file", default=str(REPO / ".env"), help="env file with BOT_TOKEN etc.")
    ap.add_argument("--extra-env", action="append", default=[], help="additional env file(s), e.g. Nyanjou secrets")
    ap.add_argument("--cleanup", action="store_true", help="remove the guild commands and exit")
    args = ap.parse_args()

    load_dotenv(args.env_file)
    for extra in args.extra_env:
        load_dotenv(extra, override=True)
    token = os.getenv("BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("BOT_TOKEN missing (use --env-file)")
        return 2

    import discord

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    intents = discord.Intents.default()
    intents.members = True
    intents.voice_states = True
    bot = discord.Bot(intents=intents, auto_sync_commands=False)
    bot.logger = logging.getLogger("bot.py")  # type: ignore[attr-defined]
    if not args.cleanup:
        bot.load_extension("cogs.nyanjou_voice")
        bot.load_extension("cogs.nyanjou_tasks")

    async def register() -> None:
        await bot.register_commands(list(bot.pending_application_commands), guild_id=args.guild, method="bulk",
                                    force=True)

    async def guard() -> None:
        while not bot.is_closed():
            await asyncio.sleep(20)
            try:
                if not await bot.http.get_guild_commands(bot.user.id, args.guild):
                    await register()
                    logging.getLogger("nyanjou.dev").info("re-registered guild commands (removed by another sync)")
            except Exception:
                logging.getLogger("nyanjou.dev").exception("command guard failed")

    @bot.event
    async def on_ready() -> None:
        if args.cleanup:
            await bot.http.bulk_upsert_guild_commands(bot.user.id, args.guild, [])
            print(f"Removed Nyanjou guild commands from {args.guild}")
            await bot.close()
            return
        await register()
        print(f"Nyanjou dev bot ready as {bot.user}; commands registered in guild {args.guild}")
        if not getattr(bot, "_nyanjou_guard", None):
            bot._nyanjou_guard = asyncio.create_task(guard())  # type: ignore[attr-defined]

    bot.run(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
