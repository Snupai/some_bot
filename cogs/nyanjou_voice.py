"""Nyanjou voice assistant: GPT-Live in Discord voice channels.

Commands: /join, /leave, /status, /voice, /model
See docs/nyanjou-integration.md for setup and the security model.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import discord
from discord.ext import commands

from services.config import GPT_LIVE_MODELS, GPT_LIVE_VOICES
from services.discord_audio import BridgeVoiceProtocol
from services.runtime import NyanjouRuntime, acquire_runtime, release_runtime
from services.voice_session import VoiceSession

log = logging.getLogger("bot.py.nyanjou.cog")

GUILD_ONLY = {"contexts": {discord.InteractionContextType.guild},
              "integration_types": {discord.IntegrationType.guild_install}}
EMPTY_CHANNEL_GRACE_S = 30


class NyanjouVoice(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot
        self.rt: NyanjouRuntime = acquire_runtime(bot)
        self._empty_timers: dict[int, asyncio.Task] = {}

    def cog_unload(self) -> None:
        for t in self._empty_timers.values():
            t.cancel()
        release_runtime(self.bot)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.rt.start()

    # ------------------------------------------------------------------ helpers
    async def _deny_unless_authorized(self, ctx: discord.ApplicationContext) -> bool:
        if await self.rt.authorizer.is_authorized(ctx.author.id):
            return False
        await ctx.respond("Du bist für Nyanjou nicht freigeschaltet. (Bot-Owner: `/usr-mgmt add_user`)", ephemeral=True)
        return True

    def _member_info_fn(self, guild: discord.Guild):
        def info(user_id: int) -> tuple[str, bool] | None:
            m = guild.get_member(user_id)
            if m is None:
                return None
            return m.display_name, m.bot
        return info

    async def _session_closed(self, session: VoiceSession, reason: str) -> None:
        if self.rt.sessions.get(session.guild_id) is session:
            del self.rt.sessions[session.guild_id]
        if reason in {"idle", "voice_state_disconnected"} or reason.startswith("bridge:"):
            channel = self.bot.get_channel(session.text_channel_id) if session.text_channel_id else None
            if channel is not None:
                text = {"idle": "Niemand hat mehr mit mir geredet, ich habe den Sprachkanal verlassen. 👋",
                        "voice_state_disconnected": "Ich wurde aus dem Sprachkanal getrennt."}.get(
                    reason, f"Sprachverbindung beendet ({reason}).")
                with contextlib.suppress(Exception):
                    await channel.send(text)

    async def _start_session(self, ctx: discord.ApplicationContext, channel: discord.VoiceChannel) -> VoiceSession:
        guild = ctx.guild
        prefs = self.rt.prefs.get(guild.id, {})
        holder: dict[str, BridgeVoiceProtocol] = {}

        async def connect_voice() -> None:
            proto = await channel.connect(cls=BridgeVoiceProtocol, timeout=20.0, reconnect=True)
            holder["proto"] = proto
            proto.on_lost = lambda reason: asyncio.create_task(session.stop(reason))

        async def disconnect_voice() -> None:
            proto = holder.get("proto") or guild.voice_client
            if isinstance(proto, BridgeVoiceProtocol):
                await proto.disconnect(force=True)

        session = VoiceSession(
            cfg=self.rt.cfg,
            guild_id=guild.id,
            voice_channel_id=channel.id,
            text_channel_id=ctx.channel_id,
            started_by=ctx.author.id,
            bridge=self.rt.bridge,
            dispatcher=self.rt.dispatcher,
            is_authorized_cached=self.rt.is_authorized_cached,
            member_info=self._member_info_fn(guild),
            voice=prefs.get("voice"),
            model=prefs.get("model"),
            connect_voice=connect_voice,
            disconnect_voice=disconnect_voice,
            on_closed=self._session_closed,
        )
        self.rt.sessions[guild.id] = session
        try:
            await session.start()
        except BaseException:
            self.rt.sessions.pop(guild.id, None)
            raise
        return session

    # ------------------------------------------------------------------ commands
    @discord.slash_command(name="join", description="Nyanjou betritt deinen Sprachkanal (GPT-Live)", **GUILD_ONLY)
    async def join(self, ctx: discord.ApplicationContext) -> None:
        if await self._deny_unless_authorized(ctx):
            return
        cfg = self.rt.cfg
        voice_state = getattr(ctx.author, "voice", None)
        channel = voice_state.channel if voice_state else None
        if channel is None:
            await ctx.respond("Geh zuerst in einen Sprachkanal.", ephemeral=True)
            return
        if not cfg.live_configured:
            await ctx.respond("OPENAI_API_KEY ist nicht gesetzt; GPT-Live ist nicht verfügbar.", ephemeral=True)
            return
        await ctx.defer()
        await self.rt.start()
        if self.rt.bridge is None or not self.rt.bridge.connected:
            try:
                await self.rt.bridge.wait_ready(timeout=8)  # type: ignore[union-attr]
            except Exception:
                errs = "; ".join(self.rt.startup_errors) or (self.rt.bridge.last_error if self.rt.bridge else "")
                await ctx.followup.send(f"Die Voice-Bridge läuft nicht. {errs}".strip())
                return
        existing = self.rt.sessions.get(ctx.guild.id)
        if existing is not None:
            if existing.voice_channel_id == channel.id and not existing.closed:
                await ctx.followup.send("Ich bin schon hier. 🐾")
                return
            await existing.stop("moved")
        vc = ctx.guild.voice_client
        if vc is not None and not isinstance(vc, BridgeVoiceProtocol):
            await ctx.followup.send("Der Bot ist in diesem Server bereits anderweitig mit Voice verbunden.")
            return
        try:
            session = await self._start_session(ctx, channel)
        except asyncio.TimeoutError:
            detail = self.rt.bridge.last_guild_error.pop(ctx.guild.id, None) if self.rt.bridge else None
            hint = (f"\nFehler der Voice-Bridge: `{detail}`\nPrüfe, ob der Server ausgehend TCP 2053/2083/2087/2096/8443 und "
                    "UDP 50000–65535 zu Discord erlaubt (siehe docs/nyanjou-integration.md → Troubleshooting).") if detail else ""
            await ctx.followup.send("Die Sprachverbindung konnte nicht rechtzeitig aufgebaut werden." + hint)
            return
        except Exception as exc:
            log.exception("join failed")
            await ctx.followup.send(f"Konnte die Sitzung nicht starten: {type(exc).__name__}: {str(exc)[:300]}")
            return
        guest = "an" if cfg.allow_guest_conversation else "aus"
        await ctx.followup.send(
            f"Nyanjou ist in **{channel.name}** (Modell `{session.model}`, Stimme `{session.voice}`). "
            f"Gäste-Unterhaltung: {guest}. Aufgaben starten können nur freigeschaltete Personen, "
            "und nur wenn sie eindeutig als Sprecher erkannt werden."
        )

    @discord.slash_command(name="leave", description="Nyanjou verlässt den Sprachkanal", **GUILD_ONLY)
    async def leave(self, ctx: discord.ApplicationContext) -> None:
        if await self._deny_unless_authorized(ctx):
            return
        session = self.rt.sessions.get(ctx.guild.id)
        if session is None:
            vc = ctx.guild.voice_client
            if isinstance(vc, BridgeVoiceProtocol):
                await vc.disconnect(force=True)
                await ctx.respond("Getrennt.", ephemeral=True)
            else:
                await ctx.respond("Ich bin in keinem Sprachkanal.", ephemeral=True)
            return
        await ctx.defer()
        await session.stop("leave")
        await ctx.followup.send("Tschüss! 👋 Laufende T3-Aufgaben arbeiten im Hintergrund weiter.")

    @discord.slash_command(name="status", description="Nyanjou: Voice-Status und aktive Aufgaben", **GUILD_ONLY)
    async def status(self, ctx: discord.ApplicationContext) -> None:
        # Task titles may be private; only authorized users see the status.
        if await self._deny_unless_authorized(ctx):
            return
        rt = self.rt
        embed = discord.Embed(title="Nyanjou-Status", color=0x8839EF)
        session = rt.sessions.get(ctx.guild.id) if ctx.guild else None
        if session is not None:
            s = session.status()
            ch = ctx.guild.get_channel(s["voice_channel_id"])
            embed.add_field(name="Voice", value=(
                f"Kanal: {ch.mention if ch else s['voice_channel_id']}\n"
                f"Verbindung: `{s['voice_connection']}`\n"
                f"GPT-Live: {'verbunden' if s['live_connected'] else 'getrennt'} (`{s['model']}`, Stimme `{s['voice']}`)\n"
                f"Reconnects: {s['live_reconnects']} · Barge-ins: {s['barge_ins']}\n"
                f"Tool-Aufrufe: {s['tool_calls']} (abgelehnt: {s['tool_denials']})\n"
                f"Laufzeit: {s['uptime_s'] // 60} min"
                + (f"\nLetzter Fehler: {s['live_last_error'][:200]}" if s["live_last_error"] else "")
            ), inline=False)
        else:
            embed.add_field(name="Voice", value="Nicht verbunden", inline=False)
        bridge = rt.bridge
        embed.add_field(name="Voice-Bridge", value="bereit" if bridge and bridge.connected else
                        f"nicht verbunden {('(' + bridge.last_error[:100] + ')') if bridge and bridge.last_error else ''}",
                        inline=True)
        if rt.t3 is None:
            t3_state = "nicht konfiguriert"
        elif rt.t3.connected:
            t3_state = f"verbunden ({len(rt.t3.tools)} Tools)"
        elif rt.t3.auth_required:
            t3_state = "Anmeldung nötig (`tools/nyanjou_t3_login.py`)"
        else:
            t3_state = f"getrennt ({(rt.t3.last_error or '')[:100]})"
        embed.add_field(name="T3 Code", value=t3_state, inline=True)
        active = rt.tasks.list(guild_id=ctx.guild.id if ctx.guild else None,
                               statuses=("queued", "starting", "running", "waiting_input"), limit=10)
        if active:
            embed.add_field(name=f"Aktive Aufgaben ({len(active)})", value="\n".join(
                f"#{r.short_id} · `{r.status}` · {r.title[:60]} · <@{r.user_id}>" for r in active)[:1024], inline=False)
        else:
            embed.add_field(name="Aktive Aufgaben", value="keine", inline=False)
        if rt.startup_errors:
            embed.add_field(name="Startprobleme", value="\n".join(rt.startup_errors)[:1024], inline=False)
        await ctx.respond(embed=embed, ephemeral=True)

    @discord.slash_command(name="voice", description="Stimme von Nyanjou wählen", **GUILD_ONLY)
    async def voice(
        self,
        ctx: discord.ApplicationContext,
        name: discord.Option(str, "GPT-Live-Stimme", choices=list(GPT_LIVE_VOICES)[:25]),  # type: ignore[valid-type]
    ) -> None:
        if await self._deny_unless_authorized(ctx):
            return
        self.rt.prefs.setdefault(ctx.guild.id, {})["voice"] = name
        session = self.rt.sessions.get(ctx.guild.id)
        if session is None:
            await ctx.respond(f"Stimme `{name}` wird beim nächsten /join verwendet.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        # The voice is fixed per Live session; restart the session (conversation history is kept).
        await session.restart_live(voice=name)
        await ctx.followup.send(f"Stimme auf `{name}` umgestellt.", ephemeral=True)

    @discord.slash_command(name="model", description="GPT-Live-Modell wählen", **GUILD_ONLY)
    async def model(
        self,
        ctx: discord.ApplicationContext,
        name: discord.Option(str, "Sprachmodell", choices=list(GPT_LIVE_MODELS)),  # type: ignore[valid-type]
    ) -> None:
        if await self._deny_unless_authorized(ctx):
            return
        self.rt.prefs.setdefault(ctx.guild.id, {})["model"] = name
        session = self.rt.sessions.get(ctx.guild.id)
        if session is None:
            await ctx.respond(f"Modell `{name}` wird beim nächsten /join verwendet.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        await session.restart_live(model=name)
        await ctx.followup.send(f"Modell auf `{name}` umgestellt.", ephemeral=True)

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        session = self.rt.sessions.get(member.guild.id)
        if session is None or member.id == self.bot.user.id:
            return
        left = before.channel and before.channel.id == session.voice_channel_id and (
            after.channel is None or after.channel.id != session.voice_channel_id)
        if left:
            session.on_member_left(member.id)
        channel = member.guild.get_channel(session.voice_channel_id)
        humans = [m for m in getattr(channel, "members", []) if not m.bot]
        gid = member.guild.id
        if not humans and gid not in self._empty_timers:
            self._empty_timers[gid] = asyncio.create_task(self._leave_if_still_empty(gid))
        elif humans and gid in self._empty_timers:
            self._empty_timers.pop(gid).cancel()

    async def _leave_if_still_empty(self, guild_id: int) -> None:
        try:
            await asyncio.sleep(EMPTY_CHANNEL_GRACE_S)
            session = self.rt.sessions.get(guild_id)
            if session is None:
                return
            guild = self.bot.get_guild(guild_id)
            channel = guild.get_channel(session.voice_channel_id) if guild else None
            if not [m for m in getattr(channel, "members", []) if not m.bot]:
                await session.stop("idle")
        finally:
            self._empty_timers.pop(guild_id, None)


def setup(bot: discord.Bot) -> None:
    bot.add_cog(NyanjouVoice(bot))
