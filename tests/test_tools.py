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


def ctx(user=None, reason="single_speaker", guild=10, speakers=None, *, delegation="d1", offset=10_000,
        others=(), session="s1", spoken_since=None, now=None, speech=None):
    """``spoken_since``: user -> ms of speech in any interval. ``speech``: list of (user, t_ms) ticks."""
    att = Attribution(user, reason if user else (reason if reason != "single_speaker" else "no_speech"),
                      frozenset(speakers or ([user] if user else [])), others_recent=frozenset(others),
                      offset_ms=offset)
    spoken = spoken_since or {}

    def between(uid, start, end):
        if speech is not None:
            return sum(20 for u, t in speech if u == uid and start < t <= end)
        return spoken.get(uid, 0)

    return ToolContext(guild_id=guild, channel_id=5, attribution=att, user_display="tester",
                       delegation_id=delegation, live_session_id=session,
                       now_ms=offset + 500 if now is None else now, speech_ms_between=between)


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


async def test_risky_requires_fresh_confirmation_from_same_user(env):
    d, tm, store = env
    risky = {**START, "task": "Lösche alle Backups auf dem Produktivserver", "risk_level": "high",
             "risk_reason": "irreversibel"}
    r = await call(d, "t3_start_task", risky, ctx(ALICE, delegation="d1", offset=10_000))
    assert r["status"] == "confirmation_required"
    cid = r["confirmation_id"]
    assert store.list() == []
    confirm = {**START, "confirmation_id": cid}
    # Backend redeems on the same delegation without new speech -> refused.
    r = await call(d, "t3_start_task", confirm, ctx(ALICE, delegation="d1", offset=10_000, spoken_since={ALICE: 900}))
    assert r["error"] == "confirmation_not_heard"
    # New delegation, but Alice hasn't said anything since the request -> refused.
    r = await call(d, "t3_start_task", confirm, ctx(ALICE, delegation="d2", offset=12_000))
    assert r["error"] == "confirmation_not_heard"
    # A different (even authorized) user can't confirm Alice's request.
    r = await call(d, "t3_start_task", confirm, ctx(BOB, delegation="d2", offset=12_000, spoken_since={BOB: 900}))
    assert r["error"] == "invalid_confirmation"
    # Other guild / other Live session -> invalid.
    r = await call(d, "t3_start_task", confirm, ctx(ALICE, guild=11, delegation="d2", offset=12_000,
                                                    spoken_since={ALICE: 900}))
    assert r["error"] == "invalid_confirmation"
    r = await call(d, "t3_start_task", confirm, ctx(ALICE, session="s2", delegation="d2", offset=12_000,
                                                    spoken_since={ALICE: 900}))
    assert r["error"] == "invalid_confirmation"
    # Alice confirms in a new utterance: the stored (original) task is launched.
    r = await call(d, "t3_start_task", confirm, ctx(ALICE, delegation="d2", offset=12_000, spoken_since={ALICE: 900}))
    assert r["ok"]
    assert "Backups" in store.list()[0].prompt
    # Confirmation ids are single-use.
    r = await call(d, "t3_start_task", confirm, ctx(ALICE, delegation="d3", offset=14_000, spoken_since={ALICE: 900}))
    assert r["error"] == "invalid_confirmation"


async def test_other_speakers_in_context_require_confirmation(env):
    d, tm, store = env
    # A guest talked earlier (their wishes are in the model context): Alice must confirm the assembled task.
    r = await call(d, "t3_start_task", START, ctx(ALICE, others=[GUEST]))
    assert r["status"] == "confirmation_required" and "andere Personen" in r["message"]
    assert store.list() == []
    r = await call(d, "t3_start_task", {**START, "confirmation_id": r["confirmation_id"]},
                   ctx(ALICE, delegation="d2", offset=12_000, spoken_since={ALICE: 500}))
    assert r["ok"]


async def test_risky_or_contested_update_requires_confirmation(env):
    d, tm, store = env
    await call(d, "t3_start_task", START, ctx(ALICE))
    upd = {"task_id": 1, "message": "dann git push -f auf main", "risk_level": "normal", "confirmation_id": None}
    r = await call(d, "t3_update_task", upd, ctx(ALICE))
    assert r["status"] == "confirmation_required"
    r = await call(d, "t3_update_task", {**upd, "message": "Deploy aufräumen", "risk_level": "high"}, ctx(ALICE))
    assert r["status"] == "confirmation_required"
    r = await call(d, "t3_update_task", {**upd, "message": "Footer hinzufügen"}, ctx(ALICE, others=[GUEST]))
    assert r["status"] == "confirmation_required"
    cid = r["confirmation_id"]
    r = await call(d, "t3_update_task", {**upd, "message": "x", "confirmation_id": cid},
                   ctx(ALICE, delegation="d9", offset=20_000, spoken_since={ALICE: 400}))
    assert r["ok"] and "Footer hinzufügen" in store.list()[0].prompt


async def test_heuristic_catches_unflagged_risk(env):
    d, tm, store = env
    r = await call(d, "t3_start_task", {**START, "task": "Mach einen force push auf main und lösche das Repo"},
                   ctx(ALICE))
    assert r["status"] == "confirmation_required"


async def test_update_cancel_ownership(env):
    d, tm, store = env
    await call(d, "t3_start_task", START, ctx(ALICE))
    # Bob can't touch Alice's task
    r = await call(d, "t3_update_task", {"task_id": 1, "message": "mach es pink", "risk_level": "normal",
                                         "confirmation_id": None}, ctx(BOB))
    assert r["error"] == "task_not_found"
    r = await call(d, "t3_cancel_task", {"task_id": 1}, ctx(BOB))
    assert r["error"] == "no_active_task"
    # Different guild: not visible
    r = await call(d, "t3_task_status", {"task_id": 1}, ctx(ALICE, guild=99))
    assert r["error"] == "task_not_found"
    # Alice updates her latest task without specifying an id
    r = await call(d, "t3_update_task", {"task_id": None, "message": "Design in Catppuccin Mocha",
                                         "risk_level": "normal", "confirmation_id": None}, ctx(ALICE))
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
    ("git push -f origin main", True),
])
def test_risk_heuristic(text, risky):
    assert looks_risky(text) is risky


async def test_speech_before_the_question_does_not_confirm(env):
    d, tm, store = env
    risky = {**START, "task": "Lösche alle Backups", "risk_level": "high", "risk_reason": "x"}
    # Delegation at 10 s, tool asks for confirmation at 11 s (now).
    r = await call(d, "t3_start_task", risky, ctx(ALICE, delegation="d1", offset=10_000, now=11_000))
    cid = r["confirmation_id"]
    alice_before = [(ALICE, t) for t in range(9_000, 11_000, 20)]  # all speech precedes the question
    r = await call(d, "t3_start_task", {**START, "confirmation_id": cid},
                   ctx(ALICE, delegation="d2", offset=14_000, speech=alice_before))
    assert r["error"] == "confirmation_not_heard"
    # Speech after the question but after the confirming delegation doesn't count either.
    r = await call(d, "t3_start_task", {**START, "confirmation_id": cid},
                   ctx(ALICE, delegation="d2", offset=14_000, speech=[(ALICE, t) for t in range(14_100, 15_000, 20)]))
    assert r["error"] == "confirmation_not_heard"
    alice_after = [(ALICE, t) for t in range(12_000, 12_600, 20)]
    r = await call(d, "t3_start_task", {**START, "confirmation_id": cid},
                   ctx(ALICE, delegation="d2", offset=14_000, speech=alice_after))
    assert r["ok"]


async def test_update_confirmation_is_bound_to_resolved_task(env):
    d, tm, store = env
    await call(d, "t3_start_task", START, ctx(ALICE))  # task #1
    upd = {"task_id": None, "message": "git push -f", "risk_level": "normal", "confirmation_id": None}
    r = await call(d, "t3_update_task", upd, ctx(ALICE))
    cid = r["confirmation_id"]
    await call(d, "t3_start_task", {**START, "title": "Neu"}, ctx(ALICE))  # task #2 is now the latest
    r = await call(d, "t3_update_task", {**upd, "confirmation_id": cid},
                   ctx(ALICE, delegation="d2", offset=20_000, spoken_since={ALICE: 400}))
    assert r["ok"] and r["task_id"] == 1
    assert "git push -f" in store.get_by_short_id(1).prompt
    assert "git push -f" not in store.get_by_short_id(2).prompt
