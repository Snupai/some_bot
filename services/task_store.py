"""SQLite persistence for Nyanjou tasks (one row per T3 Code thread hand-off)."""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ACTIVE_STATUSES = ("queued", "starting", "running", "waiting_input")
FINAL_STATUSES = ("completed", "failed", "cancelled", "timed_out")

SCHEMA = """
CREATE TABLE IF NOT EXISTS nyanjou_tasks (
    id TEXT PRIMARY KEY,
    short_id INTEGER NOT NULL,
    guild_id INTEGER,
    channel_id INTEGER,
    user_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    project_id TEXT,
    scratch INTEGER NOT NULL DEFAULT 0,
    thread_id TEXT,
    thread_link TEXT,
    run_id TEXT,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deadline_at REAL
);
CREATE INDEX IF NOT EXISTS idx_nyanjou_tasks_status ON nyanjou_tasks(status);
CREATE INDEX IF NOT EXISTS idx_nyanjou_tasks_user ON nyanjou_tasks(guild_id, user_id);
CREATE TABLE IF NOT EXISTS nyanjou_task_updates (
    task_id TEXT NOT NULL,
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
"""


@dataclass
class TaskRecord:
    user_id: int
    title: str
    prompt: str
    source: str  # "voice" | "slash"
    guild_id: int | None = None
    channel_id: int | None = None
    project_id: str | None = None
    scratch: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    short_id: int = 0
    thread_id: str | None = None
    thread_link: str | None = None
    run_id: str | None = None
    status: str = "queued"
    result: str | None = None
    error: str | None = None
    notified: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    deadline_at: float | None = None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def public(self) -> dict[str, Any]:
        """Summary safe to hand to the voice model / show in Discord."""
        out = {
            "task_id": self.short_id,
            "title": self.title,
            "status": self.status,
            "project": "neues Scratch-Projekt" if self.scratch else self.project_id,
            "age_minutes": int((time.time() - self.created_at) / 60),
        }
        if self.result:
            out["result"] = self.result[:1500]
        if self.error:
            out["error"] = self.error[:500]
        return out


class TaskStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _row(self, row: sqlite3.Row | None) -> TaskRecord | None:
        if row is None:
            return None
        d = dict(row)
        d["scratch"] = bool(d["scratch"])
        d["notified"] = bool(d["notified"])
        return TaskRecord(**d)

    def insert(self, rec: TaskRecord) -> TaskRecord:
        with self._lock:
            cur = self._db.execute("SELECT COALESCE(MAX(short_id), 0) + 1 FROM nyanjou_tasks")
            rec.short_id = int(cur.fetchone()[0])
            d = asdict(rec)
            cols = ",".join(d)
            self._db.execute(
                f"INSERT INTO nyanjou_tasks ({cols}) VALUES ({','.join('?' * len(d))})",
                [int(v) if isinstance(v, bool) else v for v in d.values()],
            )
            self._db.commit()
        return rec

    def update(self, task_id: str, **fields: Any) -> TaskRecord | None:
        fields["updated_at"] = time.time()
        sets = ",".join(f"{k} = ?" for k in fields)
        vals = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        with self._lock:
            self._db.execute(f"UPDATE nyanjou_tasks SET {sets} WHERE id = ?", [*vals, task_id])
            self._db.commit()
        return self.get(task_id)

    def add_update(self, task_id: str, kind: str, text: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO nyanjou_task_updates (task_id, at, kind, text) VALUES (?, ?, ?, ?)",
                (task_id, time.time(), kind, text[:4000]),
            )
            self._db.commit()

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM nyanjou_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row(row)

    def get_by_short_id(self, short_id: int) -> TaskRecord | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM nyanjou_tasks WHERE short_id = ?", (int(short_id),)).fetchone()
        return self._row(row)

    def list(self, *, guild_id: int | None = None, user_id: int | None = None,
             statuses: tuple[str, ...] | None = None, limit: int = 20) -> list[TaskRecord]:
        q = "SELECT * FROM nyanjou_tasks WHERE 1=1"
        args: list[Any] = []
        if guild_id is not None:
            q += " AND guild_id IS ?"
            args.append(guild_id)
        if user_id is not None:
            q += " AND user_id = ?"
            args.append(user_id)
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"
            args += list(statuses)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def active(self) -> list[TaskRecord]:
        return self.list(statuses=ACTIVE_STATUSES, limit=1000)
