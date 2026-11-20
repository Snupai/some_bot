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

_RISK_PATTERNS = [
    r"\brm\s+-rf\b", r"\bdrop\s+(database|table|schema)\b", r"\btruncate\b",
    r"force[- ]?push", r"push\s+--force", r"\bgit\s+reset\s+--hard\b",
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
            "required": ["title", "task", "project", "new_project", "risk_level", "risk_reason", "confirmation_id"],
            "properties": {
                "title": {"type": "string", "description": "Kurzer Titel (max. 8 Wörter)."},
                "task": {"type": "string", "description": (
                    "Vollständiger, eigenständiger Arbeitsauftrag für den Coding-Agenten inkl. allem relevanten "
                    "Kontext aus dem Gespräch (Ziel, Technologien, Design, Einschränkungen, Akzeptanzkriterien).")},
                "project": _nullable("string", "Name oder ID eines bestehenden T3-Projekts, falls der Nutzer eins nennt; sonst null (Standardprojekt)."),
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
            "required": ["task_id", "message"],
            "properties": {
                "task_id": _nullable("integer", "Aufgabennummer, falls bekannt; sonst null."),
                "message": {"type": "string", "description": "Die neue Anweisung, vollständig und eigenständig formuliert."},
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
]

TOOL_NAMES = {t["name"] for t in TOOL_DEFINITIONS}


@dataclass
class PendingConfirmation:
    user_id: int
    args: dict[str, Any]
    created_at: float = field(default_factory=time.time)


@dataclass
class ToolContext:
    guild_id: int
    channel_id: int | None
    attribution: Attribution
    user_display: str | None = None


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
    ) -> None:
        self.tasks = tasks
        self.is_authorized = is_authorized
        self.is_owner = is_owner
        self.default_project_id = default_project_id
        self.allow_scratch = allow_scratch
        self._list_projects = list_projects
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
            pending = self._pending.get(conf_id)
            if pending is None or pending.user_id != user_id:
                return _out(ok=False, error="invalid_confirmation",
                            message="Diese Bestätigung ist ungültig oder abgelaufen; bitte den Auftrag neu formulieren.")
            del self._pending[conf_id]
            args = {**pending.args, "confirmation_id": conf_id}
            task_text = pending.args["task"]
            title = pending.args["title"]
        else:
            risky = args.get("risk_level") == "high" or looks_risky(task_text) or looks_risky(title)
            if risky:
                cid = secrets.token_hex(4)
                self._pending[cid] = PendingConfirmation(user_id, {**args, "task": task_text, "title": title})
                return _out(
                    ok=False, status="confirmation_required", confirmation_id=cid,
                    message=("Das ist eine riskante oder irreversible Aktion"
                             + (f" ({args.get('risk_reason')})" if args.get("risk_reason") else "")
                             + ". Fasse kurz zusammen, was passieren würde, und frage ausdrücklich nach Bestätigung. "
                               "Nur wenn dieselbe Person klar zustimmt, rufe t3_start_task erneut mit dieser confirmation_id auf."),
                )

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

        prompt = (
            f"{task_text}\n\n---\n"
            f"Auftrag per Discord-Sprachassistent Nyanjou von Discord-Nutzer {ctx.user_display or user_id} "
            f"(ID {user_id}). Arbeite eigenständig; normale Entwicklungsschritte brauchen keine Rückfrage. "
            "Frage nur bei irreversiblen oder sicherheitskritischen Schritten nach. "
            "Fasse am Ende kurz zusammen, was erledigt wurde, wo die Ergebnisse liegen und wie man sie startet."
        )
        rec = TaskRecord(user_id=user_id, title=title, prompt=prompt, source="voice",
                         guild_id=ctx.guild_id, channel_id=ctx.channel_id, project_id=project_id, scratch=scratch)
        rec = await self.tasks.submit(rec)
        return _out(ok=True, task_id=rec.short_id, status=rec.status,
                    message=f"Aufgabe #{rec.short_id} an T3 Code übergeben. Läuft im Hintergrund; du wirst informiert, "
                            "wenn sie fertig ist. Das Gespräch kann normal weitergehen.")

    async def _tool_t3_update_task(self, args: dict, ctx: ToolContext, user_id: int) -> str:
        assert self.tasks is not None
        message = (args.get("message") or "").strip()
        if not message:
            return _out(ok=False, error="empty_message")
        if looks_risky(message):
            return _out(ok=False, error="needs_new_task",
                        message="Diese Änderung wirkt riskant. Starte sie als neue Aufgabe mit t3_start_task, damit sie bestätigt werden kann.")
        rec = await self._owned(args.get("task_id"), ctx, user_id)
        if rec is None:
            return _out(ok=False, error="task_not_found", message="Keine passende Aufgabe dieser Person gefunden.")
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
