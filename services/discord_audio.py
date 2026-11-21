"""Discord voice I/O through the local Node voice bridge.

Pycord 2.8 cannot receive voice under Discord's DAVE end-to-end encryption.
Its own ``start_recording`` warns "Voice reception is currently broken due to
Discord's DAVE (End-to-End Encryption) protocol"; see
https://github.com/Pycord-Development/pycord/issues/3139. So the voice UDP
connection runs in ``bridge/`` with @discordjs/voice, which handles DAVE for
send and receive.

The Python bot keeps the gateway connection. ``BridgeVoiceProtocol`` is a
regular Pycord ``VoiceProtocol``, the extension point Pycord documents for
external voice backends such as Lavalink. It forwards Discord's
VOICE_STATE_UPDATE and VOICE_SERVER_UPDATE payloads to the bridge and sends
the bridge's voice-state requests through Pycord's gateway connection.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import struct
from typing import Any, Protocol

import discord
import websockets

# Import the protocol base directly. ``discord.VoiceProtocol`` pulls in Pycord's
# own voice stack, which needs PyNaCl and davey. The bridge doesn't use them.
# Pycord's ``Connectable.connect`` imports from the same module for this reason.
from discord.voice._types import VoiceProtocol
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("bot.py.nyanjou.bridge")

KIND_CAPTURE = 0x01
KIND_PLAYBACK = 0x02
_CAPTURE_HEADER = struct.Struct(">BQQ")
_PLAYBACK_HEADER = struct.Struct(">BQ")
MIN_NODE = (22, 12)


def encode_playback(guild_id: int, pcm48_stereo: bytes) -> bytes:
    return _PLAYBACK_HEADER.pack(KIND_PLAYBACK, guild_id) + pcm48_stereo


def decode_capture(buf: bytes) -> tuple[int, int, bytes] | None:
    if len(buf) < _CAPTURE_HEADER.size or buf[0] != KIND_CAPTURE:
        return None
    _, guild_id, user_id = _CAPTURE_HEADER.unpack_from(buf)
    return guild_id, user_id, buf[_CAPTURE_HEADER.size:]


class GuildAudioHandler(Protocol):
    def on_capture(self, user_id: int, pcm48_stereo: bytes) -> None: ...
    def on_bridge_event(self, msg: dict[str, Any]) -> None: ...


class BridgeClient:
    """WebSocket client for the bridge, with reconnect and per-guild routing."""

    def __init__(self, url: str, token: str, *, connect=websockets.connect) -> None:
        self.url = url
        self.token = token
        self._connect = connect
        self._ws = None
        self._ready = asyncio.Event()
        self._runner: asyncio.Task | None = None
        self._closing = False
        self._send_lock = asyncio.Lock()
        self.handlers: dict[int, GuildAudioHandler] = {}
        self.protocols: dict[int, Any] = {}  # guild_id -> BridgeVoiceProtocol
        self.last_guild_error: dict[int, str] = {}  # guild_id -> last voice connection error from the bridge
        self.last_error: str | None = None

    @property
    def connected(self) -> bool:
        return self._ready.is_set()

    async def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._closing = False
            self._runner = asyncio.create_task(self._run(), name="nyanjou-bridge-client")

    async def wait_ready(self, timeout: float = 10.0) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout)

    async def close(self) -> None:
        self._closing = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._runner:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._runner
        self._ready.clear()

    async def _run(self) -> None:
        backoff = 0.5
        while not self._closing:
            try:
                async with self._connect(self.url, max_size=8 * 1024 * 1024, open_timeout=5) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({"op": "hello", "token": self.token}))
                    async for raw in ws:
                        if isinstance(raw, (bytes, bytearray)):
                            self._on_binary(bytes(raw))
                        else:
                            self._on_text(raw)
                        backoff = 0.5
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.debug("bridge connection failed: %s", self.last_error)
            was_ready = self._ready.is_set()
            self._ready.clear()
            self._ws = None
            if was_ready:
                log.warning("voice bridge connection lost")
                for h in list(self.handlers.values()):
                    with contextlib.suppress(Exception):
                        h.on_bridge_event({"op": "bridge_lost"})
            if self._closing:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10.0)

    def _on_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        op = msg.get("op")
        if op == "ready":
            log.info("voice bridge ready")
            self._ready.set()
            return
        gid = int(msg["guildId"]) if msg.get("guildId") else None
        if op == "error" and gid is not None:
            self.last_guild_error[gid] = str(msg.get("message") or "unknown error")[:300]
        proto = self.protocols.get(gid) if gid is not None else None
        if op == "gateway":
            if proto is not None:
                asyncio.create_task(proto.handle_gateway_payload(msg.get("payload") or {}))
            return
        if op == "connection_state" and proto is not None:
            proto.mark_state(msg.get("state"))
        elif op == "left" and proto is not None:
            proto.mark_state(None)
        if gid is not None and gid in self.handlers:
            with contextlib.suppress(Exception):
                self.handlers[gid].on_bridge_event(msg)

    def _on_binary(self, buf: bytes) -> None:
        decoded = decode_capture(buf)
        if decoded is None:
            return
        gid, uid, pcm = decoded
        h = self.handlers.get(gid)
        if h is not None:
            h.on_capture(uid, pcm)

    async def send(self, msg: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or not self._ready.is_set():
            raise ConnectionError("voice bridge not connected")
        async with self._send_lock:
            await ws.send(json.dumps(msg))

    async def send_playback(self, guild_id: int, pcm48_stereo: bytes) -> None:
        ws = self._ws
        if ws is None or not self._ready.is_set() or not pcm48_stereo:
            return
        with contextlib.suppress(ConnectionClosed):
            async with self._send_lock:
                await ws.send(encode_playback(guild_id, pcm48_stereo))


class BridgeProcess:
    """Starts and supervises ``node bridge/index.mjs`` (when autostart is on)."""

    def __init__(self, node: str, bridge_dir, host: str, port: int, token: str, debug: bool = False) -> None:
        self.node = node
        self.bridge_dir = bridge_dir
        self.host = host
        self.port = port
        self.token = token
        self.debug = debug
        self._proc: asyncio.subprocess.Process | None = None
        self._supervisor: asyncio.Task | None = None
        self._stopping = False
        self.last_error: str | None = None

    async def check_node(self) -> None:
        path = shutil.which(self.node) or (self.node if os.path.exists(self.node) else None)
        if not path:
            raise RuntimeError(f"Node.js not found ({self.node!r}); install Node >= 22.12 or set NYANJOU_BRIDGE_NODE")
        proc = await asyncio.create_subprocess_exec(path, "--version", stdout=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        m = re.match(r"v(\d+)\.(\d+)", out.decode().strip())
        if not m or (int(m.group(1)), int(m.group(2))) < MIN_NODE:
            raise RuntimeError(f"Node {out.decode().strip()} is too old; @discordjs/voice needs >= 22.12")
        if not (self.bridge_dir / "node_modules" / "@discordjs" / "voice").exists():
            raise RuntimeError(f"bridge dependencies missing: run `npm ci` in {self.bridge_dir}")

    async def start(self) -> None:
        await self.check_node()
        self._stopping = False
        self._supervisor = asyncio.create_task(self._supervise(), name="nyanjou-bridge-process")

    async def _supervise(self) -> None:
        backoff = 1.0
        env = {
            **os.environ,
            "NYANJOU_BRIDGE_HOST": self.host,
            "NYANJOU_BRIDGE_PORT": str(self.port),
            "NYANJOU_BRIDGE_TOKEN": self.token,
            "NYANJOU_BRIDGE_DEBUG": "1" if self.debug else "0",
            # The bridge exits when our end of its stdin closes, even if this process is killed hard.
            "NYANJOU_BRIDGE_EXIT_ON_STDIN_CLOSE": "1",
        }
        # The bridge never needs these; keep secrets out of the child environment.
        for key in ("BOT_TOKEN", "DISCORD_BOT_TOKEN", "OPENAI_API_KEY", "OPENAI_TOKEN", "T3_MCP_ACCESS_TOKEN"):
            env.pop(key, None)
        try:
            while not self._stopping:
                self._proc = await asyncio.create_subprocess_exec(
                    shutil.which(self.node) or self.node, "index.mjs",
                    cwd=str(self.bridge_dir), env=env, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                assert self._proc.stdout is not None
                async for line in self._proc.stdout:
                    text = line.decode(errors="replace").rstrip()
                    (log.debug if "[bridge:debug]" in text else log.info)(text)
                code = await self._proc.wait()
                if self._stopping:
                    break
                self.last_error = f"bridge exited with code {code}"
                log.error("%s; restarting in %.0fs", self.last_error, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
        finally:
            # Also runs when the task is cancelled (bot shutdown cancels all tasks).
            self._terminate_now()

    def _terminate_now(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()

    async def stop(self) -> None:
        self._stopping = True
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 3)
            except asyncio.TimeoutError:
                self._proc.kill()
        if self._supervisor:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._supervisor


def _raw(data: Any) -> dict:
    if isinstance(data, dict):
        return data
    raw = getattr(data, "_raw_data", None)
    if isinstance(raw, dict):
        return raw
    raise TypeError(f"unexpected voice payload type {type(data)!r}")


class BridgeVoiceProtocol(VoiceProtocol):
    """Pycord voice protocol backed by the Node voice bridge."""

    def __init__(self, client: discord.Client, channel) -> None:
        super().__init__(client, channel)
        runtime = getattr(client, "nyanjou", None)
        if runtime is None or runtime.bridge is None:
            raise RuntimeError("Nyanjou runtime/bridge not initialized")
        self.bridge: BridgeClient = runtime.bridge
        self.guild: discord.Guild = channel.guild
        self._connected = asyncio.Event()
        self._state: str | None = None
        self.on_lost = None  # set by VoiceSession: callable(reason)

    # -- state from the bridge
    def mark_state(self, state: str | None) -> None:
        self._state = state
        if state == "ready":
            self._connected.set()
        else:
            self._connected.clear()

    def is_connected(self) -> bool:
        return self._connected.is_set()

    async def handle_gateway_payload(self, payload: dict) -> None:
        if payload.get("op") != 4:
            return
        d = payload.get("d") or {}
        cid = d.get("channel_id")
        channel = self.guild.get_channel(int(cid)) if cid else None
        await self.guild.change_voice_state(
            channel=channel, self_mute=bool(d.get("self_mute")), self_deaf=bool(d.get("self_deaf"))
        )

    async def connect(self, *, timeout: float, reconnect: bool) -> None:
        if not self.bridge.connected:
            await self.bridge.wait_ready(timeout=min(timeout, 10))
        self.bridge.protocols[self.guild.id] = self
        await self.bridge.send({
            "op": "join",
            "guildId": str(self.guild.id),
            "channelId": str(self.channel.id),
            "selfUserId": str(self.client.user.id),
        })
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def on_voice_state_update(self, data) -> None:
        raw = _raw(data)
        with contextlib.suppress(ConnectionError):
            await self.bridge.send({"op": "voice_state", "guildId": str(self.guild.id), "d": raw})
        if raw.get("channel_id") is None:
            # Kicked or disconnected from voice by someone else.
            if self.on_lost:
                self.on_lost("voice_state_disconnected")
        else:
            new_channel = self.guild.get_channel(int(raw["channel_id"]))
            if new_channel is not None:
                self.channel = new_channel

    async def on_voice_server_update(self, data) -> None:
        with contextlib.suppress(ConnectionError):
            await self.bridge.send({"op": "voice_server", "guildId": str(self.guild.id), "d": _raw(data)})

    async def disconnect(self, *, force: bool = False) -> None:
        with contextlib.suppress(Exception):
            await self.bridge.send({"op": "leave", "guildId": str(self.guild.id)})
        with contextlib.suppress(Exception):
            await self.guild.change_voice_state(channel=None)
        if self.bridge.protocols.get(self.guild.id) is self:
            del self.bridge.protocols[self.guild.id]
        self._connected.clear()
        with contextlib.suppress(Exception):
            self.cleanup()
