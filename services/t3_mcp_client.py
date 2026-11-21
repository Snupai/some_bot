"""T3 Code MCP client.

Uses only the tools T3 documents for outside agents
(docs/user/outside-agents.md): hand off work with ``t3_thread_launch``, then
follow it with ``t3_thread_wait`` / ``t3_thread_read``. Also ``t3_thread_send``
(follow-up instructions), ``t3_thread_interrupt`` (cancel) and
``t3_project_list``. Tools are discovered at connect time. Arguments are
filtered against each tool's published ``inputSchema``, so the bot never
sends parameters the server doesn't declare.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .t3_oauth import FileTokenStorage, T3AuthRequired, non_interactive_provider

log = logging.getLogger("bot.py.nyanjou.t3")

REQUIRED_TOOLS = ("t3_thread_launch", "t3_thread_wait", "t3_thread_read")
OPTIONAL_TOOLS = ("t3_thread_send", "t3_thread_interrupt", "t3_project_list", "t3_thread_list")
NEWEST_POSITION = 2**31 - 1
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted", "rolled_back", "error", "stopped"}


class T3Error(RuntimeError):
    pass


class T3ToolError(T3Error):
    pass


class T3Unavailable(T3Error):
    pass


def _unwrap(exc: BaseException) -> BaseException:
    """anyio task groups wrap errors in ExceptionGroups; find the meaningful one."""
    seen = 0
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions and seen < 10:
        auth = [e for e in exc.exceptions if isinstance(_unwrap(e), T3AuthRequired)]
        exc = _unwrap(auth[0]) if auth else exc.exceptions[0]
        seen += 1
    return exc


def parse_tool_result(result: Any) -> Any:
    """Return parsed JSON text, structured content, or plain text. Raises on isError.

    T3 returns JSON as text content. Some MCP servers also wrap string results
    as ``structuredContent={"result": "<json>"}``. Both forms are unwrapped.
    """
    texts = [getattr(c, "text", "") for c in (getattr(result, "content", None) or [])]
    text = "\n".join(t for t in texts if t)
    if getattr(result, "isError", False):
        raise T3ToolError(text or "T3 Code returned an error")
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and set(structured) == {"result"} and isinstance(structured["result"], str):
        try:
            return json.loads(structured["result"])
        except json.JSONDecodeError:
            return structured["result"]
    if structured:
        return structured
    return text or {}


def find_key(obj: Any, *keys: str) -> Any:
    """Depth-first search for the first present key (T3 results nest thread/run objects)."""
    if isinstance(obj, dict):
        for k in keys:
            if obj.get(k) not in (None, ""):
                return obj[k]
        for v in obj.values():
            found = find_key(v, *keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, *keys)
            if found not in (None, ""):
                return found
    return None


def filter_args(schema: dict | None, args: dict[str, Any]) -> dict[str, Any]:
    props = (schema or {}).get("properties")
    clean = {k: v for k, v in args.items() if v is not None}
    if not isinstance(props, dict):
        return clean
    dropped = set(clean) - set(props)
    if dropped:
        log.debug("dropping args not in tool schema: %s", sorted(dropped))
    return {k: v for k, v in clean.items() if k in props}


class T3McpClient:
    """Persistent MCP session with reconnect. Safe to use from many tasks at once."""

    def __init__(
        self,
        url: str,
        token_file,
        static_token: str = "",
        *,
        transport_factory=None,
    ) -> None:
        self.url = url
        self.storage = FileTokenStorage(token_file, url)
        self.static_token = static_token
        self._transport_factory = transport_factory
        self._session: ClientSession | None = None
        self._ready = asyncio.Event()
        self._reset = asyncio.Event()
        self._stop = asyncio.Event()
        self._runner: asyncio.Task | None = None
        self.tools: dict[str, Any] = {}
        self.last_error: str | None = None
        self.auth_required = False

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._stop.clear()
            self._runner = asyncio.create_task(self._run(), name="nyanjou-t3-mcp")

    async def close(self) -> None:
        self._stop.set()
        self._reset.set()
        if self._runner:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._runner, 5)
        self._ready.clear()

    @property
    def connected(self) -> bool:
        return self._ready.is_set()

    def _http_client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(30.0, read=300.0)
        if self.static_token:
            return httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self.static_token}"}, timeout=timeout, follow_redirects=False
            )
        return httpx.AsyncClient(auth=non_interactive_provider(self.url, self.storage), timeout=timeout)

    def _transport(self, http: httpx.AsyncClient):
        if self._transport_factory is not None:
            return self._transport_factory(self.url, http)
        return streamable_http_client(self.url, http_client=http)

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with self._http_client() as http:
                    async with self._transport(http) as streams:
                        read, write = streams[0], streams[1]
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            listed = await session.list_tools()
                            self.tools = {t.name: t for t in listed.tools}
                            missing = [t for t in REQUIRED_TOOLS if t not in self.tools]
                            if missing:
                                raise T3Unavailable(f"T3 MCP is missing required tools: {missing}")
                            self._session = session
                            self.auth_required = False
                            self.last_error = None
                            self._reset.clear()
                            self._ready.set()
                            log.info("T3 MCP connected (%d tools)", len(self.tools))
                            backoff = 1.0
                            await self._reset.wait()
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # includes ExceptionGroup from anyio
                inner = _unwrap(exc)
                self.last_error = f"{type(inner).__name__}: {inner}"
                if isinstance(inner, T3AuthRequired):
                    self.auth_required = True
                    log.error("T3 MCP: %s", inner)
                    backoff = 300.0  # waiting for a human to log in again
                else:
                    log.warning("T3 MCP connection error: %s", self.last_error)
            finally:
                self._ready.clear()
                self._session = None
            if self._stop.is_set():
                break
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), backoff)
            backoff = min(backoff * 2, 300.0)

    def reconnect(self) -> None:
        self._reset.set()

    async def wait_ready(self, timeout: float = 20.0) -> None:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError as exc:
            if self.auth_required:
                raise T3AuthRequired(self.last_error or "T3 sign-in required") from exc
            raise T3Unavailable(self.last_error or "T3 MCP not reachable") from exc

    # ------------------------------------------------------------------ calls
    def has_tool(self, name: str) -> bool:
        return name in self.tools

    async def call(self, name: str, args: dict[str, Any], timeout_s: float = 60.0) -> Any:
        await self.wait_ready()
        tool = self.tools.get(name)
        if tool is None:
            raise T3Unavailable(f"T3 MCP does not offer {name}")
        session = self._session
        if session is None:
            raise T3Unavailable("T3 MCP session lost")
        clean = filter_args(getattr(tool, "inputSchema", None), args)
        try:
            result = await session.call_tool(name, clean, read_timeout_seconds=timedelta(seconds=timeout_s))
        except T3ToolError:
            raise
        except Exception as exc:
            inner = _unwrap(exc)
            # MCP-level errors (tool validation etc.) keep the session; transport errors reset it.
            from mcp.shared.exceptions import McpError

            if not isinstance(inner, McpError):
                self.reconnect()
            raise T3Error(f"{name} failed: {type(inner).__name__}: {inner}") from exc
        return parse_tool_result(result)

    # ------------------------------------------------------------------ helpers
    async def list_projects(self) -> list[dict]:
        if not self.has_tool("t3_project_list"):
            return []
        projects: list[dict] = []
        cursor = None
        for _ in range(10):
            res = await self.call("t3_project_list", {"limit": 100, "cursor": cursor})
            projects += [p for p in (res.get("projects") or []) if not p.get("archivedAt") and not p.get("deletedAt")]
            cursor = res.get("nextCursor")
            if cursor in (None, "", 0) or not res.get("projects"):
                break
        return projects

    async def launch(self, *, title: str, message: str, project_id: str | None, client_request_id: str,
                     scratch: bool = False, workspace_strategy: dict | None = None,
                     runtime_mode: str | None = None, environment_id: str | None = None) -> dict:
        args: dict[str, Any] = {"title": title[:200], "message": message, "clientRequestId": client_request_id,
                                "environmentId": environment_id}
        if scratch:
            args["scratch"] = True
        else:
            args["projectId"] = project_id
            if workspace_strategy:
                args["workspaceStrategy"] = workspace_strategy
        if runtime_mode:
            args["runtimeMode"] = runtime_mode
        res = await self.call("t3_thread_launch", args, timeout_s=120)
        thread_id = find_key(res, "threadId")
        if not thread_id:
            raise T3Error(f"t3_thread_launch returned no threadId: {str(res)[:300]}")
        return {
            "thread_id": thread_id,
            "run_id": find_key(res, "runId", "activeRunId", "latestRunId"),
            "link": find_key(res, "link"),
            "raw": res,
        }

    async def send(self, thread_id: str, message: str, client_request_id: str, mode: str = "auto",
                   environment_id: str | None = None) -> dict:
        res = await self.call(
            "t3_thread_send",
            {"threadId": thread_id, "message": message, "mode": mode, "clientRequestId": client_request_id,
             "environmentId": environment_id},
            timeout_s=60,
        )
        return {"run_id": find_key(res, "runId", "activeRunId", "latestRunId"), "raw": res}

    async def wait(self, thread_id: str, run_id: str | None, timeout_ms: int, environment_id: str | None = None) -> dict:
        res = await self.call(
            "t3_thread_wait",
            {"threadId": thread_id, "runId": run_id, "timeoutMs": timeout_ms, "environmentId": environment_id},
            timeout_s=timeout_ms / 1000 + 60,
        )
        if not isinstance(res, dict):
            res = {"text": str(res)}
        return res

    async def read_latest_reply(self, thread_id: str, max_chars: int = 6000, run_id: str | None = None,
                                environment_id: str | None = None) -> dict:
        # A cursorless read pages forward from the *oldest* items for outside agents
        # (OrchestratorMcpService.readThread); beforePosition selects the newest page.
        res = await self.call(
            "t3_thread_read",
            {"threadId": thread_id, "view": "messages", "limit": 10, "maxCharsPerItem": max_chars,
             "runLimit": 1, "beforePosition": NEWEST_POSITION, "environmentId": environment_id},
        )
        items = res.get("items") if isinstance(res, dict) else None
        replies = [i for i in (items or []) if i.get("type") == "assistant_message" and i.get("text")]
        replies.sort(key=lambda i: i.get("position") or 0, reverse=True)  # newest first
        if run_id:
            # Never report an earlier run's reply for a run that produced none.
            replies = [i for i in replies if i.get("runId") == run_id]
        thread = res.get("thread") if isinstance(res, dict) else {}
        return {
            "text": replies[0]["text"] if replies else "",
            "status": (thread or {}).get("status"),
            "pending_requests": (thread or {}).get("pendingRequestCount", 0),
            "link": (thread or {}).get("link"),
            "title": (thread or {}).get("title"),
        }

    async def interrupt(self, thread_id: str, run_id: str | None, reason: str, client_request_id: str,
                        environment_id: str | None = None) -> Any:
        if not self.has_tool("t3_thread_interrupt"):
            raise T3Unavailable("T3 MCP does not offer t3_thread_interrupt")
        return await self.call(
            "t3_thread_interrupt",
            {"threadId": thread_id, "runId": run_id, "stop": True, "reason": reason[:500],
             "clientRequestId": client_request_id, "environmentId": environment_id},
        )

    async def list_environments(self) -> list[dict]:
        """This environment plus linked ones (t3_environment_links)."""
        if not self.has_tool("t3_environment_links"):
            return []
        res = await self.call("t3_environment_links", {})
        if not isinstance(res, dict):
            return []
        envs = [{"id": res.get("environmentId"), "label": "dieses System", "current": True}]
        for link in res.get("links") or []:
            envs.append({"id": link.get("environmentId"), "label": link.get("label") or link.get("name"),
                         "reachable": link.get("reachable", link.get("status"))})
        return [e for e in envs if e.get("id")]

    async def list_threads(self, *, query: str | None = None, only_active: bool = False,
                           environment_id: str | None = None, limit: int = 10) -> list[dict]:
        if query and self.has_tool("t3_thread_search"):
            res = await self.call("t3_thread_search", {"query": query, "limit": limit, "environmentId": environment_id})
        else:
            args: dict[str, Any] = {"allProjects": True, "limit": limit, "environmentId": environment_id}
            if only_active:
                args["statuses"] = ["queued", "starting", "running", "waiting", "preparing"]
            res = await self.call("t3_thread_list", args)
        threads = (res.get("threads") or res.get("matches") or []) if isinstance(res, dict) else []
        out = []
        for t in threads:
            if t.get("parentThreadId"):
                continue  # skip subagent threads
            out.append({"thread_id": t.get("threadId"), "title": t.get("title"), "status": t.get("status"),
                        "project_id": t.get("projectId"), "updated_at": t.get("updatedAt")})
        return out
