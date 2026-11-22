"""Per-guild voice session: Discord audio <-> GPT-Live, plus tool routing.

Audio path (all in-memory, nothing is recorded to disk):

  bridge capture frames (48 kHz stereo, per user)
    -> per-user jitter buffers -> 20 ms clock tick
    -> SpeakerGate (floor control / mix) + VAD
    -> SpeakerTimeline (who spoke when, on the Live session clock)
    -> 24 kHz mono -> GPT-Live

  GPT-Live audio deltas (24 kHz mono) -> 48 kHz stereo -> bridge playback

Barge-in: if a forwarded speaker talks while Nyanjou is speaking, local
playback is flushed and further model audio is dropped until they pause.
GPT-Live is full duplex and stops on its own. Flushing avoids playing audio
that was already buffered.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import numpy as np

from .audio import FRAME_MS, DiscordToLive, LiveToDiscord, is_silence, rms_dbfs, stereo48k_to_mono
from .config import NyanjouConfig
from .live_client import FunctionCall, LiveCallbacks, LiveClient, LiveSettings
from .prompts import backend_instructions, live_instructions
from .speaker_gate import Attribution, SpeakerGate, SpeakerTimeline
from .task_store import TaskRecord
from .tools import TOOL_DEFINITIONS, ToolContext, ToolDispatcher

log = logging.getLogger("bot.py.nyanjou.voice")

JITTER_FRAMES = 25  # max buffered frames per user (500 ms); older audio is dropped


@dataclass
class SessionStats:
    started_at: float = field(default_factory=time.time)
    frames_in: int = 0
    frames_dropped: int = 0
    frames_sent: int = 0
    audio_out_bytes: int = 0
    barge_ins: int = 0
    tool_calls: int = 0
    tool_denials: int = 0
    last_speech_at: float = field(default_factory=time.time)


class VoiceSession:
    def __init__(
        self,
        *,
        cfg: NyanjouConfig,
        guild_id: int,
        voice_channel_id: int,
        text_channel_id: int | None,
        started_by: int,
        bridge,  # BridgeClient-like: send(), send_playback(), handlers
        dispatcher: ToolDispatcher,
        is_authorized_cached: Callable[[int], bool],
        member_info: Callable[[int], tuple[str, bool] | None],
        voice: str | None = None,
        model: str | None = None,
        live_factory: Callable[[LiveSettings, LiveCallbacks], LiveClient] = LiveClient,
        connect_voice: Callable[[], Awaitable[Any]] | None = None,
        disconnect_voice: Callable[[], Awaitable[None]] | None = None,
        on_closed: Callable[["VoiceSession", str], Awaitable[None]] | None = None,
    ) -> None:
        self.cfg = cfg
        self.guild_id = guild_id
        self.voice_channel_id = voice_channel_id
        self.text_channel_id = text_channel_id
        self.started_by = started_by
        self.bridge = bridge
        self.dispatcher = dispatcher
        self._is_authorized_cached = is_authorized_cached
        self._member_info = member_info
        self.voice = voice or cfg.voice_name
        self.model = model or cfg.voice_model
        self._live_factory = live_factory
        self._connect_voice = connect_voice
        self._disconnect_voice = disconnect_voice
        self._on_closed = on_closed

        self.gate = SpeakerGate(cfg.speaker_mode, cfg.vad_threshold_dbfs, cfg.floor_release_ms)
        self.timeline = SpeakerTimeline()
        self.to_live = DiscordToLive()
        self.to_discord = LiveToDiscord()
        self.stats = SessionStats()
        self.live: LiveClient | None = None
        self.clock_ms = 0
        self.playing = False
        self.voice_state: str | None = None
        self.closed = False
        self._buffers: dict[int, deque[bytes]] = {}
        self._delegations: OrderedDict[str, Attribution] = OrderedDict()
        self._playback_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=500)
        self._tasks: list[asyncio.Task] = []
        self._speech_run = 0
        self._suppress_output = False
        self._last_announced: int | None = None
        self._last_announce_at = 0.0
        self._auth_cache: dict[int, tuple[bool, float]] = {}
        # Serializes Live client replacement (/voice, /model) with start and teardown.
        self._live_lock = asyncio.Lock()
        self._teardown: asyncio.Task | None = None
        # Completed (not flushed) utterances of Nyanjou in the current Live session: (start_ms, end_ms).
        self._utterances: deque[tuple[int, int]] = deque(maxlen=64)
        self._utterance_start: int | None = None
        self._utterance_flushed = False
        # Everyone whose audio reached GPT-Live while this conversation's history exists. Kept across
        # Live reconnects and restarts (history is carried over); never reset during the session.
        self._contributors: set[int] = set()

    # ------------------------------------------------------------------ lifecycle
    def _live_settings(self) -> LiveSettings:
        return LiveSettings(
            api_key=self.cfg.openai_api_key,
            url=self.cfg.live_url,
            model=self.model,
            voice=self.voice,
            instructions=live_instructions(self.cfg),
            backend_model=self.cfg.backend_model,
            backend_instructions=backend_instructions(self.cfg),
            tools=TOOL_DEFINITIONS,
            reasoning_effort=self.cfg.backend_reasoning_effort or None,
            web_search=self.cfg.backend_web_search,
            safety_identifier=f"discord-guild-{self.guild_id}",
        )

    def _callbacks(self) -> LiveCallbacks:
        return LiveCallbacks(
            on_audio=self._on_live_audio,
            on_input_transcript=self._on_input_transcript,
            on_output_transcript=lambda t: None,
            on_delegation=self._on_delegation,
            on_function_call=self._on_function_call,
            on_session_started=self._on_live_started,
            on_session_lost=self._on_live_lost,
            on_error=lambda err: None,
        )

    async def start(self) -> None:
        # The whole startup (Live, voice connection, background tasks) holds the lock, so a concurrent
        # stop() waits for it and then tears everything down; nothing starts on a closed session.
        failed: BaseException | None = None
        async with self._live_lock:
            if self.closed:
                raise RuntimeError("voice session already closed")
            self.bridge.handlers[self.guild_id] = self
            try:
                self.live = self._live_factory(self._live_settings(), self._callbacks())
                await self.live.start()
                if self._connect_voice is not None:
                    await self._connect_voice()
            except BaseException as exc:
                failed = exc
            else:
                if not self.closed:
                    self._tasks = [
                        asyncio.create_task(self._clock(), name=f"nyanjou-clock-{self.guild_id}"),
                        asyncio.create_task(self._playback_sender(), name=f"nyanjou-playback-{self.guild_id}"),
                        asyncio.create_task(self._idle_watchdog(), name=f"nyanjou-idle-{self.guild_id}"),
                    ]
        if failed is not None:
            await self.stop("start_failed", notify=False)
            raise failed
        log.info("voice session started in guild %s (model=%s voice=%s)", self.guild_id, self.model, self.voice)

    async def stop(self, reason: str = "leave", notify: bool = True) -> None:
        # One shared teardown; shielded so a cancelled caller can't abandon it halfway.
        self.closed = True
        if self._teardown is None:
            self._teardown = asyncio.get_running_loop().create_task(self._do_stop(reason, notify))
        if self._teardown is not asyncio.current_task():
            await asyncio.shield(self._teardown)

    async def _do_stop(self, reason: str, notify: bool) -> None:
        log.info("stopping voice session in guild %s (%s)", self.guild_id, reason)
        # Waits for an in-flight start() or restart_live() before tearing down.
        async with self._live_lock:
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            if self.bridge.handlers.get(self.guild_id) is self:
                del self.bridge.handlers[self.guild_id]
            if self.live is not None:
                with contextlib.suppress(Exception):
                    await self.live.close()
            if self._disconnect_voice is not None:
                with contextlib.suppress(Exception):
                    await self._disconnect_voice()
            self._buffers.clear()
            self._delegations.clear()
            self.timeline.reset()
        if notify and self._on_closed is not None:
            with contextlib.suppress(Exception):
                await self._on_closed(self, reason)

    async def restart_live(self, *, voice: str | None = None, model: str | None = None) -> None:
        """Voice and model are fixed per Live session; start a new one (history is kept)."""
        async with self._live_lock:
            if self.closed:
                return
            if voice:
                self.voice = voice
            if model:
                self.model = model
            old = self.live
            history = list(old.history) if old else []
            if old is not None:
                await old.close()
            self.live = self._live_factory(self._live_settings(), self._callbacks())
            self.live.history.extend(history)
            await self.live.start()

    # ------------------------------------------------------------------ bridge input
    def _authorized(self, user_id: int) -> bool:
        cached = self._auth_cache.get(user_id)
        now = time.monotonic()
        if cached and now - cached[1] < 30:
            return cached[0]
        ok = self._is_authorized_cached(user_id)
        self._auth_cache[user_id] = (ok, now)
        return ok

    def on_capture(self, user_id: int, pcm48_stereo: bytes) -> None:
        if self.closed:
            return
        info = self._member_info(user_id)
        if info is None:
            return  # not a known member of this guild: ignore
        _, is_bot = info
        if is_bot:
            return  # never listen to bots, including ourselves
        if not self.cfg.allow_guest_conversation and not self._authorized(user_id):
            return
        buf = self._buffers.get(user_id)
        if buf is None:
            buf = self._buffers[user_id] = deque()
        if len(buf) >= JITTER_FRAMES:
            buf.popleft()
            self.stats.frames_dropped += 1
        buf.append(pcm48_stereo)
        self.stats.frames_in += 1

    def on_bridge_event(self, msg: dict[str, Any]) -> None:
        op = msg.get("op")
        if op == "connection_state":
            self.voice_state = msg.get("state")
        elif op == "playback_idle":
            self.playing = False
            if self._utterance_start is not None and not self._utterance_flushed:
                self._utterances.append((self._utterance_start, self.clock_ms))
            self._utterance_start = None
            self._utterance_flushed = False
        elif op == "speaking_end":
            pass
        elif op in {"left", "bridge_lost"} and not self.closed:
            reason = msg.get("reason") or op
            if reason not in {"leave", "moved"}:
                asyncio.create_task(self.stop(f"bridge:{reason}"))
        elif op == "error":
            log.warning("bridge error in guild %s: %s", self.guild_id, msg.get("message"))

    def on_member_left(self, user_id: int) -> None:
        self._buffers.pop(user_id, None)
        self.gate.drop_user(user_id)
        with contextlib.suppress(Exception):
            asyncio.create_task(self.bridge.send({"op": "forget_user", "guildId": str(self.guild_id), "userId": str(user_id)}))

    # ------------------------------------------------------------------ clock
    def tick(self) -> np.ndarray | None:
        """One 20 ms step. Returns the forwarded mono 48 kHz frame (or None). Public for tests."""
        frames: dict[int, tuple[np.ndarray, float]] = {}
        for uid in list(self._buffers):
            buf = self._buffers[uid]
            if not buf:
                continue
            mono = stereo48k_to_mono(buf.popleft())
            if mono.size != 960:
                continue
            frames[uid] = (mono, rms_dbfs(mono))
        result = self.gate.process(frames)
        self.timeline.record(self.clock_ms, result.sources, result.speakers)
        self._contributors |= result.sources

        if result.speakers:
            self.stats.last_speech_at = time.time()
            self._speech_run += 1
            if (self.cfg.barge_in and self.playing
                    and self._speech_run * FRAME_MS >= self.cfg.barge_in_ms and not self._suppress_output):
                self._barge_in()
        else:
            self._speech_run = 0
            if self._suppress_output and result.floor_holder is None:
                self._suppress_output = False

        if result.floor_holder is not None and result.floor_holder != self._last_announced:
            self._announce_speaker(result.floor_holder)
        self.clock_ms += FRAME_MS
        return result.audio

    async def _clock(self) -> None:
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        silence = np.zeros(960, dtype=np.float32)
        while True:
            next_t += FRAME_MS / 1000
            if self.live is not None and self.live.connected:
                audio = self.tick()
                pcm = self.to_live.convert_mono(audio if audio is not None else silence)
                await self.live.send_audio(pcm)
                self.stats.frames_sent += 1
            else:
                # Not connected: drop input, so stale speech isn't sent after a reconnect.
                for buf in self._buffers.values():
                    buf.clear()
            delay = next_t - loop.time()
            if delay < -0.2:
                next_t = loop.time()  # fell behind (GC, CPU spike): resync, don't burst
                delay = 0
            await asyncio.sleep(max(0.0, delay))

    def _barge_in(self) -> None:
        self._utterance_flushed = True  # an interrupted utterance never counts as "question played"
        self.stats.barge_ins += 1
        self._suppress_output = True
        self.playing = False
        while not self._playback_q.empty():
            self._playback_q.get_nowait()
        self.to_discord.reset()
        asyncio.create_task(self._send_flush())

    async def _send_flush(self) -> None:
        with contextlib.suppress(Exception):
            await self.bridge.send({"op": "flush", "guildId": str(self.guild_id)})

    def _announce_speaker(self, user_id: int) -> None:
        now = time.monotonic()
        if now - self._last_announce_at < 3 and self._last_announced is not None:
            return
        self._last_announced = user_id
        self._last_announce_at = now
        info = self._member_info(user_id)
        if info and self.live is not None:
            name = info[0]
            asyncio.create_task(self._safe(self.live.append_thinking(f"Jetzt spricht: {name}")))

    @staticmethod
    async def _safe(coro) -> None:
        with contextlib.suppress(Exception):
            await coro

    # ------------------------------------------------------------------ live output
    def _on_live_audio(self, pcm24: bytes) -> None:
        if self._suppress_output or self.closed:
            return
        if is_silence(pcm24):
            # GPT-Live streams audio continuously, silence included. Dropping it lets the bridge go idle:
            # Discord's speaking indicator turns off, and utterance ends (playback_idle) are detected.
            return
        pcm48 = self.to_discord.convert(pcm24)
        if not pcm48:
            return
        if not self.playing and self._utterance_start is None:
            self._utterance_start = self.clock_ms
        self.playing = True
        self.stats.audio_out_bytes += len(pcm48)
        try:
            self._playback_q.put_nowait(pcm48)
        except asyncio.QueueFull:
            self.stats.frames_dropped += 1

    async def _playback_sender(self) -> None:
        while True:
            chunk = await self._playback_q.get()
            if chunk is None:
                return
            await self.bridge.send_playback(self.guild_id, chunk)

    def _on_input_transcript(self, text: str, start_ms: int, end_ms: int) -> None:
        if self.cfg.debug:
            log.debug("guild %s heard [%d-%d]: %s", self.guild_id, start_ms, end_ms, text)

    def _on_live_started(self, session_id: str) -> None:
        self._utterances.clear()  # markers belong to the old session clock
        self._utterance_start = None
        self._utterance_flushed = False
        # New session -> new clock. Old attributions are meaningless now.
        self.clock_ms = 0
        self.timeline.reset()
        self.gate.reset()
        self._delegations.clear()
        self._last_announced = None
        for buf in self._buffers.values():
            buf.clear()

    def _on_live_lost(self, reason: str) -> None:
        log.warning("GPT-Live session lost in guild %s: %s (reconnecting)", self.guild_id, reason)
        self.playing = False

    # ------------------------------------------------------------------ tools
    def _on_delegation(self, delegation_id: str, offset_ms: int, target: str) -> None:
        att = self.timeline.attribute(offset_ms, self.cfg.attribution_window_ms, self.cfg.attribution_tolerance_ms)
        if att.user_id is not None:
            att.others_recent = frozenset(self._contributors - {att.user_id})
        self._delegations[delegation_id] = att
        while len(self._delegations) > 64:
            self._delegations.popitem(last=False)
        log.debug("delegation %s at %dms attributed to %s (%s)", delegation_id, offset_ms, att.user_id, att.reason)

    def attribution_for(self, delegation_id: str | None) -> Attribution:
        # Only delegations recorded in the current Live session count. A missing, unknown or
        # evicted ID never falls back to "whoever spoke recently".
        if delegation_id and delegation_id in self._delegations:
            return self._delegations[delegation_id]
        return Attribution(None, "unknown_delegation")

    def playback_idle_after(self, t_ms: int) -> int | None:
        """End of Nyanjou's first fully played utterance that *started* after ``t_ms`` (for a
        confirmation: the spoken question). Flushed utterances and earlier sessions never count."""
        return next((end for start, end in self._utterances if start > t_ms), None)

    async def _on_function_call(self, call: FunctionCall) -> str:
        self.stats.tool_calls += 1
        att = self.attribution_for(call.delegation_id)
        name = None
        if att.user_id is not None:
            info = self._member_info(att.user_id)
            name = info[0] if info else None
        ctx = ToolContext(
            guild_id=self.guild_id, channel_id=self.text_channel_id, attribution=att, user_display=name,
            delegation_id=call.delegation_id, live_session_id=self.live.session_id if self.live else None,
            now_ms=self.clock_ms, speech_ms_between=self.timeline.speech_ms_between,
            playback_idle_after=self.playback_idle_after,
        )
        out = await self.dispatcher.dispatch(call.name, call.arguments, ctx)
        if '"ok": false' in out:
            self.stats.tool_denials += 1
        return out

    async def on_task_event(self, rec: TaskRecord, kind: str) -> None:
        """Tell GPT-Live about task progress for tasks started in this guild."""
        if self.closed or self.live is None or rec.guild_id != self.guild_id:
            return
        info = self._member_info(rec.user_id)
        who = info[0] if info else "jemand"
        text = None
        if kind == "completed":
            summary = (rec.result or "ohne Zusammenfassung").strip()
            text = f"Aufgabe #{rec.short_id} '{rec.title}' von {who} ist fertig. Ergebnis von T3 Code: {summary[:1200]}"
        elif kind in {"failed", "timed_out"}:
            text = f"Aufgabe #{rec.short_id} '{rec.title}' von {who} ist fehlgeschlagen: {rec.error or kind}"
        elif kind == "waiting_input":
            text = (f"Aufgabe #{rec.short_id} '{rec.title}' wartet in T3 Code auf eine Eingabe oder Freigabe. "
                    "Bitte in der T3-Code-App nachsehen.")
        elif kind == "auth_required":
            text = "Die Verbindung zu T3 Code braucht eine neue Anmeldung durch den Bot-Admin."
        if text:
            await self._safe(self.live.append_commentary(text))

    # ------------------------------------------------------------------ housekeeping
    async def _idle_watchdog(self) -> None:
        while True:
            await asyncio.sleep(15)
            if self.cfg.idle_timeout_s > 0 and time.time() - self.stats.last_speech_at > self.cfg.idle_timeout_s:
                log.info("guild %s idle for %ss; leaving", self.guild_id, self.cfg.idle_timeout_s)
                asyncio.create_task(self.stop("idle"))
                return

    def status(self) -> dict[str, Any]:
        live = self.live
        return {
            "guild_id": self.guild_id,
            "voice_channel_id": self.voice_channel_id,
            "voice_connection": self.voice_state,
            "live_connected": bool(live and live.connected),
            "live_session_id": live.session_id if live else None,
            "live_reconnects": live.reconnects if live else 0,
            "live_last_error": live.last_error if live else None,
            "model": self.model,
            "voice": self.voice,
            "speaker_mode": self.cfg.speaker_mode,
            "floor_holder": self.gate.holder,
            "uptime_s": int(time.time() - self.stats.started_at),
            "frames_in": self.stats.frames_in,
            "frames_sent": self.stats.frames_sent,
            "barge_ins": self.stats.barge_ins,
            "tool_calls": self.stats.tool_calls,
            "tool_denials": self.stats.tool_denials,
        }
