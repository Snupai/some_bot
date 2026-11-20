"""Nyanjou T3 Code tasks: /t3task, /tasks, /cancel.

Voice-started tasks go through the same TaskManager (services/task_manager.py).
Text commands use the interaction author's Discord user ID; no speaker
attribution is needed.
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from services.runtime import NyanjouRuntime, acquire_runtime, release_runtime, task_embed
from services.task_store import ACTIVE_STATUSES, TaskRecord
from services.tools import looks_risky

log = logging.getLogger("bot.py.nyanjou.tasks_cog")


class NyanjouTasks(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot
        self.rt: NyanjouRuntime = acquire_runtime(bot)

    def cog_unload(self) -> None:
        release_runtime(self.bot)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.rt.start()

    async def _allowed(self, ctx: discord.ApplicationContext) -> bool:
        if await self.rt.authorizer.is_authorized(ctx.author.id):
            return True
        await ctx.respond("Du bist für Nyanjou-Aufgaben nicht freigeschaltet.", ephemeral=True)
        return False

    @discord.slash_command(name="t3task", description="Coding-Aufgabe an T3 Code übergeben")
    async def t3task(
        self,
        ctx: discord.ApplicationContext,
        aufgabe: discord.Option(str, "Was soll T3 Code erledigen?"),  # type: ignore[valid-type]
        projekt: discord.Option(str, "T3-Projektname oder -ID (optional)", required=False, default=None),  # type: ignore[valid-type]
        neues_projekt: discord.Option(bool, "Als neues Scratch-Projekt starten", required=False, default=False),  # type: ignore[valid-type]
        bestaetigt: discord.Option(bool, "Riskante Aktion ausdrücklich bestätigen", required=False, default=False),  # type: ignore[valid-type]
    ) -> None:
        if not await self._allowed(ctx):
            return
        rt = self.rt
        if rt.t3 is None:
            await ctx.respond("T3 Code ist nicht konfiguriert (T3_MCP_URL).", ephemeral=True)
            return
        if looks_risky(aufgabe) and not bestaetigt:
            await ctx.respond(
                "Das sieht nach einer riskanten oder irreversiblen Aktion aus. "
                "Wenn du sicher bist, führe den Befehl erneut mit `bestaetigt: True` aus.",
                ephemeral=True,
            )
            return
        await ctx.defer(ephemeral=True)
        await rt.start()
        scratch = bool(neues_projekt) and rt.cfg.t3_allow_scratch
        project_id = None
        if not scratch:
            project_id, err = await rt.dispatcher.resolve_project(projekt)
            if err == "no_default_project" and rt.cfg.t3_allow_scratch:
                scratch = True
            elif err:
                await ctx.followup.send(f"Projekt nicht eindeutig/unbekannt: {err}", ephemeral=True)
                return
        title = aufgabe.strip().splitlines()[0][:80]
        prompt = (
            f"{aufgabe}\n\n---\nAuftrag per Discord (/t3task) von {ctx.author} (ID {ctx.author.id}). "
            "Arbeite eigenständig und fasse am Ende kurz zusammen, was erledigt wurde."
        )
        rec = TaskRecord(user_id=ctx.author.id, title=title, prompt=prompt, source="slash",
                         guild_id=ctx.guild_id, channel_id=ctx.channel_id, project_id=project_id, scratch=scratch)
        try:
            rec = await rt.tasks.submit(rec)
        except Exception as exc:
            await ctx.followup.send(f"Konnte die Aufgabe nicht anlegen: {exc}", ephemeral=True)
            return
        await ctx.followup.send(
            f"Aufgabe **#{rec.short_id}** ist in der Warteschlange und wird an T3 Code übergeben. "
            "Du bekommst eine Nachricht, wenn sie fertig ist.",
            ephemeral=True,
        )

    @discord.slash_command(name="tasks", description="Laufende Nyanjou/T3-Code-Aufgaben anzeigen")
    async def tasks(
        self,
        ctx: discord.ApplicationContext,
        alle: discord.Option(bool, "Auch abgeschlossene Aufgaben zeigen", required=False, default=False),  # type: ignore[valid-type]
    ) -> None:
        if not await self._allowed(ctx):
            return
        is_owner = await self.rt.authorizer.is_owner(ctx.author.id)
        statuses = None if alle else ACTIVE_STATUSES
        recs = self.rt.tasks.list(
            guild_id=ctx.guild_id if ctx.guild_id else None,
            user_id=None if (is_owner and ctx.guild_id) else ctx.author.id,
            statuses=statuses, limit=15,
        )
        if not recs:
            await ctx.respond("Keine Aufgaben gefunden.", ephemeral=True)
            return
        embed = discord.Embed(title="Nyanjou-Aufgaben", color=0x1E66F5)
        for r in recs:
            value = f"`{r.status}` · <@{r.user_id}> · {'Scratch' if r.scratch else (r.project_id or '–')}"
            if r.error and r.status != "completed":
                value += f"\n{r.error[:120]}"
            embed.add_field(name=f"#{r.short_id} {r.title[:80]}", value=value, inline=False)
        q = self.rt.tasks.queued_count
        if q:
            embed.set_footer(text=f"{q} Aufgabe(n) in der Warteschlange")
        await ctx.respond(embed=embed, ephemeral=True)

    @discord.slash_command(name="cancel", description="Eine laufende T3-Code-Aufgabe abbrechen")
    async def cancel(
        self,
        ctx: discord.ApplicationContext,
        nummer: discord.Option(int, "Aufgabennummer (siehe /tasks)"),  # type: ignore[valid-type]
    ) -> None:
        if not await self._allowed(ctx):
            return
        rec = self.rt.tasks.get(nummer)
        is_owner = await self.rt.authorizer.is_owner(ctx.author.id)
        if rec is None or (rec.user_id != ctx.author.id and not is_owner):
            await ctx.respond("Keine passende Aufgabe gefunden.", ephemeral=True)
            return
        if not rec.active:
            await ctx.respond(f"Aufgabe #{rec.short_id} ist bereits `{rec.status}`.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        try:
            rec = await self.rt.tasks.cancel(rec, reason=f"Per /cancel von {ctx.author.id}")
        except Exception as exc:
            await ctx.followup.send(f"Abbrechen fehlgeschlagen: {exc}", ephemeral=True)
            return
        await ctx.followup.send(embed=task_embed(rec, "cancelled"), ephemeral=True)


def setup(bot: discord.Bot) -> None:
    bot.add_cog(NyanjouTasks(bot))
