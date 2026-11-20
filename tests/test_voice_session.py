"""VoiceSession with a fake bridge and a fake Live client (no Discord / OpenAI needed)."""
import asyncio
import json
from collections import deque

import numpy as np
import pytest

from services.config import NyanjouConfig
from services.live_client import FunctionCall
from services.task_manager import TaskManager
from services.task_store import TaskRecord, TaskStore
from services.tools import ToolDispatcher
from services.voice_session import VoiceSession
from tests.fakes import FakeT3Client, eventually

ALICE, BOB, GUEST, BOT = 2, 3, 4, 50


class FakeBridge:
    def __init__(self):
        self.handlers = {}
        self.sent = []
        self.playback = []
        self.connected = True

    async def send(self, msg):
        self.sent.append(msg)

    async def send_playback(self, guild_id, pcm):
        self.playback.append(pcm)


class FakeLive:
    def __init__(self, settings, callbacks):
        self.settings = settings
        self.cb = callbacks
        self.connected = False
        self.audio = deque()
        self.commentary = []
        self.thinking = []
        self.history = deque(maxlen=40)
        self.session_id = None
        self.reconnects = 0
        self.last_error = None
        self.closed = False

    async def start(self):
        self.connected = True
        self.session_id = "s1"
        self.cb.on_session_started("s1")

    async def close(self):
        self.closed = True
        self.connected = False

    async def send_audio(self, pcm):
        self.audio.append(pcm)

    async def append_commentary(self, text):
        self.commentary.append(text)

    async def append_thinking(self, text):
        self.thinking.append(text)


def voice_frame(amp=8000):
    t = np.arange(960) / 48000
    mono = (amp * np.sin(2 * np.pi * 300 * t)).astype("<i2")
    return np.repeat(mono, 2).tobytes()


SILENT = bytes(3840)
MEMBERS = {ALICE: ("Alice", False), BOB: ("Bob", False), GUEST: ("Gast", False), BOT: ("OtherBot", True)}


@pytest.fixture
async def setup(tmp_path):
    store = TaskStore(tmp_path / "t.sqlite")
    t3 = FakeT3Client()
    tm = TaskManager(store, t3)

    async def authorized(uid):
        return uid in {ALICE, BOB}

    async def owner(uid):
        return False

    dispatcher = ToolDispatcher(tm, authorized, owner, default_project_id="p1")
    yield store, dispatcher
    store.close()


def make_session(dispatcher, **cfg_kw):
    cfg = NyanjouConfig(openai_api_key="sk", bridge_token="x" * 20, **cfg_kw)
    bridge = FakeBridge()
    s = VoiceSession(
        cfg=cfg, guild_id=10, voice_channel_id=20, text_channel_id=30, started_by=ALICE, bridge=bridge,
        dispatcher=dispatcher, is_authorized_cached=lambda uid: uid in {ALICE, BOB},
        member_info=lambda uid: MEMBERS.get(uid), live_factory=FakeLive,
    )
    return s, bridge


async def test_start_registers_and_stop_cleans_up(setup):
    _, d = setup
    s, bridge = make_session(d)
    await s.start()
    assert bridge.handlers[10] is s and s.live.connected
    await s.stop("leave")
    assert 10 not in bridge.handlers and s.live.closed and s.closed
    assert all(t.done() for t in s._tasks)


async def test_floor_mode_forwards_only_floor_holder_and_ignores_bots(setup):
    _, d = setup
    s, _ = make_session(d)
    s.live = FakeLive(None, s._callbacks())
    s.on_capture(ALICE, voice_frame())
    s.on_capture(BOB, voice_frame(16000))
    s.on_capture(BOT, voice_frame(20000))
    s.on_capture(999, voice_frame())  # not a guild member
    s.tick()
    assert s.gate.holder == BOB  # loudest takes the floor
    assert BOT not in s._buffers and 999 not in s._buffers
    for _ in range(5):
        s.on_capture(ALICE, voice_frame(20000))
        s.on_capture(BOB, voice_frame(8000))
        s.tick()
    assert s.gate.holder == BOB  # keeps the floor while talking
    att = s.timeline.attribute(s.clock_ms, 8000, 500)
    assert att.user_id == BOB


async def test_guest_audio_dropped_when_guest_conversation_disabled(setup):
    _, d = setup
    s, _ = make_session(d, allow_guest_conversation=False)
    s.on_capture(GUEST, voice_frame())
    s.on_capture(ALICE, voice_frame())
    assert GUEST not in s._buffers and ALICE in s._buffers


async def test_delegation_attributed_to_single_authorized_speaker(setup):
    store, d = setup
    s, _ = make_session(d)
    s.live = FakeLive(None, s._callbacks())
    for _ in range(50):  # Alice speaks 1 s
        s.on_capture(ALICE, voice_frame())
        s.tick()
    s._on_delegation("del_1", s.clock_ms, "responses")
    out = json.loads(await s._on_function_call(FunctionCall(
        "c1", "t3_start_task",
        {"title": "Seite", "task": "Baue eine Minecraft-Webseite mit Catppuccin Mocha", "project": None,
         "new_project": False, "risk_level": "normal", "risk_reason": None, "confirmation_id": None}, "del_1")))
    assert out["ok"]
    t = store.list()[0]
    assert t.user_id == ALICE and t.guild_id == 10 and t.channel_id == 30


async def test_guest_speaking_blocks_execution(setup):
    store, d = setup
    s, _ = make_session(d, speaker_mode="floor", floor_release_ms=100)
    s.live = FakeLive(None, s._callbacks())
    for _ in range(30):
        s.on_capture(ALICE, voice_frame())
        s.tick()
    for _ in range(10):
        s.tick()  # pause, floor released
    for _ in range(30):  # guest says "start a task" right after
        s.on_capture(GUEST, voice_frame())
        s.tick()
    s._on_delegation("del_2", s.clock_ms, "responses")
    out = json.loads(await s._on_function_call(FunctionCall("c2", "t3_cancel_task", {"task_id": None}, "del_2")))
    assert out["error"] == "speaker_not_identified"
    # Guest alone -> identified but not authorized
    s2, _ = make_session(d)
    s2.live = FakeLive(None, s2._callbacks())
    for _ in range(30):
        s2.on_capture(GUEST, voice_frame())
        s2.tick()
    s2._on_delegation("d3", s2.clock_ms, "responses")
    out = json.loads(await s2._on_function_call(FunctionCall("c3", "t3_list_tasks", {"only_active": True}, "d3")))
    assert out["error"] == "not_authorized"
    assert store.list() == []


async def test_unknown_or_missing_delegation_never_falls_back_to_recent_speaker(setup):
    store, d = setup
    s, _ = make_session(d)
    s.live = FakeLive(None, s._callbacks())
    for _ in range(50):  # authorized Alice spoke just now
        s.on_capture(ALICE, voice_frame())
        s.tick()
    for did in (None, "never-recorded"):
        out = json.loads(await s._on_function_call(FunctionCall("c", "t3_list_tasks", {"only_active": True}, did)))
        assert out["error"] == "speaker_not_identified"
    assert s.attribution_for("never-recorded").reason == "unknown_delegation"


async def test_live_audio_upsampled_to_playback_and_barge_in_flushes(setup):
    _, d = setup
    s, bridge = make_session(d, barge_in_ms=60)
    await s.start()
    s._on_live_audio(b"\x00\x10" * 480)  # 20 ms at 24 kHz
    await eventually(lambda: bridge.playback)
    assert len(bridge.playback[0]) == 480 * 2 * 2 * 2  # 2x rate, stereo, int16
    assert s.playing
    # User talks over the bot for >= 60 ms -> flush
    for _ in range(4):
        s.on_capture(ALICE, voice_frame())
        s.tick()
    await eventually(lambda: any(m.get("op") == "flush" for m in bridge.sent))
    assert s.stats.barge_ins == 1
    # While the user keeps talking, model audio is suppressed
    n = len(bridge.playback)
    s._on_live_audio(b"\x00\x10" * 480)
    await asyncio.sleep(0.05)
    assert len(bridge.playback) == n
    await s.stop()


async def test_clock_streams_audio_to_live(setup):
    _, d = setup
    s, _ = make_session(d)
    await s.start()
    await asyncio.sleep(0.15)
    assert len(s.live.audio) >= 4
    assert all(len(chunk) == 960 for chunk in s.live.audio)  # 20 ms, 24 kHz mono int16
    await s.stop()


async def test_new_live_session_resets_clock_and_attribution(setup):
    _, d = setup
    s, _ = make_session(d)
    s.live = FakeLive(None, s._callbacks())
    for _ in range(30):
        s.on_capture(ALICE, voice_frame())
        s.tick()
    s._on_delegation("old", s.clock_ms, "responses")
    s._on_live_started("s2")
    assert s.clock_ms == 0 and "old" not in s._delegations


async def test_task_events_reach_live_only_for_this_guild(setup):
    _, d = setup
    s, _ = make_session(d)
    await s.start()
    done = TaskRecord(user_id=ALICE, title="Seite", prompt="p", source="voice", guild_id=10, short_id=3,
                      status="completed", result="Next.js-App unter ~/apps/mc liegt bereit")
    other = TaskRecord(user_id=ALICE, title="X", prompt="p", source="voice", guild_id=11, short_id=4,
                       status="completed", result="r")
    await s.on_task_event(done, "completed")
    await s.on_task_event(other, "completed")
    assert len(s.live.commentary) == 1 and "#3" in s.live.commentary[0] and "Alice" in s.live.commentary[0]
    await s.stop()


async def test_speaker_announcement(setup):
    _, d = setup
    s, _ = make_session(d)
    s.live = FakeLive(None, s._callbacks())
    s.on_capture(ALICE, voice_frame())
    s.tick()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert s.live.thinking == ["Jetzt spricht: Alice"]


async def test_restart_live_keeps_history(setup):
    _, d = setup
    s, _ = make_session(d)
    await s.start()
    s.live.history.append({"role": "user", "content": [{"type": "input_text", "text": "hi"}]})
    old = s.live
    await s.restart_live(voice="cedar")
    assert old.closed and s.live is not old
    assert s.live.settings.voice == "cedar" and list(s.live.history)[0]["content"][0]["text"] == "hi"
    await s.stop()


async def test_bridge_lost_stops_session(setup):
    _, d = setup
    s, bridge = make_session(d)
    await s.start()
    s.on_bridge_event({"op": "bridge_lost"})
    await eventually(lambda: s.closed)


async def test_stop_waits_for_restart_and_closes_new_client(setup):
    _, d = setup
    s, _ = make_session(d)
    created = []
    slow = asyncio.Event()

    class SlowLive(FakeLive):
        async def start(self):
            created.append(self)
            if len(created) > 1:
                await slow.wait()
            await super().start()

    s._live_factory = SlowLive
    await s.start()
    restart = asyncio.create_task(s.restart_live(voice="cedar"))
    await asyncio.sleep(0.02)
    stop = asyncio.create_task(s.stop("leave"))
    await asyncio.sleep(0.02)
    assert not stop.done()  # teardown waits for the in-flight replacement
    slow.set()
    await asyncio.gather(restart, stop)
    assert all(c.closed for c in created)  # no Live client left connected
    # A restart after stop is a no-op.
    await s.restart_live(voice="ash")
    assert len(created) == 2


async def test_guest_earlier_in_conversation_forces_confirmation(setup):
    store, d = setup
    s, _ = make_session(d, floor_release_ms=100)
    s.live = FakeLive(None, s._callbacks())
    for _ in range(30):
        s.on_capture(GUEST, voice_frame())
        s.tick()
    for _ in range(3000):  # a minute later, and a Live reconnect in between
        s.tick()
    s._on_live_started("s2")
    for _ in range(50):
        s.on_capture(ALICE, voice_frame())
        s.tick()
    s._on_delegation("dd", s.clock_ms, "responses")
    assert s.attribution_for("dd").others_recent == {GUEST}
    out = json.loads(await s._on_function_call(FunctionCall("c", "t3_start_task", {
        "title": "x", "task": "Baue eine kleine Webseite", "project": None, "new_project": False,
        "risk_level": "normal", "risk_reason": None, "confirmation_id": None}, "dd")))
    assert out["status"] == "confirmation_required"
    assert store.list() == []


async def test_leave_during_startup_starts_nothing(setup):
    _, d = setup
    s, bridge = make_session(d)
    gate = asyncio.Event()

    async def slow_connect():
        await gate.wait()

    s._connect_voice = slow_connect
    disconnected = []

    async def disc():
        disconnected.append(True)

    s._disconnect_voice = disc
    start = asyncio.create_task(s.start())
    await asyncio.sleep(0.02)
    stop = asyncio.create_task(s.stop("leave"))
    await asyncio.sleep(0.02)
    assert not stop.done()
    gate.set()
    await asyncio.gather(start, stop)
    assert s._tasks == [] and s.live.closed and disconnected and 10 not in bridge.handlers


async def test_cancelled_stop_caller_does_not_abandon_teardown(setup):
    _, d = setup
    s, bridge = make_session(d)
    gate = asyncio.Event()

    async def slow_connect():
        await gate.wait()

    s._connect_voice = slow_connect
    start = asyncio.create_task(s.start())
    await asyncio.sleep(0.02)
    stopper = asyncio.create_task(s.stop("leave"))
    await asyncio.sleep(0.02)
    stopper.cancel()  # e.g. the interaction handler is cancelled
    gate.set()
    await start
    await asyncio.sleep(0.05)
    assert s.live.closed and 10 not in bridge.handlers and all(t.done() for t in s._tasks)
    await s.stop("again")  # later calls await the same finished teardown


async def test_playback_idle_marks_question_end(setup):
    _, d = setup
    s, _ = make_session(d)
    s.clock_ms = 5000
    s.on_bridge_event({"op": "playback_idle", "guildId": "10"})
    assert s.playback_idle_after(4000) == 5000 and s.playback_idle_after(5000) is None
