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
