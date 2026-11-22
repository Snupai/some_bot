"""Function tools for the GPT-Live Responses backend, plus their security checks.

Every tool call is attributed to a Discord user by ``SpeakerTimeline``, using
audio provenance and never conversation content. Executing tools run only if:

1. exactly one identifiable Discord user spoke right before the delegation,
2. that user is authorized (bot owner or in ``allowed_users.sqlite``), and
3. for existing tasks, the task belongs to that user in the same guild (the
   owner may manage all tasks).

High-risk work (irreversible deletion, production changes, publishing private
data, security-critical actions) also needs a spoken confirmation from the
same user. The first call returns a ``confirmation_id``; only a later call
quoting that ID, attributed to the same user within the TTL, launches it.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .speaker_gate import Attribution
from .t3_oauth import T3AuthRequired
from .task_manager import TaskManager
from .task_store import TaskRecord

log = logging.getLogger("bot.py.nyanjou.tools")

CONFIRMATION_TTL_S = 180
CONFIRMATION_MIN_SPEECH_MS = 300

_RISK_PATTERNS = [
    r"\brm\s+-rf\b", r"\bdrop\s+(database|table|schema)\b", r"\btruncate\b",
    r"force[- ]?push", r"push\s+(--force|-f\b)", r"\bgit\s+reset\s+--hard\b",
    r"\b(prod|production|produktiv\w*|live-?system)\b",
    r"(alle|sämtliche|komplett)\w*\s+.{0,40}(lösch|entfern|vernicht)",
    r"(lösch|entfern)\w*\s+.{0,40}(repo|repository|datenbank|backup|server|alle)",
    r"\b(delete|wipe|destroy)\b.{0,40}\b(all|repo|repository|database|backup|server)\b",
    r"\b(secret|passw(or)?d|private[ -]?key|api[- ]?key|token)s?\b.{0,40}\b(veröffentlich|publish|post|leak|teil)",
    r"\b(firewall|sudo|chmod\s+777|ssh[- ]?key|iptables)\b",
]
_RISK_RE = re.compile("|".join(_RISK_PATTERNS), re.IGNORECASE)


def looks_risky(text: str) -> bool:
    return bool(_RISK_RE.search(text or ""))


def _nullable(t: str, desc: str) -> dict:
    return {"type": [t, "null"], "description": desc}


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "t3_start_task",
        "description": (
            "Startet eine neue Entwicklungsaufgabe in T3 Code (Coding-Agent mit Zugriff auf Projekte/Dateien/Terminal). "
            "Verwenden für alles, was echte Arbeit an Code, Dateien, Repositories, Builds, Tests, Dependencies oder "
            "Entwicklungsumgebungen erfordert. Nicht verwenden für reine Wissensfragen."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "task", "project", "environment", "new_project", "risk_level", "risk_reason",
                         "confirmation_id"],
            "properties": {
                "title": {"type": "string", "description": "Kurzer Titel (max. 8 Wörter)."},
                "task": {"type": "string", "description": (
                    "Vollständiger, eigenständiger Arbeitsauftrag für den Coding-Agenten inkl. allem relevanten "
                    "Kontext aus dem Gespräch (Ziel, Technologien, Design, Einschränkungen, Akzeptanzkriterien).")},
                "project": _nullable("string", "Name oder ID eines bestehenden T3-Projekts, falls der Nutzer eins nennt; sonst null (Standardprojekt)."),
                "environment": _nullable("string", "Name/ID einer verlinkten T3-Umgebung (anderer Rechner), falls genannt; sonst null (dieses System)."),
                "new_project": {"type": "boolean", "description": "true, wenn eine komplett neue, eigenständige App/ein neues Projekt angelegt werden soll."},
                "risk_level": {"type": "string", "enum": ["normal", "high"], "description": (
                    "high nur bei irreversiblen/gefährlichen Aktionen: unwiderrufliches Löschen wichtiger Daten, "
                    "Veröffentlichen privater Informationen, riskante Änderungen an Produktivsystemen, sicherheitskritische Aktionen.")},
                "risk_reason": _nullable("string", "Begründung bei risk_level=high, sonst null."),
                "confirmation_id": _nullable("string", "Nur nach ausdrücklicher Bestätigung des Nutzers: die zuvor erhaltene confirmation_id; sonst null."),
            },
        },
    },
    {
        "type": "function",
        "name": "t3_update_task",
        "description": (
            "Gibt eine zusätzliche Anweisung oder Änderung an eine bestehende T3-Code-Aufgabe weiter "
            "(z. B. 'mach das Design in Catppuccin Mocha'). Ohne task_id wird die zuletzt aktive Aufgabe des Sprechers verwendet."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task_id", "message", "risk_level", "confirmation_id"],
            "properties": {
                "task_id": _nullable("integer", "Aufgabennummer, falls bekannt; sonst null."),
                "message": {"type": "string", "description": "Die neue Anweisung, vollständig und eigenständig formuliert."},
                "risk_level": {"type": "string", "enum": ["normal", "high"],
                               "description": "high bei irreversiblen/gefährlichen Änderungen (wie bei t3_start_task)."},
                "confirmation_id": _nullable("string", "Nur nach ausdrücklicher Bestätigung: die zuvor erhaltene confirmation_id; sonst null."),
            },
        },
    },
    {
        "type": "function",
        "name": "t3_list_tasks",
        "description": "Listet die Aufgaben des aktuellen Sprechers auf diesem Server mit Status.",
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False, "required": ["only_active"],
                       "properties": {"only_active": {"type": "boolean", "description": "Nur laufende Aufgaben."}}},
    },
    {
        "type": "function",
        "name": "t3_task_status",
        "description": "Status und ggf. Ergebnis einer Aufgabe abfragen.",
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False, "required": ["task_id"],
                       "properties": {"task_id": _nullable("integer", "Aufgabennummer; null = zuletzt aktive Aufgabe.")}},
    },
    {
        "type": "function",
        "name": "t3_cancel_task",
        "description": "Bricht eine laufende T3-Code-Aufgabe ab.",
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False, "required": ["task_id"],
                       "properties": {"task_id": _nullable("integer", "Aufgabennummer; null = zuletzt aktive Aufgabe.")}},
    },
    {
        "type": "function",
        "name": "t3_list_projects",
        "description": "Listet verfügbare T3-Code-Projekte (Name und ID).",
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {}},
    },
    {
        "type": "function",
        "name": "t3_list_environments",
        "description": "Listet die T3-Code-Umgebungen (dieses System und verlinkte Rechner).",
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {}},
    },
    {
        "type": "function",
        "name": "t3_list_threads",
        "description": (
            "Listet T3-Code-Threads des Nutzers (auch solche, die nicht von Nyanjou gestartet wurden), neueste zuerst. "
            "Für Fragen wie 'Welcher Thread ist zuletzt fertig geworden?' oder 'Was läuft gerade in T3?'."
        ),
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False,
                       "required": ["query", "only_active", "environment"],
                       "properties": {
                           "query": _nullable("string", "Suchbegriff (Titel/Inhalt), sonst null."),
                           "only_active": {"type": "boolean", "description": "Nur laufende/wartende Threads."},
                           "environment": _nullable("string", "Umgebung (Name/ID), sonst null."),
                       }},
    },
    {
        "type": "function",
        "name": "t3_thread_info",
        "description": "Status und letzte Antwort eines bestimmten T3-Threads (per Titel oder ID).",
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False, "required": ["thread", "environment"],
                       "properties": {
                           "thread": {"type": "string", "description": "Thread-Titel (oder Teil davon) oder Thread-ID."},
                           "environment": _nullable("string", "Umgebung (Name/ID), sonst null."),
                       }},
    },
    {
        "type": "function",
        "name": "t3_message_thread",
        "description": (
            "Schickt eine Anweisung an einen BESTEHENDEN T3-Thread (per Titel oder ID), z. B. 'sag dem Thread X, er soll "
            "die Tests reparieren'. Nyanjou verfolgt danach den Fortschritt und meldet das Ergebnis."
        ),
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False,
                       "required": ["thread", "environment", "message", "risk_level", "confirmation_id"],
                       "properties": {
                           "thread": {"type": "string", "description": "Thread-Titel (oder Teil davon) oder Thread-ID."},
                           "environment": _nullable("string", "Umgebung (Name/ID), sonst null."),
                           "message": {"type": "string", "description": "Die Anweisung, vollständig formuliert."},
                           "risk_level": {"type": "string", "enum": ["normal", "high"],
                                          "description": "high bei irreversiblen/gefährlichen Anweisungen."},
                           "confirmation_id": _nullable("string", "Nur nach ausdrücklicher Bestätigung: die erhaltene confirmation_id; sonst null."),
                       }},
    },
]

TOOL_NAMES = {t["name"] for t in TOOL_DEFINITIONS}


@dataclass
class PendingConfirmation:
    tool: str
    user_id: int
    guild_id: int
    live_session_id: str | None
    delegation_id: str | None
    issued_at_ms: int | None  # Live session clock when the confirmation was requested
    args: dict[str, Any]
    created_at: float = field(default_factory=time.time)


@dataclass
class ToolContext:
    guild_id: int
    channel_id: int | None
    attribution: Attribution
    user_display: str | None = None
    delegation_id: str | None = None
    live_session_id: str | None = None
    now_ms: int | None = None  # Live session clock when the tool runs
    # (user_id, start_ms, end_ms) -> ms of forwarded speech by that user in (start, end]
    speech_ms_between: Callable[[int, int, int], int] | None = None
    # t_ms -> first Live-clock time after t_ms at which Nyanjou finished speaking, or None
    playback_idle_after: Callable[[int], int | None] | None = None


def _out(**kw: Any) -> str:
    return json.dumps(kw, ensure_ascii=False)


class ToolDispatcher:
    def __init__(
        self,
        tasks: TaskManager | None,
        is_authorized: Callable[[int], Awaitable[bool]],
        is_owner: Callable[[int], Awaitable[bool]],
        *,
        default_project_id: str = "",
        allow_scratch: bool = True,
        list_projects: Callable[[], Awaitable[list[dict]]] | None = None,
        t3: Any = None,
    ) -> None:
        self.tasks = tasks
        self.is_authorized = is_authorized
        self.is_owner = is_owner
        self.default_project_id = default_project_id
        self.allow_scratch = allow_scratch
        self._list_projects = list_projects
        self.t3 = t3
        self._pending: dict[str, PendingConfirmation] = {}

    async def dispatch(self, name: str, args: dict[str, Any], ctx: ToolContext) -> str:
        if name not in TOOL_NAMES:
            return _out(ok=False, error="unknown_tool")
        att = ctx.attribution
        if not att.ok:
            log.info("tool %s denied: attribution failed (%s, speakers=%s)", name, att.reason, sorted(att.speakers))
            msg = {
                "multiple_speakers": "Es haben gerade mehrere Personen gesprochen. Aus Sicherheitsgründen führe ich nur "
                                     "Aufträge aus, die eindeutig einer berechtigten Person zugeordnet werden können. "
                                     "Bitte die berechtigte Person den Auftrag noch einmal allein sagen lassen.",
                "no_speech": "Ich konnte den Auftrag keiner sprechenden Person zuordnen; deshalb wird nichts ausgeführt.",
            }.get(att.reason, "Sprecher konnte nicht eindeutig bestimmt werden; es wird nichts ausgeführt.")
            return _out(ok=False, error="speaker_not_identified", message=msg)
        user_id = att.user_id
        assert user_id is not None
        if not await self.is_authorized(user_id):
            log.info("tool %s denied: user %s not authorized", name, user_id)
            return _out(ok=False, error="not_authorized",
                        message="Diese Person ist nicht für ausführende Aufgaben freigeschaltet. "
                                "Normal unterhalten ist okay, aber Aufgaben werden nicht gestartet.")
        if self.tasks is None:
            return _out(ok=False, error="t3_not_configured",
                        message="T3 Code ist auf diesem Bot nicht eingerichtet.")
        try:
            handler = getattr(self, f"_tool_{name}")
            return await handler(args, ctx, user_id)
        except T3AuthRequired:
            return _out(ok=False, error="t3_auth_required",
                        message="Die Anmeldung bei T3 Code ist abgelaufen. Der Bot-Admin muss sich neu anmelden.")
        except Exception as exc:
            log.exception("tool %s failed", name)
            return _out(ok=False, error="tool_failed", message=f"{type(exc).__name__}: {str(exc)[:300]}")

    # ------------------------------------------------------------------ helpers
    async def _owned(self, task_id: int | None, ctx: ToolContext, user_id: int) -> TaskRecord | None:
        assert self.tasks is not None
        if task_id is None:
            return self.tasks.latest_active_for(ctx.guild_id, user_id)
        rec = self.tasks.get(int(task_id))
        if rec is None or rec.guild_id != ctx.guild_id:
            return None
        if rec.user_id != user_id and not await self.is_owner(user_id):
            return None
        return rec

    async def resolve_project(self, project: str | None) -> tuple[str | None, str | None]:
        """Returns (project_id, error)."""
        if not project:
            return (self.default_project_id or None), (None if self.default_project_id else "no_default_project")
        if self._list_projects is None:
            return project, None
        projects = await self._list_projects()
        p = project.strip().lower()
        for pr in projects:
            if pr.get("id") == project:
                return pr["id"], None
        matches = [pr for pr in projects if p == str(pr.get("title", "")).lower()]
        if not matches:
            matches = [pr for pr in projects if p in str(pr.get("title", "")).lower()]
        if len(matches) == 1:
            return matches[0]["id"], None
        if len(matches) > 1:
            return None, "ambiguous_project:" + ", ".join(m.get("title", "?") for m in matches[:5])
        return None, "unknown_project"

    def _prune(self) -> None:
        now = time.time()
        for cid in [c for c, p in self._pending.items() if now - p.created_at > CONFIRMATION_TTL_S]:
            del self._pending[cid]

    def _ask_confirmation(self, tool: str, args: dict, ctx: ToolContext, user_id: int, reason: str) -> str:
        # At most one open confirmation per speaker and guild. A new question replaces older ones, so a
        # spoken "yes" can only ever redeem the request that was asked last.
        for old in [c for c, pend in self._pending.items() if pend.user_id == user_id and pend.guild_id == ctx.guild_id]:
            del self._pending[old]
        cid = secrets.token_hex(4)
        self._pending[cid] = PendingConfirmation(
            tool=tool, user_id=user_id, guild_id=ctx.guild_id, live_session_id=ctx.live_session_id,
            delegation_id=ctx.delegation_id, issued_at_ms=ctx.now_ms, args=dict(args),
        )
        return _out(ok=False, status="confirmation_required", confirmation_id=cid, message=(
            f"{reason} Fasse den Auftrag kurz zusammen und frage ausdrücklich nach Bestätigung. Nur wenn dieselbe "
            f"Person danach klar zustimmt, rufe {tool} erneut mit dieser confirmation_id auf."))

    def _redeem(self, conf_id: str, tool: str, ctx: ToolContext, user_id: int) -> dict | str:
        """Return the stored args, or an error JSON string.

        A confirmation is only valid in the same guild and Live session, from the same speaker, from a
        *new* delegation, and only if that speaker audibly spoke after Nyanjou finished playing the
        question and before that delegation. The backend can't redeem its own confirmation; silence
        and speech from before the question don't count.
        """
        self._prune()
        pending = self._pending.get(conf_id)
        invalid = _out(ok=False, error="invalid_confirmation",
                       message="Diese Bestätigung ist ungültig oder abgelaufen; bitte den Auftrag neu formulieren.")
        if (pending is None or pending.tool != tool or pending.user_id != user_id
                or pending.guild_id != ctx.guild_id or pending.live_session_id != ctx.live_session_id):
            return invalid
        asked, answered = pending.issued_at_ms, ctx.attribution.offset_ms
        # Count only audible speech after the question has actually been played to the channel.
        played = ctx.playback_idle_after(asked) if (ctx.playback_idle_after and asked is not None) else None
        fresh = (
            ctx.delegation_id is not None and ctx.delegation_id != pending.delegation_id
            and played is not None and answered is not None and answered > played
            and ctx.speech_ms_between is not None
            and ctx.speech_ms_between(user_id, played, answered) >= CONFIRMATION_MIN_SPEECH_MS
        )
        if not fresh:
            return _out(ok=False, error="confirmation_not_heard",
                        message="Ich habe noch keine Bestätigung dieser Person gehört. Bitte frage sie und warte auf ihre Antwort.")
        del self._pending[conf_id]
        return pending.args

    def _others_note(self, ctx: ToolContext) -> str | None:
        if ctx.attribution.others_recent:
            return ("Vor diesem Auftrag haben auch andere Personen gesprochen; ihre Wünsche sind im Gesprächskontext. "
                    "Darum muss die berechtigte Person den zusammengefassten Auftrag selbst bestätigen.")
        return None

    # ------------------------------------------------------------------ tools
    async def _tool_t3_start_task(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        assert self.tasks is not None
        self._prune()
        title = (args.get("title") or "Nyanjou-Aufgabe").strip()[:120]
        task_text = (args.get("task") or "").strip()
        if len(task_text) < 10:
            return _out(ok=False, error="task_too_vague", message="Der Auftrag ist noch zu ungenau. Frag nach Details.")

        conf_id = args.get("confirmation_id")
        if conf_id:
            stored = self._redeem(conf_id, "t3_start_task", ctx, user_id)
            if isinstance(stored, str):
                return stored
            # Launch exactly what was confirmed, not whatever accompanies the confirmation.
            args = stored
            task_text, title = stored["task"], stored["title"]
        else:
            reason = None
            if args.get("risk_level") == "high" or looks_risky(task_text) or looks_risky(title):
                reason = ("Das ist eine riskante oder irreversible Aktion"
                          + (f" ({args.get('risk_reason')})" if args.get("risk_reason") else "") + ".")
            reason = reason or self._others_note(ctx)
            if reason:
                return self._ask_confirmation("t3_start_task", {**args, "task": task_text, "title": title},
                                              ctx, user_id, reason)

        scratch = bool(args.get("new_project")) and self.allow_scratch and not args.get("project")
        project_id = None
        if not scratch:
            project_id, err = await self.resolve_project(args.get("project"))
            if err == "no_default_project":
                if self.allow_scratch:
                    scratch = True
                else:
                    return _out(ok=False, error="no_project", message="Es ist kein Standardprojekt konfiguriert.")
            elif err:
                return _out(ok=False, error=err.split(":")[0], message=(
                    "Mehrere passende Projekte: " + err.split(":", 1)[1] if err.startswith("ambiguous")
                    else "Dieses Projekt kenne ich nicht. Frag nach oder nutze t3_list_projects."))

        env, env_err = await self.resolve_environment(args.get("environment"))
        if env_err:
            return _out(ok=False, error=env_err, message="Diese T3-Umgebung kenne ich nicht. Nutze t3_list_environments.")
        prompt = (
            f"{task_text}\n\n---\n"
            f"Auftrag per Discord-Sprachassistent Nyanjou von Discord-Nutzer {ctx.user_display or user_id} "
            f"(ID {user_id}). Arbeite eigenständig; normale Entwicklungsschritte brauchen keine Rückfrage. "
            "Frage nur bei irreversiblen oder sicherheitskritischen Schritten nach. "
            "Fasse am Ende kurz zusammen, was erledigt wurde, wo die Ergebnisse liegen und wie man sie startet."
        )
        rec = TaskRecord(user_id=user_id, title=title, prompt=prompt, source="voice", environment_id=env,
                         guild_id=ctx.guild_id, channel_id=ctx.channel_id, project_id=project_id, scratch=scratch)
        rec = await self.tasks.submit(rec)
        return _out(ok=True, task_id=rec.short_id, status=rec.status,
                    message=f"Aufgabe #{rec.short_id} an T3 Code übergeben. Läuft im Hintergrund; du wirst informiert, "
                            "wenn sie fertig ist. Das Gespräch kann normal weitergehen.")

    async def _tool_t3_update_task(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        assert self.tasks is not None
        message = (args.get("message") or "").strip()
        conf_id = args.get("confirmation_id")
        if conf_id:
            stored = self._redeem(conf_id, "t3_update_task", ctx, user_id)
            if isinstance(stored, str):
                return stored
            args = stored  # contains the concrete task_id resolved when the confirmation was requested
            message = stored["message"]
        if not message:
            return _out(ok=False, error="empty_message")
        rec = await self._owned(args.get("task_id"), ctx, user_id)
        if rec is None:
            return _out(ok=False, error="task_not_found", message="Keine passende Aufgabe dieser Person gefunden.")
        if not conf_id:
            reason = None
            if args.get("risk_level") == "high" or looks_risky(message):
                reason = "Diese Änderung ist riskant oder irreversibel."
            reason = reason or self._others_note(ctx)
            if reason:
                return self._ask_confirmation("t3_update_task", {**args, "message": message, "task_id": rec.short_id},
                                              ctx, user_id, reason)
        if rec.status == "cancelled":
            return _out(ok=False, error="task_cancelled", message=f"Aufgabe #{rec.short_id} wurde abgebrochen.")
        rec = await self.tasks.update(rec, message)
        return _out(ok=True, task_id=rec.short_id, status=rec.status,
                    message=f"Änderung an Aufgabe #{rec.short_id} weitergegeben.")

    async def _tool_t3_list_tasks(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        assert self.tasks is not None
        from .task_store import ACTIVE_STATUSES

        statuses = ACTIVE_STATUSES if args.get("only_active") else None
        recs = self.tasks.list(guild_id=ctx.guild_id, user_id=user_id, statuses=statuses, limit=10)
        return _out(ok=True, tasks=[{k: v for k, v in r.public().items() if k != "result"} for r in recs])

    async def _tool_t3_task_status(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        rec = await self._owned(args.get("task_id"), ctx, user_id)
        if rec is None:
            return _out(ok=False, error="task_not_found")
        return _out(ok=True, task=rec.public())

    async def _tool_t3_cancel_task(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        assert self.tasks is not None
        rec = await self._owned(args.get("task_id"), ctx, user_id)
        if rec is None or not rec.active:
            return _out(ok=False, error="no_active_task", message="Keine laufende Aufgabe gefunden.")
        rec = await self.tasks.cancel(rec, reason=f"Per Sprache abgebrochen von {user_id}")
        return _out(ok=True, task_id=rec.short_id, status=rec.status)

    async def _tool_t3_list_projects(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        if self._list_projects is None:
            return _out(ok=False, error="unavailable")
        projects = await self._list_projects()
        return _out(ok=True, default_project_id=self.default_project_id or None,
                    projects=[{"id": p.get("id"), "title": p.get("title")} for p in projects[:30]])


    # ------------------------------------------------------------------ T3 threads & environments
    async def resolve_environment(self, ref: str | None) -> tuple[str | None, str | None]:
        """Returns (environment_id or None for this system, error)."""
        if not ref or self.t3 is None:
            return None, None
        envs = await self.t3.list_environments()
        r = ref.strip().lower()
        for e in envs:
            if e["id"] == ref:
                return (None if e.get("current") else e["id"]), None
        matches = [e for e in envs if r in str(e.get("label") or "").lower()]
        if len(matches) == 1:
            return (None if matches[0].get("current") else matches[0]["id"]), None
        if any(w in r for w in ("hier", "dies", "lokal", "this", "local")):
            return None, None
        return None, "unknown_environment" if not matches else "ambiguous_environment"

    async def resolve_thread(self, ref: str, environment_id: str | None) -> tuple[dict | None, str | None]:
        assert self.t3 is not None
        threads = await self.t3.list_threads(query=ref, environment_id=environment_id, limit=10)
        exact = [t for t in threads if t["thread_id"] == ref or str(t.get("title") or "").lower() == ref.lower()]
        if len(exact) == 1:
            return exact[0], None
        if len(threads) == 1:
            return threads[0], None
        if not threads:
            return None, "thread_not_found"
        return None, "ambiguous_thread:" + "; ".join(str(t.get("title")) for t in threads[:5])

    async def _tool_t3_list_environments(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        if self.t3 is None:
            return _out(ok=False, error="unavailable")
        return _out(ok=True, environments=await self.t3.list_environments())

    async def _tool_t3_list_threads(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        if self.t3 is None:
            return _out(ok=False, error="unavailable")
        env, err = await self.resolve_environment(args.get("environment"))
        if err:
            return _out(ok=False, error=err)
        threads = await self.t3.list_threads(query=args.get("query"), only_active=bool(args.get("only_active")),
                                             environment_id=env, limit=10)
        return _out(ok=True, threads=threads)

    async def _tool_t3_thread_info(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        if self.t3 is None:
            return _out(ok=False, error="unavailable")
        env, err = await self.resolve_environment(args.get("environment"))
        if err:
            return _out(ok=False, error=err)
        thread, err = await self.resolve_thread(args.get("thread") or "", env)
        if err:
            return _out(ok=False, error=err.split(":")[0], message=err.split(":", 1)[-1] if ":" in err else None)
        reply = await self.t3.read_latest_reply(thread["thread_id"], max_chars=1500, environment_id=env)
        return _out(ok=True, thread={**thread, "status": reply.get("status") or thread.get("status"),
                                     "last_reply": (reply.get("text") or "")[:1500]})

    async def _tool_t3_message_thread(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        assert self.tasks is not None
        if self.t3 is None:
            return _out(ok=False, error="unavailable")
        message = (args.get("message") or "").strip()
        conf_id = args.get("confirmation_id")
        if conf_id:
            stored = self._redeem(conf_id, "t3_message_thread", ctx, user_id)
            if isinstance(stored, str):
                return stored
            args, message = stored, stored["message"]
        if not message:
            return _out(ok=False, error="empty_message")
        if conf_id:
            env, thread = args.get("_environment_id"), {"thread_id": args["_thread_id"], "title": args.get("_title")}
        else:
            env, err = await self.resolve_environment(args.get("environment"))
            if err:
                return _out(ok=False, error=err)
            thread, err = await self.resolve_thread(args.get("thread") or "", env)
            if err:
                return _out(ok=False, error=err.split(":")[0], message=err.split(":", 1)[-1] if ":" in err else None)
            reason = None
            if args.get("risk_level") == "high" or looks_risky(message):
                reason = "Diese Anweisung ist riskant oder irreversibel."
            reason = reason or self._others_note(ctx)
            if reason:
                # Bind the confirmation to the concrete thread resolved now.
                return self._ask_confirmation("t3_message_thread", {
                    **args, "message": message, "_thread_id": thread["thread_id"], "_environment_id": env,
                    "_title": thread.get("title")}, ctx, user_id, reason)
        rec = await self.tasks.adopt(thread_id=thread["thread_id"], environment_id=env,
                                     title=str(thread.get("title") or "T3-Thread"), message=message, user_id=user_id,
                                     guild_id=ctx.guild_id, channel_id=ctx.channel_id)
        return _out(ok=True, task_id=rec.short_id, status=rec.status,
                    message=f"An '{thread.get('title')}' weitergegeben; ich melde mich, wenn der Thread fertig ist.")
