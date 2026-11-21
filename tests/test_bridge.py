"""Bridge protocol: Python BridgeClient against (a) a fake bridge, (b) the real Node bridge process."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import websockets

from services.discord_audio import (
    BridgeClient,
    BridgeVoiceProtocol,
    decode_capture,
    encode_playback,
)
from tests.fakes import eventually

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "t" * 32


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_frame_codec():
    buf = encode_playback(123456789012345678, b"\x01\x02")
    assert buf[0] == 0x02 and buf[-2:] == b"\x01\x02"
    cap = bytes([0x01]) + (42).to_bytes(8, "big") + (7).to_bytes(8, "big") + b"pcm"
    assert decode_capture(cap) == (42, 7, b"pcm")
    assert decode_capture(b"\x02junk") is None


class Handler:
    def __init__(self):
        self.captures = []
        self.events = []

    def on_capture(self, uid, pcm):
        self.captures.append((uid, pcm))

    def on_bridge_event(self, msg):
        self.events.append(msg)


async def test_client_auth_routing_and_reconnect():
    received = []
    conns = []

    async def server(ws):
        conns.append(ws)
        hello = json.loads(await ws.recv())
        if hello != {"op": "hello", "token": TOKEN}:
            await ws.close(4003)
            return
        await ws.send(json.dumps({"op": "ready", "version": 1}))
        await ws.send(bytes([0x01]) + (10).to_bytes(8, "big") + (2).to_bytes(8, "big") + b"\x00" * 3840)
        await ws.send(json.dumps({"op": "error", "guildId": "10", "message": "connect ECONNREFUSED 1.2.3.4:8443"}))
        await ws.send(json.dumps({"op": "connection_state", "guildId": "10", "state": "ready"}))
        await ws.send(json.dumps({"op": "gateway", "guildId": "10",
                                  "payload": {"op": 4, "d": {"guild_id": "10", "channel_id": "20"}}}))
        async for m in ws:
            received.append(m)

    srv = await websockets.serve(server, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    client = BridgeClient(f"ws://127.0.0.1:{port}", TOKEN)
    h = Handler()
    client.handlers[10] = h

    class Proto:
        def __init__(self):
            self.states = []
            self.payloads = []

        def mark_state(self, s):
            self.states.append(s)

        async def handle_gateway_payload(self, p):
            self.payloads.append(p)

    proto = Proto()
    client.protocols[10] = proto
    await client.start()
    await client.wait_ready(3)
    await eventually(lambda: h.captures and proto.payloads)
    assert h.captures[0][0] == 2 and len(h.captures[0][1]) == 3840
    assert proto.states == ["ready"] and proto.payloads[0]["op"] == 4
    assert "8443" in client.last_guild_error[10]
    await client.send_playback(10, b"\x00\x01")
    await client.send({"op": "flush", "guildId": "10"})
    await eventually(lambda: len(received) == 2)
    assert isinstance(received[0], bytes) and received[0][0] == 0x02

    # server drops -> handlers notified -> client reconnects
    await conns[0].close()
    await eventually(lambda: {"op": "bridge_lost"} in h.events)
    await eventually(lambda: len(conns) == 2 and client.connected, timeout=5)
    await client.close()
    srv.close()
    await srv.wait_closed()


async def test_voice_protocol_forwards_gateway_events():
    sent = []

    class FakeBridge:
        connected = True
        protocols = {}

        async def send(self, msg):
            sent.append(msg)
            if msg["op"] == "join":
                proto.mark_state("ready")

        async def wait_ready(self, timeout):
            pass

    class Guild:
        id = 10
        changes = []

        def get_channel(self, cid):
            return Channel() if cid == 20 else None

        async def change_voice_state(self, *, channel, self_mute=False, self_deaf=False):
            self.changes.append(channel.id if channel else None)

    guild = Guild()

    class Channel:
        id = 20
        guild = None

        def _get_voice_client_key(self):
            return 10, "guild_id"

    Channel.guild = guild

    class Conn:
        removed = []

        def _remove_voice_client(self, key):
            self.removed.append(key)

    class User:
        id = 50

    class Client:
        user = User()
        _connection = Conn()

    client = Client()
    client.nyanjou = type("RT", (), {"bridge": FakeBridge()})()
    proto = BridgeVoiceProtocol(client, Channel())
    await proto.connect(timeout=2, reconnect=True)
    assert sent[0] == {"op": "join", "guildId": "10", "channelId": "20", "selfUserId": "50"}
    assert proto.is_connected()

    await proto.handle_gateway_payload({"op": 4, "d": {"guild_id": "10", "channel_id": "20"}})
    assert guild.changes == [20]
    await proto.on_voice_server_update({"guild_id": "10", "token": "tok", "endpoint": "x.discord.media"})
    assert sent[-1]["op"] == "voice_server" and sent[-1]["d"]["token"] == "tok"

    lost = []
    proto.on_lost = lost.append
    await proto.on_voice_state_update({"guild_id": "10", "channel_id": None, "user_id": "50", "session_id": "s"})
    assert sent[-1]["op"] == "voice_state" and lost == ["voice_state_disconnected"]

    await proto.disconnect(force=True)
    assert sent[-1] == {"op": "leave", "guildId": "10"}
    assert guild.changes[-1] is None and Conn.removed == [10]


def _node_ok():
    node = shutil.which("node")
    if not node or not (ROOT / "bridge" / "node_modules" / "@discordjs" / "voice").exists():
        return None
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    major, minor = (int(x) for x in out.lstrip("v").split(".")[:2])
    return node if (major, minor) >= (22, 12) else None


@pytest.mark.skipif(_node_ok() is None, reason="Node >= 22.12 with bridge/node_modules not available")
async def test_real_node_bridge_auth_and_commands():
    port = free_port()
    env = {**os.environ, "NYANJOU_BRIDGE_TOKEN": TOKEN, "NYANJOU_BRIDGE_PORT": str(port)}
    proc = await asyncio.create_subprocess_exec(_node_ok(), "index.mjs", cwd=ROOT / "bridge", env=env,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), 10)
        assert b"listening" in line

        # wrong token is rejected
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"op": "hello", "token": "wrong" * 8}))
            with pytest.raises(websockets.ConnectionClosed) as ei:
                await asyncio.wait_for(ws.recv(), 3)
            assert ei.value.rcvd.code == 4003

        client = BridgeClient(f"ws://127.0.0.1:{port}", TOKEN)
        h = Handler()
        client.handlers[10] = h
        payloads = []

        class Proto:
            def mark_state(self, s):
                h.events.append({"state": s})

            async def handle_gateway_payload(self, p):
                payloads.append(p)

        client.protocols[10] = Proto()
        await client.start()
        await client.wait_ready(5)
        # join -> @discordjs/voice asks us (the gateway owner) to send a voice state update (op 4)
        await client.send({"op": "join", "guildId": "10", "channelId": "20", "selfUserId": "50"})
        await eventually(lambda: payloads, timeout=5)
        assert payloads[0]["op"] == 4
        assert payloads[0]["d"]["guild_id"] == "10" and payloads[0]["d"]["channel_id"] == "20"
        assert payloads[0]["d"]["self_deaf"] is False
        # playback before the voice connection is ready is buffered, not crashing the bridge
        await client.send_playback(10, b"\x00" * 3840)
        await client.send({"op": "ping", "t": 1})
        await client.send({"op": "leave", "guildId": "10"})
        await eventually(lambda: any(e.get("op") == "left" for e in h.events), timeout=5)
        assert proc.returncode is None
        await client.close()
    finally:
        proc.terminate()
        await proc.wait()
