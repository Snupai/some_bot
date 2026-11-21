"""Shared fakes for Nyanjou tests."""
from __future__ import annotations

import asyncio


class FakeT3Client:
    """In-memory stand-in for T3McpClient (same method surface)."""

    def __init__(self, waits_until_done: int = 1):
        self.waits_until_done = waits_until_done
        self.threads: dict[str, dict] = {}
        self.launches: list[dict] = []
        self.sends: list[tuple[str, str]] = []
        self.interrupts: list[str] = []
        self.fail_launch = 0
        self.launch_exc: Exception | None = None
        self.gate: asyncio.Event | None = None  # if set, waits block until it is set
        self.connected = True
        self.tools = {"t3_thread_launch": None}
        for ext in ("ext-1", "ext-2"):
            self.threads[ext] = {"crid": None, "run": f"{ext}:1", "waits": 0, "status": "completed",
                                 "text": "external", "title": ext}

    async def launch(self, *, title, message, project_id, client_request_id, scratch=False,
                     workspace_strategy=None, runtime_mode=None, environment_id=None):
        if self.launch_exc:
            raise self.launch_exc
        if self.fail_launch:
            self.fail_launch -= 1
            raise ConnectionError("transient")
        for tid, t in self.threads.items():
            if t["crid"] == client_request_id:
                return {"thread_id": tid, "run_id": t["run"], "link": None}
        tid = f"th{len(self.launches) + 1}"
        self.threads[tid] = {"crid": client_request_id, "run": f"{tid}:1", "waits": 0, "status": "running",
                             "text": f"done: {title}"}
        self.launches.append({"title": title, "message": message, "project_id": project_id, "scratch": scratch,
                              "crid": client_request_id, "workspace_strategy": workspace_strategy})
        return {"thread_id": tid, "run_id": f"{tid}:1", "link": f"[t]({tid})"}

    async def wait(self, thread_id, run_id, timeout_ms, environment_id=None):
        if self.gate is not None:
            await self.gate.wait()
        t = self.threads[thread_id]
        t["waits"] += 1
        if t["status"] == "running" and t["waits"] >= self.waits_until_done:
            t["status"] = "completed"
        await asyncio.sleep(0)
        return {"threadId": thread_id, "runId": t["run"], "status": t["status"], "timedOut": t["status"] == "running"}

    async def read_latest_reply(self, thread_id, max_chars=6000, run_id=None, environment_id=None):
        t = self.threads[thread_id]
        return {"text": t["text"], "status": "idle" if t["status"] != "running" else "running", "link": None}

    async def send(self, thread_id, message, client_request_id, mode="auto", environment_id=None):
        t = self.threads[thread_id]
        self.sends.append((thread_id, message))
        n = int(t["run"].split(":")[1]) + 1
        t.update(run=f"{thread_id}:{n}", status="running", waits=0, text=f"updated: {message}")
        return {"run_id": t["run"]}

    async def interrupt(self, thread_id, run_id, reason, client_request_id, environment_id=None):
        self.interrupts.append(thread_id)
        self.threads[thread_id]["status"] = "interrupted"

    async def list_environments(self):
        return [{"id": "env-here", "label": "dieses System", "current": True},
                {"id": "env-vps", "label": "VPS Hetzner"}]

    async def list_threads(self, *, query=None, only_active=False, environment_id=None, limit=10):
        threads = [{"thread_id": "ext-1", "title": "Fix login tests", "status": "completed"},
                   {"thread_id": "ext-2", "title": "Minecraft site redesign", "status": "running"}]
        for tid, t in self.threads.items():
            threads.append({"thread_id": tid, "title": t.get("title", tid), "status": t["status"]})
        if query:
            q = query.lower()
            threads = [t for t in threads if q in t["title"].lower() or q == t["thread_id"]]
        return threads[:limit]

    async def list_projects(self):
        return [{"id": "p1", "title": "some_bot"}, {"id": "p2", "title": "minecraft-site"},
                {"id": "p3", "title": "minecraft-manager"}]

    def has_tool(self, name):
        return True


async def eventually(pred, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")
