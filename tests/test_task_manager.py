import asyncio
import time

import pytest

from services.t3_oauth import T3AuthRequired
from services.task_manager import TaskManager
from services.task_store import TaskRecord, TaskStore
from tests.fakes import FakeT3Client, eventually


def rec(user=1, guild=10, title="Seite", **kw):
    return TaskRecord(user_id=user, title=title, prompt=f"Baue {title}", source="voice", guild_id=guild,
                      channel_id=99, project_id="p1", **kw)


@pytest.fixture
def store(tmp_path):
    s = TaskStore(tmp_path / "tasks.sqlite")
    yield s
    s.close()


async def make(store, t3, **kw):
    tm = TaskManager(store, t3, wait_slice_ms=10, **kw)
    events = []

    async def listener(r, kind):
        events.append((r.short_id, kind))

    tm.add_listener(listener)
    await tm.start()
    return tm, events


async def test_submit_launch_complete_notify(store):
    t3 = FakeT3Client(waits_until_done=2)
    tm, events = await make(store, t3)
    r = await tm.submit(rec())
    assert r.short_id == 1
    await eventually(lambda: (1, "completed") in events)
    final = store.get(r.id)
    assert final.status == "completed" and final.result == "done: Seite" and final.thread_id == "th1"
    assert [k for _, k in events] == ["queued", "started", "completed"]
    assert t3.launches[0]["crid"] == r.id  # task id used as idempotency key
    await tm.close()


async def test_parallel_limit(store):
    t3 = FakeT3Client()
    t3.gate = asyncio.Event()
    tm, events = await make(store, t3, max_parallel=2)
    for i in range(4):
        await tm.submit(rec(title=f"T{i}"))
    await eventually(lambda: len(t3.launches) == 2)
    await asyncio.sleep(0.1)
    assert len(t3.launches) == 2  # third waits for a free slot
    assert sorted(r.status for r in store.list(limit=10)) == ["queued", "queued", "running", "running"]
    t3.gate.set()
    await eventually(lambda: sum(1 for _, k in events if k == "completed") == 4)
    await tm.close()


async def test_transient_launch_failure_retries(store):
    t3 = FakeT3Client()
    t3.fail_launch = 1
    tm, events = await make(store, t3)
    r = await tm.submit(rec())
    await eventually(lambda: (r.short_id, "completed") in events, timeout=6)
    assert len(t3.launches) == 1
    await tm.close()


async def test_auth_required_fails_task(store):
    t3 = FakeT3Client()
    t3.launch_exc = T3AuthRequired("login needed")
    tm, events = await make(store, t3)
    r = await tm.submit(rec())
    await eventually(lambda: (r.short_id, "failed") in events)
    assert "login needed" in store.get(r.id).error
    await tm.close()


async def test_update_running_and_finished_task(store):
    t3 = FakeT3Client()
    t3.gate = asyncio.Event()
    tm, events = await make(store, t3)
    r = await tm.submit(rec())
    await eventually(lambda: store.get(r.id).thread_id is not None)
    await tm.update(store.get(r.id), "Design in Catppuccin Mocha")
    assert t3.sends == [("th1", "Design in Catppuccin Mocha")]
    t3.gate.set()
    await eventually(lambda: store.get(r.id).status == "completed")
    assert store.get(r.id).result == "updated: Design in Catppuccin Mocha"
    # Amend a finished task: it is re-activated and monitored again.
    await tm.update(store.get(r.id), "Noch ein Footer")
    await eventually(lambda: store.get(r.id).result == "updated: Noch ein Footer")
    await tm.close()


async def test_update_before_launch_amends_prompt(store):
    t3 = FakeT3Client()
    tm = TaskManager(store, t3)  # not started -> stays queued
    r = await tm.submit(rec())
    await tm.update(r, "bitte dunkel")
    assert "bitte dunkel" in store.get(r.id).prompt
    assert t3.sends == []


async def test_cancel(store):
    t3 = FakeT3Client()
    t3.gate = asyncio.Event()
    tm, events = await make(store, t3)
    r = await tm.submit(rec())
    await eventually(lambda: store.get(r.id).thread_id is not None)
    await tm.cancel(store.get(r.id))
    assert t3.interrupts == ["th1"]
    assert store.get(r.id).status == "cancelled"
    t3.gate.set()
    await asyncio.sleep(0.05)
    assert store.get(r.id).status == "cancelled"
    await tm.close()


async def test_recovery_after_restart(store):
    t3 = FakeT3Client()
    t3.gate = asyncio.Event()
    tm, _ = await make(store, t3)
    launched = await tm.submit(rec(title="A"))
    await eventually(lambda: store.get(launched.id).thread_id is not None)
    await tm.close()  # "crash" while running
    # A queued-but-never-launched task, too:
    store.insert(rec(title="B"))

    t3.gate.set()
    tm2, events = await make(store, t3)
    await eventually(lambda: all(r.status == "completed" for r in store.list(limit=10)))
    assert len(t3.launches) == 2  # A was not launched twice
    await tm2.close()


async def test_timeout(store):
    t3 = FakeT3Client(waits_until_done=10_000)
    tm, events = await make(store, t3, task_timeout_s=0)
    r = await tm.submit(rec())
    await eventually(lambda: (r.short_id, "timed_out") in events)
    await tm.close()


async def test_isolation_queries(store):
    t3 = FakeT3Client()
    tm = TaskManager(store, t3)
    a = await tm.submit(rec(user=1, guild=10))
    await tm.submit(rec(user=2, guild=10))
    await tm.submit(rec(user=1, guild=20))
    assert tm.latest_active_for(10, 1).id == a.id
    assert [r.user_id for r in tm.list(guild_id=10, user_id=1)] == [1]
    assert time.time() - a.created_at < 5


# --- regressions from review ----------------------------------------------------------------


async def test_cancel_while_waiting_for_slot_never_launches(store):
    t3 = FakeT3Client()
    t3.gate = asyncio.Event()
    tm, _ = await make(store, t3, max_parallel=1)
    first = await tm.submit(rec(title="A"))
    await eventually(lambda: store.get(first.id).thread_id is not None)
    second = await tm.submit(rec(title="B"))
    await asyncio.sleep(0.05)  # worker picked B up and waits for the slot
    await tm.cancel(store.get(second.id))
    t3.gate.set()
    await eventually(lambda: store.get(first.id).status == "completed")
    await asyncio.sleep(0.05)
    assert [launch["title"] for launch in t3.launches] == ["A"]
    assert store.get(second.id).status == "cancelled"
    await tm.close()


async def test_cancel_during_launch_interrupts_returned_thread(store):
    t3 = FakeT3Client()
    release = asyncio.Event()
    orig = t3.launch

    async def slow_launch(**kw):
        await release.wait()
        return await orig(**kw)

    t3.launch = slow_launch
    tm, _ = await make(store, t3)
    r = await tm.submit(rec())
    await eventually(lambda: store.get(r.id).status == "starting")
    cancel = asyncio.create_task(tm.cancel(store.get(r.id)))
    await asyncio.sleep(0.05)
    assert not cancel.done()  # waits for the in-flight launch instead of "succeeding" early
    release.set()
    await cancel
    assert t3.interrupts == ["th1"]
    assert store.get(r.id).status == "cancelled"
    await asyncio.sleep(0.05)
    assert store.get(r.id).status == "cancelled"
    await tm.close()


async def test_stale_run_result_is_ignored_after_followup(store):
    class StaleT3(FakeT3Client):
        async def wait(self, thread_id, run_id, timeout_ms, environment_id=None):
            t = self.threads[thread_id]
            run_before = t["run"]
            if self.gate is not None and not self.gate.is_set():
                await self.gate.wait()
                # Report the *old* run as completed after a follow-up started a new one.
                return {"threadId": thread_id, "runId": run_before, "status": "completed", "timedOut": False}
            return await super().wait(thread_id, run_id, timeout_ms, environment_id)

    t3 = StaleT3()
    t3.gate = asyncio.Event()
    tm, events = await make(store, t3)
    r = await tm.submit(rec())
    await eventually(lambda: store.get(r.id).thread_id is not None)
    await asyncio.sleep(0.02)  # monitor is blocked inside wait() for run th1:1
    await tm.update(store.get(r.id), "Catppuccin")
    t3.gate.set()
    await eventually(lambda: store.get(r.id).status == "completed")
    assert store.get(r.id).result == "updated: Catppuccin"
    assert store.get(r.id).run_id == "th1:2"
    await tm.close()


async def test_recovered_run_counts_against_parallel_limit(store):
    t3 = FakeT3Client()
    t3.gate = asyncio.Event()
    old = store.insert(rec(title="old"))
    t3.threads["thx"] = {"crid": old.id, "run": "thx:1", "waits": 0, "status": "running", "text": "old done"}
    store.update(old.id, status="running", thread_id="thx", run_id="thx:1", deadline_at=time.time() + 600)
    tm, _ = await make(store, t3, max_parallel=1)
    new = await tm.submit(rec(title="new"))
    await asyncio.sleep(0.1)
    assert t3.launches == []  # the recovered run holds the only slot
    t3.gate.set()
    await eventually(lambda: store.get(new.id).status == "completed")
    assert store.get(old.id).status == "completed"
    await tm.close()


async def test_waiting_with_timed_out_flag_notifies_and_recovers(store):
    class WaitingT3(FakeT3Client):
        phase = "waiting"

        async def wait(self, thread_id, run_id, timeout_ms, environment_id=None):
            await asyncio.sleep(0)
            t = self.threads[thread_id]
            if self.phase == "waiting":
                return {"threadId": thread_id, "runId": t["run"], "status": "waiting", "timedOut": True}
            if self.phase == "running":
                return {"threadId": thread_id, "runId": t["run"], "status": "running", "timedOut": True}
            t["status"] = "completed"
            return {"threadId": thread_id, "runId": t["run"], "status": "completed", "timedOut": False}

    t3 = WaitingT3()
    tm, events = await make(store, t3)
    import services.task_manager as tmmod
    real_sleep = asyncio.sleep

    async def fast_sleep(s):
        await real_sleep(min(s, 0.01))

    tmmod.asyncio.sleep = fast_sleep
    try:
        r = await tm.submit(rec())
        await eventually(lambda: (r.short_id, "waiting_input") in events)
        assert store.get(r.id).status == "waiting_input"
        t3.phase = "running"
        await eventually(lambda: store.get(r.id).status == "running")
        t3.phase = "done"
        await eventually(lambda: store.get(r.id).status == "completed")
    finally:
        tmmod.asyncio.sleep = real_sleep
    await tm.close()


async def test_followup_from_completion_listener_gets_a_monitor(store):
    t3 = FakeT3Client()
    tm = TaskManager(store, t3, wait_slice_ms=10)
    done = []

    async def listener(r, kind):
        if kind == "completed" and not done:
            done.append(r.id)
            await tm.update(store.get(r.id), "Noch ein Footer")

    tm.add_listener(listener)
    await tm.start()
    r = await tm.submit(rec())
    await eventually(lambda: store.get(r.id).result == "updated: Noch ein Footer")
    assert store.get(r.id).status == "completed"
    await tm.close()


async def test_reopening_respects_parallel_limit(store):
    t3 = FakeT3Client()
    tm, _ = await make(store, t3, max_parallel=1)
    a = await tm.submit(rec(title="A"))
    await eventually(lambda: store.get(a.id).status == "completed")
    t3.gate = asyncio.Event()
    b = await tm.submit(rec(title="B"))
    await eventually(lambda: store.get(b.id).status == "running")
    with pytest.raises(Exception, match="Parallel-Limit"):
        await tm.update(store.get(a.id), "mehr")
    assert t3.sends == []
    t3.gate.set()
    await eventually(lambda: store.get(b.id).status == "completed")
    await tm.update(store.get(a.id), "mehr")
    await eventually(lambda: store.get(a.id).result == "updated: mehr")
    await tm.close()


async def test_recovered_waiting_task_returns_to_running(store):
    class T(FakeT3Client):
        async def wait(self, thread_id, run_id, timeout_ms, environment_id=None):
            await asyncio.sleep(0)
            return {"threadId": thread_id, "runId": "thx:1", "status": "running", "timedOut": True}

    t3 = T()
    old = store.insert(rec())
    t3.threads["thx"] = {"crid": old.id, "run": "thx:1", "waits": 0, "status": "running", "text": ""}
    store.update(old.id, status="waiting_input", thread_id="thx", run_id="thx:1", deadline_at=time.time() + 600)
    tm, _ = await make(store, t3)
    await eventually(lambda: store.get(old.id).status == "running")
    await tm.close()


async def test_timeout_listener_can_reopen_task(store):
    t3 = FakeT3Client(waits_until_done=10_000)
    tm = TaskManager(store, t3, wait_slice_ms=10, task_timeout_s=0)
    reopened = []

    async def listener(r, kind):
        if kind == "timed_out" and not reopened:
            reopened.append(1)
            tm.task_timeout_s = 600
            t3.waits_until_done = 1
            await tm.update(store.get(r.id), "weiter")

    tm.add_listener(listener)
    await tm.start()
    r = await tm.submit(rec())
    await eventually(lambda: store.get(r.id).result == "updated: weiter")
    await tm.close()


@pytest.mark.parametrize("with_run_id", [True, False])
async def test_late_waiting_from_old_run_is_ignored(store, with_run_id):
    class LateT3(FakeT3Client):
        async def wait(self, thread_id, run_id, timeout_ms, environment_id=None):
            if self.gate is not None and not self.gate.is_set():
                old = self.threads[thread_id]["run"]
                await self.gate.wait()
                return {"threadId": thread_id, "runId": old if self.with_run_id else None, "status": "waiting",
                        "timedOut": True}
            return await super().wait(thread_id, run_id, timeout_ms, environment_id)

    t3 = LateT3()
    t3.with_run_id = with_run_id
    t3.gate = asyncio.Event()
    tm, events = await make(store, t3)
    r = await tm.submit(rec())
    await eventually(lambda: store.get(r.id).thread_id is not None)
    await asyncio.sleep(0.02)
    await tm.update(store.get(r.id), "neu")
    t3.gate.set()
    await eventually(lambda: store.get(r.id).status == "completed")
    assert (r.short_id, "waiting_input") not in events
    await tm.close()


async def test_cancel_reopened_task_before_monitor_runs_frees_slot(store):
    t3 = FakeT3Client()
    tm, _ = await make(store, t3, max_parallel=1)
    a = await tm.submit(rec(title="A"))
    await eventually(lambda: store.get(a.id).status == "completed")
    await tm.update(store.get(a.id), "mehr")  # reserves the only slot for the new monitor
    await tm.cancel(store.get(a.id))           # before the monitor got to run
    await asyncio.sleep(0.02)
    b = await tm.submit(rec(title="B"))
    await eventually(lambda: store.get(b.id).status == "completed")
    await tm.close()


async def test_close_waits_for_final_notification(store):
    t3 = FakeT3Client()
    tm = TaskManager(store, t3, wait_slice_ms=10)
    finished = []
    entered = asyncio.Event()

    async def slow_listener(r, kind):
        if kind == "completed":
            entered.set()
            await asyncio.sleep(0.2)
            finished.append(r.id)

    tm.add_listener(slow_listener)
    await tm.start()
    r = await tm.submit(rec())
    await entered.wait()
    await tm.close()
    assert finished == [r.id]


async def test_close_does_not_hang_on_stalled_notification(store):
    t3 = FakeT3Client()
    tm = TaskManager(store, t3, wait_slice_ms=10)
    entered = asyncio.Event()

    async def stuck_listener(r, kind):
        if kind == "completed":
            entered.set()
            await asyncio.Event().wait()  # never returns

    tm.add_listener(stuck_listener)
    await tm.start()
    await tm.submit(rec())
    await entered.wait()
    await asyncio.wait_for(tm.close(), 8)


async def test_cancel_does_not_overwrite_finished_task(store):
    t3 = FakeT3Client()
    tm, events = await make(store, t3)
    r = await tm.submit(rec())
    await eventually(lambda: store.get(r.id).status == "completed")
    out = await tm.cancel(store.get(r.id))
    assert out.status == "completed" and store.get(r.id).status == "completed"
    assert t3.interrupts == [] and (r.short_id, "cancelled") not in events
    await tm.close()


def test_store_migrates_old_schema(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE nyanjou_tasks (id TEXT PRIMARY KEY, short_id INTEGER NOT NULL, guild_id INTEGER, "
                   "channel_id INTEGER, user_id INTEGER NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL, "
                   "prompt TEXT NOT NULL, project_id TEXT, scratch INTEGER NOT NULL DEFAULT 0, thread_id TEXT, "
                   "thread_link TEXT, run_id TEXT, status TEXT NOT NULL, result TEXT, error TEXT, notified INTEGER "
                   "NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL, deadline_at REAL)")
    s = TaskStore(path)
    r = s.insert(rec(environment_id="env-x"))
    assert s.get(r.id).environment_id == "env-x"
    s.close()
