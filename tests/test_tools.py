import json

import pytest

from services.speaker_gate import Attribution
from services.task_manager import TaskManager
from services.task_store import TaskStore
from services.tools import TOOL_DEFINITIONS, ToolContext, ToolDispatcher, looks_risky
from tests.fakes import FakeT3Client

OWNER, ALICE, BOB, GUEST = 1, 2, 3, 4


@pytest.fixture
async def env(tmp_path):
    store = TaskStore(tmp_path / "t.sqlite")
    t3 = FakeT3Client()
    tm = TaskManager(store, t3)  # not started: tasks stay queued (enough for dispatcher tests)

    async def authorized(uid):
        return uid in {OWNER, ALICE, BOB}

    async def owner(uid):
        return uid == OWNER

    d = ToolDispatcher(tm, authorized, owner, default_project_id="p1", list_projects=t3.list_projects)
    yield d, tm, store
    store.close()


def ctx(user=None, reason="single_speaker", guild=10, speakers=None):
    att = Attribution(user, reason if user else (reason if reason != "single_speaker" else "no_speech"),
                      frozenset(speakers or ([user] if user else [])))
    return ToolContext(guild_id=guild, channel_id=5, attribution=att, user_display="tester")


START = {"title": "Minecraft-Webseite", "task": "Erstelle eine Webseite für meinen Minecraft-Server mit Next.js",
         "project": None, "new_project": False, "risk_level": "normal", "risk_reason": None, "confirmation_id": None}


async def call(d, name, args, c):
    return json.loads(await d.dispatch(name, args, c))


async def test_unattributed_and_multi_speaker_denied(env):
    d, tm, store = env
    r = await call(d, "t3_start_task", START, ctx(None, "no_speech"))
    assert r["error"] == "speaker_not_identified"
    r = await call(d, "t3_start_task", START, ctx(None, "multiple_speakers", speakers=[ALICE, GUEST]))
    assert r["error"] == "speaker_not_identified" and "mehrere" in r["message"]
    assert store.list() == []


async def test_guest_denied_even_if_authorized_user_joined(env):
    d, tm, store = env
    r = await call(d, "t3_start_task", START, ctx(GUEST))
    assert r["error"] == "not_authorized"
    assert store.list() == []


async def test_authorized_start_uses_default_project(env):
    d, tm, store = env
    r = await call(d, "t3_start_task", START, ctx(ALICE))
    assert r["ok"] and r["task_id"] == 1
    t = store.list()[0]
    assert t.user_id == ALICE and t.guild_id == 10 and t.project_id == "p1" and not t.scratch
    assert "Next.js" in t.prompt and str(ALICE) in t.prompt


async def test_project_resolution_and_scratch(env):
    d, tm, store = env
    r = await call(d, "t3_start_task", {**START, "project": "minecraft-site"}, ctx(ALICE))
    assert r["ok"] and store.list()[0].project_id == "p2"
    r = await call(d, "t3_start_task", {**START, "project": "minecraft"}, ctx(ALICE))
    assert r["error"] == "ambiguous_project"
    r = await call(d, "t3_start_task", {**START, "project": "gibtsnicht"}, ctx(ALICE))
    assert r["error"] == "unknown_project"
    r = await call(d, "t3_start_task", {**START, "new_project": True}, ctx(ALICE))
    assert r["ok"] and store.list()[0].scratch


async def test_risky_requires_confirmation_from_same_user(env):
    d, tm, store = env
    risky = {**START, "task": "Lösche alle Backups auf dem Produktivserver", "risk_level": "high",
             "risk_reason": "irreversibel"}
    r = await call(d, "t3_start_task", risky, ctx(ALICE))
    assert r["status"] == "confirmation_required"
    cid = r["confirmation_id"]
    assert store.list() == []
    # A different (even authorized) user can't confirm Alice's request.
    r2 = await call(d, "t3_start_task", {**START, "confirmation_id": cid}, ctx(BOB))
    assert r2["error"] == "invalid_confirmation"
    # Alice confirms; the stored (original) task is launched, not whatever args come with the confirmation.
    r3 = await call(d, "t3_start_task", {**START, "confirmation_id": cid}, ctx(ALICE))
    assert r3["ok"]
    assert "Backups" in store.list()[0].prompt
    # Confirmation ids are single-use.
    r4 = await call(d, "t3_start_task", {**START, "confirmation_id": cid}, ctx(ALICE))
    assert r4["error"] == "invalid_confirmation"


async def test_heuristic_catches_unflagged_risk(env):
    d, tm, store = env
    r = await call(d, "t3_start_task", {**START, "task": "Mach einen force push auf main und lösche das Repo"},
                   ctx(ALICE))
    assert r["status"] == "confirmation_required"


async def test_update_cancel_ownership(env):
    d, tm, store = env
    await call(d, "t3_start_task", START, ctx(ALICE))
    # Bob can't touch Alice's task
    r = await call(d, "t3_update_task", {"task_id": 1, "message": "mach es pink"}, ctx(BOB))
    assert r["error"] == "task_not_found"
    r = await call(d, "t3_cancel_task", {"task_id": 1}, ctx(BOB))
    assert r["error"] == "no_active_task"
    # Different guild: not visible
    r = await call(d, "t3_task_status", {"task_id": 1}, ctx(ALICE, guild=99))
    assert r["error"] == "task_not_found"
    # Alice updates her latest task without specifying an id
    r = await call(d, "t3_update_task", {"task_id": None, "message": "Design in Catppuccin Mocha"}, ctx(ALICE))
    assert r["ok"] and "Catppuccin" in store.list()[0].prompt
    # Owner may manage everything
    r = await call(d, "t3_cancel_task", {"task_id": 1}, ctx(OWNER))
    assert r["ok"] and store.list()[0].status == "cancelled"


async def test_list_tasks_only_own(env):
    d, tm, store = env
    await call(d, "t3_start_task", START, ctx(ALICE))
    await call(d, "t3_start_task", START, ctx(BOB))
    r = await call(d, "t3_list_tasks", {"only_active": True}, ctx(ALICE))
    assert len(r["tasks"]) == 1


async def test_t3_not_configured(tmp_path):
    async def yes(uid):
        return True

    d = ToolDispatcher(None, yes, yes)
    r = json.loads(await d.dispatch("t3_start_task", START, ctx(ALICE)))
    assert r["error"] == "t3_not_configured"


def test_tool_schemas_are_strict_compatible():
    for t in TOOL_DEFINITIONS:
        params = t["parameters"]
        assert params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])


@pytest.mark.parametrize("text,risky", [
    ("Baue eine Next.js-App mit Catppuccin Mocha", False),
    ("Fix den Bug im Login", False),
    ("rm -rf / auf dem Server", True),
    ("drop database users", True),
    ("Lösche alle Dateien im Repo", True),
    ("Deploy auf Production", True),
])
def test_risk_heuristic(text, risky):
    assert looks_risky(text) is risky
