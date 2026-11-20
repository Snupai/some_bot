"""Nyanjou: authorized Discord -> T3 Code MCP task delegation.

Requires a T3 environment exposing /mcp and a previously authorized bearer token.
This intentionally does not bypass T3's OAuth consent.
"""
import asyncio
import json
import logging
import os
import sqlite3

import discord
import httpx
from discord.ext import commands
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

log = logging.getLogger(__name__)
DB_FILE = "allowed_users.sqlite"


def is_allowed(user_id: int, owner_id: int | None = None) -> bool:
    if owner_id is not None and user_id == owner_id:
        return True
    with sqlite3.connect(DB_FILE) as db:
        try:
            row = db.execute(
                "SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            return False
    return bool(row)


def readable(result) -> str:
    parts = [getattr(item, "text", "") for item in result.content]
    result_text = "\n".join(x for x in parts if x)
    if getattr(result, "isError", False):
        raise RuntimeError(result_text or "T3 Code returned an error")
    return result_text or "(no text returned)"


class NyanjouTasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active: set[asyncio.Task] = set()

    async def allowed(self, ctx):
        owner_id = getattr(self.bot, "owner_id", None)
        if not owner_id:
            owner_id = int(os.getenv("NYANJOU_OWNER_ID", "0")) or None
        if not is_allowed(ctx.author.id, owner_id):
            await ctx.respond("Du bist für Nyanjou-Aufgaben nicht freigeschaltet.", ephemeral=True)
            return False
        return True

    @discord.slash_command(name="t3task", description="Coding-Aufgabe automatisch an T3 Code übergeben")
    async def t3task(self, ctx: discord.ApplicationContext, aufgabe: discord.Option(str, "Was soll T3 Code erledigen?")):
        if not await self.allowed(ctx):
            return
        url = os.getenv("T3_MCP_URL", "").strip()
        project = os.getenv("T3_PROJECT_ID", "").strip()
        if not url or not project:
            await ctx.respond("T3 MCP ist noch nicht konfiguriert (T3_MCP_URL, T3_PROJECT_ID).", ephemeral=True)
            return
        if not url.startswith("https://") and not url.startswith("http://localhost:") and not url.startswith("http://127.0.0.1:"):
            await ctx.respond("T3_MCP_URL muss HTTPS oder localhost verwenden.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        await ctx.followup.send("Aufgabe wird an T3 Code übergeben …", ephemeral=True)
        job = asyncio.create_task(self.execute(ctx, aufgabe, url, project))
        self.active.add(job)
        job.add_done_callback(self.active.discard)

    async def execute(self, ctx, prompt: str, url: str, project_id: str):
        headers = {}
        token = os.getenv("T3_MCP_ACCESS_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(headers=headers, timeout=90.0, follow_redirects=False) as http:
                async with streamablehttp_client(url, http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        available = {tool.name for tool in (await session.list_tools()).tools}
                        if "t3_thread_launch" not in available:
                            raise RuntimeError("T3 MCP bietet t3_thread_launch nicht an.")
                        launch = await session.call_tool(
                            "t3_thread_launch",
                            {"projectId": project_id, "message": prompt},
                        )
                        details = readable(launch)
                        # Use the actual response from T3, never invent a thread ID.
                        await ctx.followup.send(
                            f"T3 Code hat geantwortet:\n```\n{details[:1500]}\n```",
                            ephemeral=True,
                        )
        except Exception as exc:
            log.exception("Nyanjou T3 task failed")
            await ctx.followup.send(
                f"T3 Code konnte die Aufgabe nicht starten: {type(exc).__name__}: {str(exc)[:900]}",
                ephemeral=True,
            )

    def cog_unload(self):
        for task in self.active:
            task.cancel()


def setup(bot):
    bot.add_cog(NyanjouTasks(bot))
