"""T3McpClient against a real MCP server (FastMCP over streamable HTTP) with T3-shaped tools.

The fake tools use T3's published tool names and argument names. Their
behavior is simplified: threads finish after a configurable number of waits.
"""
import asyncio
import json
import socket

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from services.t3_mcp_client import T3McpClient, T3ToolError, filter_args, find_key


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeT3:
    def __init__(self):
        self.mcp = FastMCP("fake-t3", stateless_http=False, json_response=True)
        self.threads: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.waits_until_done = 2
        m = self.mcp

        @m.tool()
        def t3_thread_launch(title: str, message: str | None = None, projectId: str | None = None,
                             scratch: bool | None = None, clientRequestId: str | None = None,
                             workspaceStrategy: dict | None = None, runtimeMode: str | None = None) -> str:
            self.calls.append(("launch", {"title": title, "message": message, "projectId": projectId,
                                          "scratch": scratch, "clientRequestId": clientRequestId}))
            for tid, t in self.threads.items():  # idempotent clientRequestId
                if clientRequestId and t["crid"] == clientRequestId:
                    return json.dumps({"thread": {"threadId": tid, "latestRunId": t["run"]}})
            tid = f"thread-{len(self.threads) + 1}"
            self.threads[tid] = {"crid": clientRequestId, "run": f"run:{tid}:1", "waits": 0, "status": "running",
                                 "messages": [message]}
            return json.dumps({"thread": {"threadId": tid, "latestRunId": f"run:{tid}:1",
                                          "link": f"[{title}](t3-thread://x/{tid})"}})

        @m.tool()
        def t3_thread_wait(threadId: str, runId: str | None = None, timeoutMs: float | None = None) -> str:
            t = self.threads[threadId]
            t["waits"] += 1
            if t["status"] == "running" and t["waits"] >= self.waits_until_done:
                t["status"] = "completed"
            return json.dumps({"threadId": threadId, "runId": t["run"], "status": t["status"],
                               "timedOut": t["status"] == "running"})

        @m.tool()
        def t3_thread_read(threadId: str, view: str | None = None, limit: int | None = None,
                           maxCharsPerItem: int | None = None, runLimit: int | None = None) -> str:
            t = self.threads[threadId]
            return json.dumps({"thread": {"threadId": threadId, "status": t["status"], "pendingRequestCount": 0,
                                          "link": f"[t](t3-thread://x/{threadId})"},
                               "items": [{"type": "user_message", "text": t["messages"][-1]},
                                         {"type": "assistant_message", "text": f"Fertig: {t['messages'][-1]}"}]})

        @m.tool()
        def t3_thread_send(threadId: str, message: str, mode: str | None = None,
                           clientRequestId: str | None = None) -> str:
            t = self.threads[threadId]
            t["messages"].append(message)
            n = int(t["run"].rsplit(":", 1)[1]) + 1
            t["run"] = f"run:{threadId}:{n}"
            t["status"], t["waits"] = "running", 0
            self.calls.append(("send", {"threadId": threadId, "message": message, "mode": mode}))
            return json.dumps({"threadId": threadId, "runId": t["run"]})

        @m.tool()
        def t3_thread_interrupt(threadId: str, runId: str | None = None, stop: bool | None = None,
                                reason: str | None = None, clientRequestId: str | None = None) -> str:
            self.threads[threadId]["status"] = "interrupted"
            self.calls.append(("interrupt", {"threadId": threadId, "stop": stop}))
            return json.dumps({"ok": True})

        @m.tool()
        def t3_project_list(cursor: int | None = None, limit: int | None = None) -> str:
            return json.dumps({"projects": [{"id": "p1", "title": "some_bot"}, {"id": "p2", "title": "minecraft-site"}],
                               "nextCursor": None})

        @m.tool()
        def failing_tool() -> str:
            raise ValueError("nope")


@pytest.fixture
async def t3_server():
    fake = FakeT3()
    port = free_port()
    config = uvicorn.Config(fake.mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning",
                            lifespan="on", timeout_graceful_shutdown=1)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.02)
    fake.url = f"http://127.0.0.1:{port}/mcp"
    yield fake
    server.should_exit = True
    await asyncio.wait_for(task, 10)


async def test_end_to_end_tools(t3_server, tmp_path):
    client = T3McpClient(t3_server.url, tmp_path / "tok.json", static_token="test-token")
    await client.start()
    await client.wait_ready(10)
    assert {"t3_thread_launch", "t3_thread_wait", "t3_thread_read"} <= set(client.tools)

    res = await client.launch(title="Minecraft Seite", message="Baue eine Seite", project_id="p2",
                              client_request_id="task-abc")
    assert res["thread_id"] == "thread-1" and res["run_id"] == "run:thread-1:1"
    # retry with same clientRequestId -> same thread (idempotent)
    res2 = await client.launch(title="Minecraft Seite", message="Baue eine Seite", project_id="p2",
                               client_request_id="task-abc")
    assert res2["thread_id"] == "thread-1"

    w = await client.wait("thread-1", res["run_id"], 1000)
    assert w["status"] == "running" and w["timedOut"] is True
    w = await client.wait("thread-1", res["run_id"], 1000)
    assert w["status"] == "completed"
    reply = await client.read_latest_reply("thread-1")
    assert reply["text"] == "Fertig: Baue eine Seite"

    s = await client.send("thread-1", "Catppuccin Mocha bitte", client_request_id="x")
    assert s["run_id"] == "run:thread-1:2"
    await client.interrupt("thread-1", s["run_id"], "stop", client_request_id="y")
    assert ("interrupt", {"threadId": "thread-1", "stop": True}) in t3_server.calls

    projects = await client.list_projects()
    assert [p["id"] for p in projects] == ["p1", "p2"]

    with pytest.raises(T3ToolError):
        await client.call("failing_tool", {})
    # session survives tool errors
    assert client.connected
    await client.close()


async def test_unknown_args_are_dropped(t3_server, tmp_path):
    client = T3McpClient(t3_server.url, tmp_path / "tok.json", static_token="t")
    await client.start()
    await client.wait_ready(10)
    await client.call("t3_thread_launch", {"title": "x", "message": "m", "inventedParam": 1})
    assert t3_server.calls[-1][0] == "launch"
    await client.close()


async def test_unreachable_server_reports_unavailable(tmp_path):
    from services.t3_mcp_client import T3Unavailable

    client = T3McpClient(f"http://127.0.0.1:{free_port()}/mcp", tmp_path / "t.json", static_token="t")
    await client.start()
    with pytest.raises(T3Unavailable):
        await client.wait_ready(1.5)
    assert client.last_error
    await client.close()


def test_helpers():
    assert find_key({"a": {"thread": {"threadId": "t1"}}}, "threadId") == "t1"
    assert find_key([{"x": 1}, {"runId": "r"}], "runId") == "r"
    schema = {"properties": {"a": {}, "b": {}}}
    assert filter_args(schema, {"a": 1, "b": None, "c": 3}) == {"a": 1}
    assert filter_args(None, {"a": 1, "b": None}) == {"a": 1}
