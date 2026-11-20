"""OpenAI GPT-Live WebSocket client.

Protocol (developers.openai.com/api/docs/guides/voice-websockets and
.../live-delegation, openai SDK 3.57 ``openai.types.live``):

* connect ``wss://api.openai.com/v1/live/sessions`` with ``Authorization: Bearer``
* first message ``session.start``, then wait for ``session.started``
* stream ``session.input_audio.append`` (base64 PCM16 mono 24 kHz)
* receive ``session.output_audio.delta``, transcripts, ``session.delegation.created``,
  ``response.event`` envelopes (Responses backend), ``error``, ``session.closed``
* tool results: ``response.item.create`` (function_call_output) + ``response.create``
* async context: ``session.commentary.append`` / ``session.thinking.append``

The client reconnects with backoff. A new session receives a short text
history (``session.input``) so the conversation continues after a reconnect
or when a session hits its duration limit.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import random
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("bot.py.nyanjou.live")

MAX_HISTORY_ITEMS = 40


@dataclass
class FunctionCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    delegation_id: str | None
    raw_arguments: str = ""


@dataclass
class LiveCallbacks:
    on_audio: Callable[[bytes], None] = lambda pcm: None
    on_input_transcript: Callable[[str, int, int], None] = lambda text, s, e: None
    on_output_transcript: Callable[[str], None] = lambda text: None
    on_delegation: Callable[[str, int, str], None] = lambda delegation_id, offset_ms, target: None
    on_function_call: Callable[[FunctionCall], Awaitable[str]] | None = None
    on_session_started: Callable[[str], None] = lambda session_id: None
    on_session_lost: Callable[[str], None] = lambda reason: None
    on_error: Callable[[dict], None] = lambda err: None


@dataclass
class LiveSettings:
    api_key: str
    url: str
    model: str
    voice: str
    instructions: str
    backend_model: str
    backend_instructions: str
    tools: list[dict[str, Any]]
    reasoning_effort: str | None = "low"
    web_search: bool = True
    safety_identifier: str | None = None
    connect_timeout: float = 15.0

    def session_config(self, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        tools = list(self.tools)
        if self.web_search:
            tools.append({"type": "web_search"})
        responses: dict[str, Any] = {
            "model": self.backend_model,
            "instructions": self.backend_instructions,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }
        if self.reasoning_effort:
            responses["reasoning"] = {"effort": self.reasoning_effort}
        cfg: dict[str, Any] = {
            "model": self.model,
            "instructions": self.instructions,
            "audio": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "output": {"voice": self.voice},
            },
            "delegation": {"type": "responses", "responses": responses},
        }
        if self.safety_identifier:
            cfg["safety_identifier"] = self.safety_identifier
        if history:
            cfg["input"] = history[-MAX_HISTORY_ITEMS:]
        return cfg


class LiveError(RuntimeError):
    pass


class LiveClient:
    """One logical conversation, kept alive across WebSocket reconnects."""

    def __init__(self, settings: LiveSettings, callbacks: LiveCallbacks, *, connect=websockets.connect) -> None:
        self.settings = settings
        self.cb = callbacks
        self._connect = connect
        self._ws = None
        self._runner: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._started = asyncio.Event()
        self._closing = False
        self.session_id: str | None = None
        self.connected = False
        self.reconnects = 0
        self.last_error: str | None = None
        # Rolling text history for reconnects (user/assistant transcripts, task results).
        self.history: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY_ITEMS)
        self._user_buf: list[str] = []
        self._assistant_buf: list[str] = []
        self._pending_calls: dict[str, FunctionCall] = {}
        self._call_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._closing = False
        self._runner = asyncio.create_task(self._run(), name="nyanjou-live")
        try:
            await asyncio.wait_for(self._started.wait(), timeout=self.settings.connect_timeout + 5)
        except asyncio.TimeoutError as exc:
            await self.close()
            raise LiveError(self.last_error or "GPT-Live session did not start in time") from exc
        if not self.connected:
            err = self.last_error or "GPT-Live session was rejected"
            await self.close()
            raise LiveError(err)

    async def close(self) -> None:
        self._closing = True
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                await self._send({"type": "session.close"})
                # Give the server a moment to emit session.closed (final usage).
                await asyncio.sleep(0.2)
            with contextlib.suppress(Exception):
                await ws.close()
        for t in list(self._call_tasks):
            t.cancel()
        if self._runner is not None:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._runner
        self._runner = None
        self._ws = None
        self.connected = False

    async def _run(self) -> None:
        backoff = 1.0
        while not self._closing:
            try:
                await self._session_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network, protocol, auth
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("GPT-Live connection error: %s", self.last_error)
            self.connected = False
            self._ws = None
            if self._closing:
                break
            self._flush_transcripts()
            self.cb.on_session_lost(self.last_error or "closed")
            self.reconnects += 1
            await asyncio.sleep(backoff + random.uniform(0, 0.5))
            backoff = min(backoff * 2, 30.0)

    async def _session_once(self) -> None:
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        async with self._connect(
            self.settings.url,
            additional_headers=headers,
            open_timeout=self.settings.connect_timeout,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            self._ws = ws
            self._pending_calls.clear()
            await self._send({
                "type": "session.start",
                "event_id": f"start_{uuid.uuid4().hex[:12]}",
                "session": self.settings.session_config(list(self.history)),
            })
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    log.debug("non-JSON Live frame ignored")
                    continue
                if await self._handle(event):
                    return

    # ------------------------------------------------------------------ sending
    async def _send(self, event: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            raise LiveError("not connected")
        async with self._send_lock:
            await ws.send(json.dumps(event))

    async def send_audio(self, pcm24_mono: bytes) -> None:
        """Append 24 kHz mono PCM16. Silently dropped while (re)connecting."""
        if not self.connected or not pcm24_mono:
            return
        if len(pcm24_mono) % 2:
            pcm24_mono = pcm24_mono[:-1]
        try:
            await self._send({
                "type": "session.input_audio.append",
                "audio": base64.b64encode(pcm24_mono).decode("ascii"),
            })
        except (ConnectionClosed, LiveError):
            pass

    async def append_commentary(self, text: str) -> None:
        """Speakable context (e.g. a finished T3 task). Live paraphrases it to the user."""
        self.history.append(_msg("developer", f"[Update] {text}"))
        if self.connected:
            await self._send({"type": "session.commentary.append", "delegation_id": None, "content": text[:1800]})

    async def append_thinking(self, text: str) -> None:
        """Silent context: available to the model, not spoken on arrival."""
        self.history.append(_msg("developer", f"[Kontext] {text}"))
        if self.connected:
            await self._send({"type": "session.thinking.append", "delegation_id": None, "content": text[:1800]})

    async def append_instructions(self, text: str) -> None:
        if self.connected:
            await self._send({"type": "session.instructions.append", "delegation_id": None, "content": text[:1800]})

    async def mute_input(self, muted: bool) -> None:
        if self.connected:
            await self._send({"type": "session.input_audio.mute" if muted else "session.input_audio.unmute"})

    # ------------------------------------------------------------------ receiving
    async def _handle(self, ev: dict[str, Any]) -> bool:
        """Dispatch one server event. Returns True when the session ended."""
        etype = ev.get("type")
        if etype == "session.output_audio.delta":
            try:
                self.cb.on_audio(base64.b64decode(ev.get("delta", "")))
            except Exception:
                log.exception("audio callback failed")
        elif etype == "session.input_transcript.delta":
            delta = ev.get("delta", "")
            if self._assistant_buf:
                self._flush_assistant()
            self._user_buf.append(delta)
            self.cb.on_input_transcript(delta, int(ev.get("start_ms", 0)), int(ev.get("end_ms", 0)))
        elif etype == "session.output_transcript.delta":
            delta = ev.get("delta", "")
            if self._user_buf:
                self._flush_user()
            self._assistant_buf.append(delta)
            self.cb.on_output_transcript(delta)
        elif etype == "session.started":
            session = ev.get("session") or {}
            self.session_id = session.get("id")
            self.connected = True
            self.last_error = None
            self._started.set()
            log.info("GPT-Live session started: %s (model=%s)", self.session_id, session.get("model"))
            self.cb.on_session_started(self.session_id or "")
        elif etype == "session.delegation.created":
            d = ev.get("delegation") or {}
            self.cb.on_delegation(d.get("id", ""), int(ev.get("offset_ms", 0)), d.get("target", ""))
        elif etype == "response.event":
            await self._handle_backend(ev.get("event") or {}, ev.get("delegation_id"))
        elif etype == "error":
            err = ev.get("error") or {}
            self.last_error = f"{err.get('code')}: {err.get('message')}"
            log.warning("GPT-Live error: %s", self.last_error)
            self.cb.on_error(err)
            if not self.connected and err.get("type") in {"invalid_request_error", "authentication_error"}:
                # Startup was rejected; retrying with the same config would loop.
                self._closing = True
                self._started.set()
                return True
        elif etype == "session.closed":
            reason = ev.get("reason", "")
            log.info("GPT-Live session closed: %s", reason)
            self.connected = False
            self.last_error = f"session closed: {reason}"
            if reason == "content":
                log.warning("GPT-Live closed the session for a safety reason")
            return True
        return False

    async def _handle_backend(self, inner: dict[str, Any], delegation_id: str | None) -> None:
        itype = inner.get("type")
        if itype != "response.output_item.done":
            return
        item = inner.get("item") or {}
        if item.get("type") != "function_call":
            return
        raw_args = item.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            args = {}
        call = FunctionCall(
            call_id=item.get("call_id", ""),
            name=item.get("name", ""),
            arguments=args if isinstance(args, dict) else {},
            delegation_id=delegation_id,
            raw_arguments=raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
        )
        if not call.call_id:
            return
        self._pending_calls[call.call_id] = call
        task = asyncio.create_task(self._run_call(call), name=f"nyanjou-tool-{call.name}")
        self._call_tasks.add(task)
        task.add_done_callback(self._call_tasks.discard)

    async def _run_call(self, call: FunctionCall) -> None:
        try:
            if self.cb.on_function_call is None:
                output = json.dumps({"ok": False, "error": "no tool handler"})
            else:
                output = await self.cb.on_function_call(call)
        except Exception as exc:
            log.exception("tool %s failed", call.name)
            output = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]})
        self._pending_calls.pop(call.call_id, None)
        await self.submit_tool_output(call.call_id, output)

    async def submit_tool_output(self, call_id: str, output: str) -> None:
        try:
            await self._send({
                "type": "response.item.create",
                "event_id": f"out_{call_id[:24]}",
                "item": {"type": "function_call_output", "call_id": call_id, "output": output},
            })
            if not self._pending_calls:
                # All outputs submitted; the backend only resumes after response.create.
                await self._send({"type": "response.create", "event_id": f"cont_{uuid.uuid4().hex[:10]}"})
        except (ConnectionClosed, LiveError):
            log.warning("could not deliver tool output for %s (connection lost)", call_id)

    # ------------------------------------------------------------------ history
    def _flush_user(self) -> None:
        text = "".join(self._user_buf).strip()
        self._user_buf.clear()
        if text:
            self.history.append(_msg("user", text))

    def _flush_assistant(self) -> None:
        text = "".join(self._assistant_buf).strip()
        self._assistant_buf.clear()
        if text:
            self.history.append(_msg("assistant", text))

    def _flush_transcripts(self) -> None:
        self._flush_user()
        self._flush_assistant()


def _msg(role: str, text: str) -> dict[str, Any]:
    # InitialItem: developer/user/assistant message with one text part.
    part_type = "output_text" if role == "assistant" else "input_text"
    return {"type": "message", "role": role, "content": [{"type": part_type, "text": text[:2000]}]}
