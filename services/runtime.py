"""Shared Nyanjou runtime attached to the bot as ``bot.nyanjou``.

Both Nyanjou cogs use the same instance, so voice sessions and the task
manager share one T3 connection, one task database and one voice bridge.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import discord

from .authz import Authorizer
from .config import NyanjouConfig
from .discord_audio import BridgeClient, BridgeProcess
from .t3_mcp_client import T3McpClient
from .task_manager import TaskManager
from .task_store import TaskRecord, TaskStore
from .tools import ToolDispatcher

log = logging.getLogger("bot.py.nyanjou")

FINAL_KINDS = {"completed", "failed", "timed_out", "cancelled"}
STATUS_EMOJI = {"completed": "✅", "failed": "❌", "timed_out": "⏱️", "cancelled": "🛑", "waiting_input": "⏸️",
                "auth_required": "🔑"}


class NyanjouRuntime:
    def __init__(self, bot: discord.Client, cfg: NyanjouConfig) -> None:
        self.bot = bot
        self.cfg = cfg
        self.authorizer = Authorizer(cfg.allowed_users_db, cfg.owner_id, owner_check=self._owner_check)
        self.owner_ids: set[int] = set()
        self.bridge_process: BridgeProcess | None = None
        self.bridge: BridgeClient | None = BridgeClient(
            f"ws://{cfg.bridge_host}:{cfg.bridge_port}", cfg.bridge_token
        )
        self.t3: T3McpClient | None = (
            T3McpClient(cfg.t3_mcp_url, cfg.t3_token_file, cfg.t3_static_token) if cfg.t3_configured else None
        )
        self.store = TaskStore(cfg.tasks_db)
        strategy = None
        if cfg.t3_workspace_strategy == "worktree":
            strategy = {"type": "worktree", **({"baseRef": cfg.t3_base_ref} if cfg.t3_base_ref else {})}
        elif cfg.t3_workspace_strategy == "root":
            strategy = {"type": "root"}
        self.tasks = TaskManager(
            self.store, self.t3,
            max_parallel=cfg.max_parallel_tasks,
            task_timeout_s=cfg.task_timeout_s,
            wait_slice_ms=cfg.t3_wait_slice_ms,
            workspace_strategy=strategy,
            runtime_mode=cfg.t3_runtime_mode or None,
        )
        self.dispatcher = ToolDispatcher(
            self.tasks if self.t3 else None,
            is_authorized=self.authorizer.is_authorized,
            is_owner=self.authorizer.is_owner,
            default_project_id=cfg.t3_project_id,
            allow_scratch=cfg.t3_allow_scratch,
            list_projects=self.t3.list_projects if self.t3 else None,
        )
        self.sessions: dict[int, Any] = {}  # guild_id -> VoiceSession
        self.prefs: dict[int, dict[str, str]] = {}  # guild_id -> {"voice":..., "model":...}
        self._started = False
        self._start_lock = asyncio.Lock()
        self.startup_errors: list[str] = []
        self._refs = 0

    async def _owner_check(self, user_id: int) -> bool:
        if user_id in self.owner_ids:
            return True
        user = self.bot.get_user(user_id) or discord.Object(id=user_id)
        try:
            ok = await self.bot.is_owner(user)  # type: ignore[arg-type]
        except Exception:
            return False
        if ok:
            self.owner_ids.add(user_id)
        return ok

    def is_authorized_cached(self, user_id: int) -> bool:
        return self.authorizer.is_authorized_cached(user_id, self.owner_ids)

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            self._started = True
            # Resolve owners once so the audio hot path never awaits.
            with contextlib.suppress(Exception):
                app = await self.bot.application_info()
                if app.team:
                    self.owner_ids |= {m.id for m in app.team.members}
                elif app.owner:
                    self.owner_ids.add(app.owner.id)
            if self.cfg.owner_id:
                self.owner_ids.add(self.cfg.owner_id)

            if self.cfg.bridge_autostart:
                self.bridge_process = BridgeProcess(
                    self.cfg.bridge_node, self.cfg.bridge_dir, self.cfg.bridge_host,
                    self.cfg.bridge_port, self.cfg.bridge_token, debug=self.cfg.debug,
                )
                try:
                    await self.bridge_process.start()
                except Exception as exc:
                    self.startup_errors.append(f"Voice-Bridge: {exc}")
                    log.error("voice bridge not started: %s", exc)
                    self.bridge_process = None
            if self.bridge is not None:
                await self.bridge.start()
            if self.t3 is not None:
                await self.t3.start()
            self.tasks.add_listener(self._notify_discord)
            self.tasks.add_listener(self._notify_sessions)
            await self.tasks.start()
            log.info("Nyanjou runtime started (T3=%s, live=%s)", self.cfg.t3_configured, self.cfg.live_configured)

    async def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        for session in list(self.sessions.values()):
            with contextlib.suppress(Exception):
                await session.stop("shutdown")
        self.sessions.clear()
        await self.tasks.close()
        if self.t3 is not None:
            await self.t3.close()
        if self.bridge is not None:
            await self.bridge.close()
        if self.bridge_process is not None:
            await self.bridge_process.stop()
        self.store.close()
        self._started = False

    # ------------------------------------------------------------------ notifications
    async def _notify_sessions(self, rec: TaskRecord, kind: str) -> None:
        session = self.sessions.get(rec.guild_id) if rec.guild_id else None
        if session is not None:
            await session.on_task_event(rec, kind)

    async def _notify_discord(self, rec: TaskRecord, kind: str) -> None:
        if kind not in FINAL_KINDS | {"waiting_input", "auth_required"} or self.cfg.results_to == "none":
            return
        if kind == "cancelled" and rec.source == "slash":
            return  # the /cancel response already reports it
        embed = task_embed(rec, kind)
        target = None
        if self.cfg.results_to == "channel" and rec.channel_id:
            target = self.bot.get_channel(rec.channel_id)
        if target is None:
            with contextlib.suppress(Exception):
                target = self.bot.get_user(rec.user_id) or await self.bot.fetch_user(rec.user_id)
        if target is None:
            return
        try:
            mention = f"<@{rec.user_id}>" if self.cfg.results_to == "channel" else None
            await target.send(content=mention, embed=embed,
                              allowed_mentions=discord.AllowedMentions(users=[discord.Object(rec.user_id)]))
            if kind in FINAL_KINDS:
                self.store.update(rec.id, notified=True)
        except Exception:
            log.exception("could not post task result for #%s", rec.short_id)


def task_embed(rec: TaskRecord, kind: str | None = None) -> discord.Embed:
    kind = kind or rec.status
    color = {"completed": 0x40A02B, "failed": 0xD20F39, "timed_out": 0xFE640B, "cancelled": 0x7C7F93}.get(
        kind, 0x1E66F5)
    embed = discord.Embed(
        title=f"{STATUS_EMOJI.get(kind, '🛠️')} Aufgabe #{rec.short_id}: {rec.title}"[:256],
        color=color,
    )
    embed.add_field(name="Status", value=rec.status, inline=True)
    embed.add_field(name="Von", value=f"<@{rec.user_id}>", inline=True)
    embed.add_field(name="Projekt", value="Neues Scratch-Projekt" if rec.scratch else (rec.project_id or "–"),
                    inline=True)
    if rec.result:
        embed.description = rec.result[:3900]
    if rec.error:
        embed.add_field(name="Fehler", value=rec.error[:1000], inline=False)
    if rec.thread_id:
        embed.set_footer(text=f"T3-Thread {rec.thread_id}")
    return embed


def get_runtime(bot: discord.Client) -> NyanjouRuntime:
    rt = getattr(bot, "nyanjou", None)
    if rt is None:
        rt = NyanjouRuntime(bot, NyanjouConfig.from_env())
        bot.nyanjou = rt  # type: ignore[attr-defined]
    return rt


def acquire_runtime(bot: discord.Client) -> NyanjouRuntime:
    """Called by each Nyanjou cog on load; the runtime closes when the last cog unloads."""
    rt = get_runtime(bot)
    rt._refs = getattr(rt, "_refs", 0) + 1
    return rt


def release_runtime(bot: discord.Client) -> None:
    rt = getattr(bot, "nyanjou", None)
    if rt is None:
        return
    rt._refs = getattr(rt, "_refs", 1) - 1
    if rt._refs <= 0:
        bot.nyanjou = None  # type: ignore[attr-defined]
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(rt.close())
