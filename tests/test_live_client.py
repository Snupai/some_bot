"""GPT-Live client against an in-process fake Live WebSocket server."""
import asyncio
import base64
import json

import pytest
import websockets

from services.live_client import LiveCallbacks, LiveClient, LiveError, LiveSettings
from services.tools import TOOL_DEFINITIONS


class FakeLive:
    def __init__(self):
        self.connections = []
        self.received: list[list[dict]] = []
        self.headers = []
        self.reject_auth = False
        self.server = None

    async def handler(self, ws):
        self.connections.append(ws)
        self.headers.append(dict(ws.request.headers))
        msgs: list[dict] = []
        self.received.append(msgs)
        try:
            async for raw in ws:
                ev = json.loads(raw)
                msgs.append(ev)
                if ev["type"] == "session.start":
                    if self.reject_auth:
                        await ws.send(json.dumps({"type": "error", "event_id": "e1", "error": {
                            "type": "invalid_request_error", "code": "invalid_api_key", "message": "bad key"}}))
                        continue
                    await ws.send(json.dumps({"type": "session.started", "event_id": "e0",
                                              "session": {"id": f"sess_{len(self.connections)}",
                                                          "model": ev["session"]["model"], "status": "active",
                                                          "expires_at": 0}}))
                elif ev["type"] == "session.close":
                    await ws.send(json.dumps({"type": "session.closed", "event_id": "c", "reason": "close_requested",
                                              "session": {}, "usage": {}}))
        except websockets.ConnectionClosed:
            pass

    async def start(self):
        self.server = await websockets.serve(self.handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}/v1/live/sessions"

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def wait_for(self, pred, timeout=3.0, conn=-1):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.received and any(pred(m) for m in self.received[conn]):
                return [m for m in self.received[conn] if pred(m)]
            await asyncio.sleep(0.01)
        raise AssertionError("expected message not received")


def settings(url):
    return LiveSettings(api_key="sk-test", url=url, model="gpt-live-1.1", voice="marin", instructions="hi",
                        backend_model="gpt-5.6-luna", backend_instructions="be", tools=TOOL_DEFINITIONS,
                        connect_timeout=3)


@pytest.fixture
async def fake():
    f = FakeLive()
    f.url = await f.start()
    yield f
    await f.stop()


async def test_session_start_payload_and_audio(fake):
    audio = []
    client = LiveClient(settings(fake.url), LiveCallbacks(on_audio=audio.append))
    await client.start()
    assert client.connected and client.session_id == "sess_1"
    assert fake.headers[0]["authorization"] == "Bearer sk-test"
    start = fake.received[0][0]
    assert start["type"] == "session.start"
    s = start["session"]
    assert s["model"] == "gpt-live-1.1"
    assert s["audio"] == {"format": {"type": "audio/pcm", "rate": 24000}, "output": {"voice": "marin"}}
    assert s["delegation"]["type"] == "responses"
    tool_names = {t.get("name", t["type"]) for t in s["delegation"]["responses"]["tools"]}
    assert {"t3_start_task", "t3_update_task", "web_search"} <= tool_names

    await client.send_audio(b"\x01\x02\x03")  # odd length gets trimmed
    sent = await fake.wait_for(lambda m: m["type"] == "session.input_audio.append")
    assert base64.b64decode(sent[0]["audio"]) == b"\x01\x02"

    pcm = b"\x10\x00" * 240
    await fake.connections[0].send(json.dumps({"type": "session.output_audio.delta", "delta": base64.b64encode(pcm).decode()}))
    for _ in range(100):
        if audio:
            break
        await asyncio.sleep(0.01)
    assert audio == [pcm]
    await client.close()
    assert any(m["type"] == "session.close" for m in fake.received[0])


async def test_function_call_roundtrip(fake):
    calls = []

    async def on_call(call):
        calls.append(call)
        return json.dumps({"ok": True, "task_id": 7})

    delegations = []
    client = LiveClient(settings(fake.url), LiveCallbacks(
        on_function_call=on_call, on_delegation=lambda d, o, t: delegations.append((d, o, t))))
    await client.start()
    ws = fake.connections[0]
    await ws.send(json.dumps({"type": "session.delegation.created", "event_id": "x", "offset_ms": 4200,
                              "delegation": {"id": "del_1", "type": "delegation", "target": "responses",
                                             "response_id": "resp_1"}}))
    await ws.send(json.dumps({"type": "response.event", "event_id": "y", "delegation_id": "del_1", "event": {
        "type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": "call_1", "name": "t3_start_task",
            "arguments": json.dumps({"title": "Web", "task": "Baue eine Seite"})}}}))
    out = await fake.wait_for(lambda m: m["type"] == "response.item.create")
    assert out[0]["item"] == {"type": "function_call_output", "call_id": "call_1",
                              "output": json.dumps({"ok": True, "task_id": 7})}
    await fake.wait_for(lambda m: m["type"] == "response.create")
    assert delegations == [("del_1", 4200, "responses")]
    assert calls[0].delegation_id == "del_1" and calls[0].arguments["title"] == "Web"
    await client.close()


async def test_tool_exception_returns_error_output(fake):
    async def boom(call):
        raise ValueError("kaputt")

    client = LiveClient(settings(fake.url), LiveCallbacks(on_function_call=boom))
    await client.start()
    await fake.connections[0].send(json.dumps({"type": "response.event", "event_id": "y", "delegation_id": "d", "event": {
        "type": "response.output_item.done", "item": {"type": "function_call", "call_id": "c9", "name": "x",
                                                     "arguments": "{}"}}}))
    out = await fake.wait_for(lambda m: m["type"] == "response.item.create")
    assert json.loads(out[0]["item"]["output"])["ok"] is False
    await client.close()


async def test_reconnect_carries_history(fake):
    started = []
    client = LiveClient(settings(fake.url), LiveCallbacks(on_session_started=started.append))
    await client.start()
    ws = fake.connections[0]
    await ws.send(json.dumps({"type": "session.input_transcript.delta", "event_id": "t", "delta": "Bau mir eine Webseite",
                              "start_ms": 0, "end_ms": 900}))
    await ws.send(json.dumps({"type": "session.output_transcript.delta", "event_id": "t2", "delta": "Was für eine?"}))
    await asyncio.sleep(0.05)
    await ws.close()  # server drops the connection
    for _ in range(400):
        if len(started) >= 2:
            break
        await asyncio.sleep(0.01)
    assert started == ["sess_1", "sess_2"]
    history = fake.received[1][0]["session"]["input"]
    assert history[0]["role"] == "user" and history[0]["content"][0]["text"] == "Bau mir eine Webseite"
    assert history[1]["role"] == "assistant" and history[1]["content"][0]["type"] == "output_text"
    assert client.reconnects == 1
    await client.close()


async def test_auth_error_fails_fast(fake):
    fake.reject_auth = True
    client = LiveClient(settings(fake.url), LiveCallbacks())
    with pytest.raises(LiveError, match="invalid_api_key"):
        await client.start()
    assert len(fake.connections) == 1  # no retry loop


async def test_commentary_and_history(fake):
    client = LiveClient(settings(fake.url), LiveCallbacks())
    await client.start()
    await client.append_commentary("Aufgabe #1 ist fertig")
    ev = await fake.wait_for(lambda m: m["type"] == "session.commentary.append")
    assert ev[0]["delegation_id"] is None and "fertig" in ev[0]["content"]
    assert client.history[-1]["role"] == "developer"
    await client.close()
