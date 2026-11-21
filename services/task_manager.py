"""Async scheduling and monitoring of T3 Code tasks.

* ``submit`` stores a task and queues it. Workers launch it in T3 Code with
  ``t3_thread_launch``, using the task ID as ``clientRequestId`` so retries
  after a crash can't create duplicate threads.
* Each launched task gets a monitor that loops ``t3_thread_wait`` in slices.
  When the run ends it reads the agent's last reply with ``t3_thread_read``
  and notifies listeners: the voice session, which tells GPT-Live, and the
  Discord channel.
* ``update`` sends follow-up instructions with ``t3_thread_send`` (mode
  ``auto``: steer a running turn, or queue one).
* ``cancel`` stops the run with ``t3_thread_interrupt``.
* On startup, unfinished tasks are resumed from SQLite.

At most ``max_parallel`` tasks are launched or running at once; the rest wait
in the queue. Nothing here blocks the voice conversation.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from typing import Awaitable, Callable

from .t3_mcp_client import TERMINAL_STATUSES, T3Error, T3McpClient
from .t3_oauth import T3AuthRequired
from .task_store import ACTIVE_STATUSES, TaskRecord, TaskStore

log = logging.getLogger("bot.py.nyanjou.tasks")

Listener = Callable[[TaskRecord, str], Awaitable[None]]


class TaskManager:
    def __init__(
        self,
        store: TaskStore,
        t3: T3McpClient | None,
        *,
        max_parallel: int = 4,
        task_timeout_s: int = 7200,
        wait_slice_ms: int = 25000,
        workspace_strategy: dict | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        self.store = store
        self.t3 = t3
        self.max_parallel = max_parallel
        self.task_timeout_s = task_timeout_s
        self.wait_slice_ms = wait_slice_ms
        self.workspace_strategy = workspace_strategy
        self.runtime_mode = runtime_mode or None
        self._slots = asyncio.Semaphore(max_parallel)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._monitors: dict[str, asyncio.Task] = {}
        # Per-task lock: launch, follow-ups, cancel and applying results are serialized per task.
        self._locks: dict[str, asyncio.Lock] = {}
        self._retiring: set[asyncio.Task] = set()  # monitors finishing their last notification
        self._listeners: list[Listener] = []
        self._started = False

    # ------------------------------------------------------------------ lifecycle
    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Listener) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(fn)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        # Recover first and let recovered monitors claim their slots before workers launch new tasks.
        await self.recover()
        await asyncio.sleep(0)
        for i in range(self.max_parallel):
            self._workers.append(asyncio.create_task(self._worker(), name=f"nyanjou-task-worker-{i}"))

    async def close(self) -> None:
        for t in [*self._workers, *self._monitors.values()]:
            t.cancel()
        # Let retiring monitors finish their final notification (e.g. recording delivery) first.
        if self._retiring:
            _, pending = await asyncio.wait(list(self._retiring), timeout=5)
            for t in pending:  # a stalled notification must not block shutdown
                t.cancel()
        await asyncio.gather(*self._workers, *self._monitors.values(), *self._retiring, return_exceptions=True)
        self._retiring.clear()
        self._workers.clear()
        self._monitors.clear()
        self._started = False

    async def recover(self) -> None:
        """Resume tasks that were active when the bot stopped."""
        for rec in self.store.active():
            if rec.thread_id:
                log.info("resuming monitor for task #%s (thread %s)", rec.short_id, rec.thread_id)
                self._spawn_monitor(rec)
            else:
                log.info("re-queueing task #%s", rec.short_id)
                self.store.update(rec.id, status="queued")
                await self._queue.put(rec.id)

    def _lock(self, task_id: str) -> asyncio.Lock:
        lock = self._locks.get(task_id)
        if lock is None:
            lock = self._locks[task_id] = asyncio.Lock()
        return lock

    async def _emit(self, rec: TaskRecord, kind: str) -> None:
        for fn in list(self._listeners):
            try:
                await fn(rec, kind)
            except Exception:
                log.exception("task listener failed")

    # ------------------------------------------------------------------ API
    async def submit(self, rec: TaskRecord) -> TaskRecord:
        if self.t3 is None:
            raise T3Error("T3 Code ist nicht konfiguriert (T3_MCP_URL fehlt).")
        rec.deadline_at = time.time() + self.task_timeout_s
        rec.status = "queued"
        rec = self.store.insert(rec)
        self.store.add_update(rec.id, "prompt", rec.prompt)
        await self._queue.put(rec.id)
        await self._emit(rec, "queued")
        return rec

    async def update(self, rec: TaskRecord, message: str) -> TaskRecord:
        if self.t3 is None:
            raise T3Error("T3 Code ist nicht konfiguriert.")
        async with self._lock(rec.id):
            rec = self.store.get(rec.id) or rec
            if rec.status == "cancelled":
                raise T3Error(f"Aufgabe #{rec.short_id} wurde abgebrochen.")
            return await self._update_locked(rec, message)

    async def _update_locked(self, rec: TaskRecord, message: str) -> TaskRecord:
        assert self.t3 is not None
        if not rec.thread_id:
            # Not launched yet: fold the change into the prompt that will be sent.
            new_prompt = f"{rec.prompt}\n\nNachtrag des Nutzers:\n{message}"
            self.store.update(rec.id, prompt=new_prompt)
            self.store.add_update(rec.id, "followup", message)
            return self.store.get(rec.id)  # type: ignore[return-value]
        reopening = not rec.active
        if reopening:
            # Reopening starts remote work, so it needs a slot first (refuse instead of exceeding the limit).
            if self._slots.locked():
                raise T3Error("Parallel-Limit erreicht; bitte später erneut versuchen.")
            await self._slots.acquire()
        try:
            res = await self.t3.send(rec.thread_id, message, client_request_id=f"{rec.id}-{uuid.uuid4().hex[:8]}",
                                     environment_id=rec.environment_id)
        except BaseException:
            if reopening:
                self._slots.release()
            raise
        self.store.add_update(rec.id, "followup", message)
        fields: dict = {"status": "running", "error": None, "notified": False}
        if res.get("run_id"):
            fields["run_id"] = res["run_id"]
        if not rec.active:
            fields["deadline_at"] = time.time() + self.task_timeout_s
            fields["result"] = None
        rec = self.store.update(rec.id, **fields)  # type: ignore[assignment]
        if reopening:
            self._spawn_monitor(rec, holds_slot=True)
        elif rec.id not in self._monitors:
            self._spawn_monitor(rec)
        await self._emit(rec, "updated")
        return rec

    async def cancel(self, rec: TaskRecord, reason: str = "Vom Nutzer abgebrochen") -> TaskRecord:
        # Taking the task lock waits out an in-flight launch, so a returned thread is interrupted too.
        async with self._lock(rec.id):
            rec = self.store.get(rec.id) or rec
            if not rec.active:
                return rec  # already finished: keep its real outcome
            if rec.thread_id and self.t3 is not None:
                await self.t3.interrupt(rec.thread_id, rec.run_id, reason, client_request_id=f"{rec.id}-cancel",
                                        environment_id=rec.environment_id)
            rec = self.store.update(rec.id, status="cancelled", error=reason)  # type: ignore[assignment]
        mon = self._monitors.pop(rec.id, None)
        if mon:
            mon.cancel()
        await self._emit(rec, "cancelled")
        return rec

    async def adopt(self, *, thread_id: str, environment_id: str | None, title: str, message: str, user_id: int,
                    guild_id: int | None, channel_id: int | None, source: str = "voice") -> TaskRecord:
        """Send ``message`` to an existing T3 thread (not started by Nyanjou) and track it like a task."""
        existing = [r for r in self.store.list(guild_id=guild_id, user_id=user_id, limit=200)
                    if r.thread_id == thread_id and r.environment_id == environment_id]
        if existing:
            return await self.update(existing[0], message)
        rec = TaskRecord(user_id=user_id, title=title[:120] or "T3-Thread", prompt=f"(bestehender Thread) {message}",
                         source=source, guild_id=guild_id, channel_id=channel_id, environment_id=environment_id,
                         thread_id=thread_id, status="completed")  # inactive until the message reopens it
        rec = self.store.insert(rec)
        return await self.update(rec, message)

    def get(self, short_id: int) -> TaskRecord | None:
        return self.store.get_by_short_id(short_id)

    def list(self, **kw) -> list[TaskRecord]:
        return self.store.list(**kw)

    def latest_active_for(self, guild_id: int | None, user_id: int) -> TaskRecord | None:
        tasks = self.store.list(guild_id=guild_id, user_id=user_id, statuses=ACTIVE_STATUSES, limit=1)
        if tasks:
            return tasks[0]
        # Fall back to the user's most recent task in this guild (to amend a finished one).
        recent = self.store.list(guild_id=guild_id, user_id=user_id, limit=1)
        return recent[0] if recent else None

    @property
    def queued_count(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------ internals
    async def _worker(self) -> None:
        while True:
            task_id = await self._queue.get()
            try:
                rec = self.store.get(task_id)
                if rec is None or rec.status != "queued":
                    continue
                await self._slots.acquire()
                try:
                    async with self._lock(task_id):
                        rec = self.store.get(task_id)  # may have been cancelled while waiting for a slot
                        launched = await self._launch(rec) if rec is not None and rec.status == "queued" else None
                except BaseException:
                    self._slots.release()
                    raise
                if launched is None:
                    self._slots.release()
                    continue
                self._spawn_monitor(launched, holds_slot=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("task worker error")
            finally:
                self._queue.task_done()

    async def _launch(self, rec: TaskRecord) -> TaskRecord | None:
        assert self.t3 is not None
        rec = self.store.update(rec.id, status="starting")  # type: ignore[assignment]
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                res = await self.t3.launch(
                    title=rec.title,
                    message=rec.prompt,
                    project_id=rec.project_id,
                    scratch=rec.scratch,
                    client_request_id=rec.id,  # idempotent retries
                    workspace_strategy=None if rec.scratch else self.workspace_strategy,
                    runtime_mode=self.runtime_mode,
                    environment_id=rec.environment_id,
                )
                rec = self.store.update(  # type: ignore[assignment]
                    rec.id, status="running", thread_id=res["thread_id"],
                    run_id=res.get("run_id"), thread_link=res.get("link"),
                )
                log.info("task #%s launched as T3 thread %s", rec.short_id, rec.thread_id)
                await self._emit(rec, "started")
                return rec
            except T3AuthRequired as exc:
                last_exc = exc
                break
            except Exception as exc:
                last_exc = exc
                log.warning("launch attempt %d for task #%s failed: %s", attempt + 1, rec.short_id, exc)
                await asyncio.sleep(2 * (attempt + 1))
        rec = self.store.update(rec.id, status="failed", error=f"Start fehlgeschlagen: {last_exc}")  # type: ignore[assignment]
        await self._emit(rec, "failed")
        return None

    def _spawn_monitor(self, rec: TaskRecord, holds_slot: bool = False) -> None:
        old = self._monitors.get(rec.id)
        if old and not old.done():
            if holds_slot:
                self._slots.release()  # an existing monitor already accounts for this task
            return
        # Slot ownership lives outside the coroutine, so a monitor cancelled before it ever ran
        # (e.g. cancel right after reopening) still returns its slot.
        slot = {"held": holds_slot}
        task = asyncio.create_task(self._monitor(rec.id, slot), name=f"nyanjou-monitor-{rec.short_id}")
        self._monitors[rec.id] = task

        def _release_on_done(_t: asyncio.Task, slot=slot, task_id=rec.id) -> None:
            if self._monitors.get(task_id) is _t:
                del self._monitors[task_id]
            if slot["held"]:
                slot["held"] = False
                self._slots.release()
            self._retiring.discard(_t)

        task.add_done_callback(_release_on_done)

    def _retire(self, task_id: str, slot: dict) -> None:
        """Stop tracking this monitor as the task's monitor (so a follow-up gets a new one) and free its
        slot, before awaiting listeners. close() still waits for it via ``_retiring``."""
        current = asyncio.current_task()
        if self._monitors.get(task_id) is current:
            del self._monitors[task_id]
        if current is not None:
            self._retiring.add(current)
        if slot["held"]:
            slot["held"] = False
            self._slots.release()

    async def _monitor(self, task_id: str, slot: dict) -> None:
        assert self.t3 is not None
        errors = 0
        if not slot["held"]:
            # Recovered or reopened tasks count against the parallel limit too.
            await self._slots.acquire()
            slot["held"] = True
        while True:
            rec = self.store.get(task_id)
            if rec is None or not rec.active or not rec.thread_id:
                return
            if rec.deadline_at and time.time() > rec.deadline_at:
                rec = self.store.update(task_id, status="timed_out",  # type: ignore[assignment]
                                        error="Zeitlimit überschritten; T3 Code arbeitet ggf. weiter.")
                self._retire(task_id, slot)
                await self._emit(rec, "timed_out")
                return
            waited_run = rec.run_id
            try:
                res = await self.t3.wait(rec.thread_id, waited_run, self.wait_slice_ms, environment_id=rec.environment_id)
                errors = 0
            except asyncio.CancelledError:
                raise
            except T3AuthRequired as exc:
                rec = self.store.update(task_id, error=str(exc))  # type: ignore[assignment]
                await self._emit(rec, "auth_required")
                await asyncio.sleep(300)
                continue
            except Exception as exc:
                errors += 1
                log.warning("wait for task #%s failed (%d): %s", rec.short_id, errors, exc)
                await asyncio.sleep(min(60, 2 ** errors))
                continue

            status = str(res.get("status") or "").lower()
            emit: tuple[TaskRecord, str] | None = None
            delay = 0.0
            async with self._lock(task_id):  # every wait-driven write sees the current row
                current = self.store.get(task_id)
                if current is None or not current.active:
                    return
                if current.run_id != waited_run or (
                        current.run_id and res.get("runId") and current.run_id != res.get("runId")):
                    continue  # response for an older run (a follow-up started a newer one): ignore
                # T3 may report "waiting" (pending approval/input) together with timedOut=true.
                if status == "waiting":
                    delay = 10.0
                    if current.status != "waiting_input":
                        emit = (self.store.update(task_id, status="waiting_input"), "waiting_input")  # type: ignore[assignment]
                elif current.status == "waiting_input":
                    self.store.update(task_id, status="running")
                if status == "waiting" or res.get("timedOut") or status in {"running", "starting", "queued", "preparing"}:
                    pass
                elif status in TERMINAL_STATUSES or status in {"idle", ""}:
                    reply = await self._safe_read(rec.thread_id, res.get("runId"), rec.environment_id)
                    if reply.get("status") == "running":
                        # A follow-up was queued behind the finished run; keep watching.
                        self.store.update(task_id, run_id=None)
                        continue
                    final = "completed" if status in {"completed", "idle", ""} else (
                        "cancelled" if status in {"cancelled", "interrupted", "stopped"} else "failed")
                    text = reply.get("text") or res.get("text") or ""
                    rec = self.store.update(  # type: ignore[assignment]
                        task_id, status=final, result=text[:8000] or None,
                        thread_link=reply.get("link") or rec.thread_link,
                        error=None if final == "completed" else f"T3-Status: {status}",
                    )
                    self.store.add_update(task_id, "result", text or status)
                    emit = (rec, final)
                else:
                    log.debug("unknown T3 status %r for task #%s", status, rec.short_id)
                    delay = 5.0
            if emit is not None:
                final_event = emit[1] != "waiting_input"
                if final_event:
                    self._retire(task_id, slot)
                await self._emit(*emit)
                if final_event:
                    return
            if delay:
                await asyncio.sleep(delay)

    async def _safe_read(self, thread_id: str, run_id: str | None = None, environment_id: str | None = None) -> dict:
        assert self.t3 is not None
        try:
            return await self.t3.read_latest_reply(thread_id, run_id=run_id, environment_id=environment_id)
        except Exception as exc:
            log.warning("could not read result of thread %s: %s", thread_id, exc)
            return {}
