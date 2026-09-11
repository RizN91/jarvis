"""Local SQLite storage: memory, vocabulary, usage, conversation, tasks, audit.

Design rules taken from the build spec:
  * Local only. No cloud database. No telemetry.
  * Provenance is stored: every memory row records whether it came from the
    user (authoritative) or a model guess (advisory). The assistant may not
    learn permissions from untrusted page text - see core/tools.
  * Usage snapshots are CUMULATIVE, not increments. `record_live_usage`
    upserts the running maximum for a session rather than summing, because
    adding cumulative snapshots together would massively over-report cost.
    Only genuinely incremental events (per transcription call, per backend
    response) are appended.

Thread-safety: one connection guarded by an RLock, WAL mode.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config
from .logsetup import get as _log

log = _log("db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,              -- fact | preference | vocab | place
    text        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'user',  -- user | model | import
    confidence  REAL NOT NULL DEFAULT 1.0,
    approved    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_text ON memory(kind, text);

CREATE TABLE IF NOT EXISTS vocab (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    term        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,              -- ISO local time
    day         TEXT NOT NULL,              -- YYYY-MM-DD local
    month       TEXT NOT NULL,              -- YYYY-MM local
    session_id  TEXT,                       -- groups cumulative snapshots
    category    TEXT NOT NULL,              -- live_voice | transcribe | backend | tool | agent
    seconds     REAL NOT NULL DEFAULT 0,
    usd         REAL NOT NULL DEFAULT 0,
    meta        TEXT NOT NULL DEFAULT '{}',
    finalized   INTEGER NOT NULL DEFAULT 1,
    UNIQUE(session_id, category)            -- enforces snapshot semantics
);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_events(day);
CREATE INDEX IF NOT EXISTS idx_usage_month ON usage_events(month);

CREATE TABLE IF NOT EXISTS conversation (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    session_id  TEXT,
    role        TEXT NOT NULL,              -- user | assistant | system | tool
    text        TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversation(session_id);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    agent       TEXT NOT NULL,              -- codex | claude | internal
    session_id  TEXT,
    workdir     TEXT,
    title       TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    meta        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    allowed     INTEGER NOT NULL DEFAULT 1
);
"""


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


class Database:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or config.db_path())
        new_database = not self.path.exists()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        if new_database:
            self._seed_vocab()

    # ------------------------------------------------------------ helpers
    def _exec(self, sql: str, args: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(args))
            self._conn.commit()
            return cur

    def _query(self, sql: str, args: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(args)).fetchall())

    def close(self) -> None:
        """Close the connection AND drop the process-wide singleton.

        Dropping it matters: `db()` caches this object, so without the reset it
        keeps handing back a CLOSED Database and every later call raises
        sqlite3.ProgrammingError. Anything that runs after a shutdown - a late
        audit write from a second quit(), a tool call, a test that continues -
        then fails for a reason that has nothing to do with it. Reopening is
        cheap and idempotent.
        """
        global _DB
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
        with _DB_LOCK:
            if _DB is self:
                _DB = None

    # ---------------------------------------------------------- vocabulary
    def _seed_vocab(self) -> None:
        existing = {r["term"] for r in self._query("SELECT term FROM vocab")}
        for term in config.DEFAULT_VOCABULARY:
            if term not in existing:
                self.add_vocab(term)

    def add_vocab(self, term: str) -> None:
        term = (term or "").strip()
        if not term:
            return
        self._exec(
            "INSERT OR IGNORE INTO vocab(term, created_at) VALUES(?,?)",
            (term, _now()),
        )

    def remove_vocab(self, term: str) -> None:
        self._exec("DELETE FROM vocab WHERE term=?", (term.strip(),))

    def list_vocab(self) -> list[str]:
        return [r["term"] for r in self._query("SELECT term FROM vocab ORDER BY id")]

    def set_vocab(self, terms: list[str]) -> None:
        clean = list(dict.fromkeys(t.strip() for t in terms if t.strip()))
        with self._lock:
            with self._conn:
                self._conn.execute("DELETE FROM vocab")
                self._conn.executemany("INSERT OR IGNORE INTO vocab(term,created_at) VALUES(?,?)",
                                       [(t, _now()) for t in clean])

    # -------------------------------------------------------------- memory
    def remember(self, text: str, kind: str = "fact", source: str = "user",
                 confidence: float = 1.0, approved: bool = True) -> int:
        text = (text or "").strip()
        if not text:
            raise ValueError("empty memory text")
        now = _now()
        cur = self._exec(
            """INSERT INTO memory(kind,text,source,confidence,approved,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(kind,text) DO UPDATE SET updated_at=excluded.updated_at,
                   confidence=excluded.confidence, approved=excluded.approved""",
            (kind, text, source, float(confidence), 1 if approved else 0, now, now),
        )
        return int(cur.lastrowid or 0)

    def forget(self, mem_id: int) -> None:
        self._exec("DELETE FROM memory WHERE id=?", (int(mem_id),))

    def list_memory(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM memory WHERE approved=1 ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        )
        return [dict(r) for r in rows]

    def search_memory(self, q: str, limit: int = 12) -> list[dict[str, Any]]:
        q = (q or "").strip()
        if not q:
            return self.list_memory(limit)
        rows = self._query(
            """SELECT * FROM memory WHERE approved=1 AND text LIKE ?
               ORDER BY updated_at DESC LIMIT ?""",
            (f"%{q}%", int(limit)),
        )
        return [dict(r) for r in rows]

    def export_memory(self) -> dict[str, Any]:
        return {
            "version": 1,
            "exported_at": _now(),
            "memory": self.list_memory(10_000),
            "vocab": self.list_vocab(),
        }

    def import_memory(self, blob: dict[str, Any], source: str = "import") -> int:
        n = 0
        for item in blob.get("memory", []):
            try:
                self.remember(
                    item.get("text", ""), kind=item.get("kind", "fact"),
                    source=source, confidence=float(item.get("confidence", 0.5)),
                    approved=bool(item.get("approved", True)),
                )
                n += 1
            except Exception:
                continue
        for term in blob.get("vocab", []):
            self.add_vocab(term)
        return n

    # -------------------------------------------------------------- usage
    def record_usage(self, category: str, usd: float, seconds: float = 0.0,
                     session_id: Optional[str] = None,
                     meta: Optional[dict] = None, finalized: bool = True) -> None:
        """Record a usage event.

        If `session_id` is given, the (session_id, category) pair is a
        CUMULATIVE SNAPSHOT and we keep the maximum observed value instead of
        summing - adding snapshots would over-report badly. Without a
        session_id the event is an increment and is appended.
        """
        now = datetime.now()
        day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
        meta_json = json.dumps(meta or {})
        if session_id:
            with self._lock:
                row = self._conn.execute(
                    "SELECT id, seconds, usd FROM usage_events WHERE session_id=? AND category=?",
                    (session_id, category),
                ).fetchone()
                if row is None:
                    self._conn.execute(
                        """INSERT INTO usage_events(ts,day,month,session_id,category,
                               seconds,usd,meta,finalized) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (now.replace(microsecond=0).isoformat(sep=" "), day, month,
                         session_id, category, float(seconds), float(usd), meta_json,
                         1 if finalized else 0),
                    )
                else:
                    # Snapshots only ever grow; never sum them.
                    self._conn.execute(
                        """UPDATE usage_events SET seconds=?, usd=?, ts=?, meta=?,
                               finalized=? WHERE id=?""",
                        (max(float(seconds), float(row["seconds"])),
                         max(float(usd), float(row["usd"])),
                         now.replace(microsecond=0).isoformat(sep=" "),
                         meta_json, 1 if finalized else 0, row["id"]),
                    )
                self._conn.commit()
        else:
            self._exec(
                """INSERT INTO usage_events(ts,day,month,session_id,category,
                       seconds,usd,meta,finalized) VALUES(?,?,?,?,?,?,?,?,?)""",
                (now.replace(microsecond=0).isoformat(sep=" "), day, month, None,
                 category, float(seconds), float(usd), meta_json, 1 if finalized else 0),
            )

    def _sum(self, where: str, arg: str) -> dict[str, Any]:
        rows = self._query(
            f"""SELECT category, SUM(seconds) AS secs, SUM(usd) AS usd
                FROM usage_events WHERE {where}=? GROUP BY category""",
            (arg,),
        )
        out: dict[str, Any] = {
            "live_seconds": 0.0, "transcribe_seconds": 0.0, "backend_usd": 0.0,
            "tools_usd": 0.0, "agent_usd": 0.0, "total_usd": 0.0,
        }
        for r in rows:
            cat = r["category"]
            if cat == "live_voice":
                out["live_seconds"] += float(r["secs"] or 0)
            elif cat == "transcribe":
                out["transcribe_seconds"] += float(r["secs"] or 0)
            elif cat == "backend":
                out["backend_usd"] += float(r["usd"] or 0)
            elif cat == "tool":
                out["tools_usd"] += float(r["usd"] or 0)
            elif cat == "agent":
                out["agent_usd"] += float(r["usd"] or 0)
            out["total_usd"] += float(r["usd"] or 0)
        return out

    def usage_today(self) -> dict[str, Any]:
        return self._sum("day", date.today().strftime("%Y-%m-%d"))

    def usage_month(self) -> dict[str, Any]:
        return self._sum("month", date.today().strftime("%Y-%m"))

    def usage_events(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM usage_events ORDER BY id DESC LIMIT ?", (int(limit),)
        )
        return [dict(r) for r in rows]

    def mark_unfinalized(self, session_id: str, category: str = "live_voice") -> None:
        """Record that a session ended without a server-confirmed final usage.

        The stored cost stays as the last observed cumulative snapshot, but is
        flagged so the UI can say the charge is unconfirmed. We never invent a
        final number.
        """
        self._exec(
            "UPDATE usage_events SET finalized=0 WHERE session_id=? AND category=?",
            (session_id, category),
        )

    def prune_conversation(self, days: Optional[int] = None) -> int:
        days = int(days if days is not None else config.get("conversation_retention_days", 30))
        if days <= 0:
            return 0
        cutoff = (datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat(sep=" ")
        cur = self._exec("DELETE FROM conversation WHERE ts < ?", (cutoff,))
        return cur.rowcount or 0

    # -------------------------------------------------------- conversation
    def add_turn(self, role: str, text: str, session_id: Optional[str] = None,
                 meta: Optional[dict] = None) -> None:
        self._exec(
            "INSERT INTO conversation(ts,session_id,role,text,meta) VALUES(?,?,?,?,?)",
            (_now(), session_id, role, text, json.dumps(meta or {})),
        )

    def recent_turns(self, limit: int = 20, session_id: Optional[str] = None) -> list[dict[str, Any]]:
        if session_id:
            rows = self._query(
                "SELECT * FROM conversation WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, int(limit)),
            )
        else:
            rows = self._query(
                "SELECT * FROM conversation ORDER BY id DESC LIMIT ?", (int(limit),)
            )
        return [dict(r) for r in reversed(rows)]

    def clear_conversation(self) -> None:
        self._exec("DELETE FROM conversation")

    # --------------------------------------------------------------- tasks
    def start_task(self, agent: str, session_id: str, workdir: str,
                   title: str, meta: Optional[dict] = None) -> int:
        now = _now()
        cur = self._exec(
            """INSERT INTO tasks(ts,updated_at,agent,session_id,workdir,title,status,meta)
               VALUES(?,?,?,?,?,?,?,?)""",
            (now, now, agent, session_id, workdir, title, "running", json.dumps(meta or {})),
        )
        return int(cur.lastrowid or 0)

    def update_task(self, task_id: int, status: Optional[str] = None,
                    meta: Optional[dict] = None) -> None:
        sets, args = ["updated_at=?"], [_now()]
        if status:
            sets.append("status=?")
            args.append(status)
        if meta is not None:
            sets.append("meta=?")
            args.append(json.dumps(meta))
        args.append(int(task_id))
        self._exec(f"UPDATE tasks SET {','.join(sets)} WHERE id=?", args)

    def list_tasks(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(r) for r in rows]

    def get_task(self, task_id: int) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM tasks WHERE id=?", (int(task_id),))
        return dict(rows[0]) if rows else None

    # --------------------------------------------------------------- audit
    def audit(self, action: str, detail: Any, allowed: bool = True) -> None:
        try:
            self._exec(
                "INSERT INTO audit(ts,action,detail,allowed) VALUES(?,?,?,?)",
                (_now(), action, json.dumps(detail, default=str), 1 if allowed else 0),
            )
        except Exception as exc:  # audit must never break the app
            log.warning("audit write failed: %s", exc)

    def recent_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(r) for r in rows]


_DB: Optional[Database] = None
_DB_LOCK = threading.Lock()


def db() -> Database:
    global _DB
    with _DB_LOCK:
        if _DB is None:
            _DB = Database()
        return _DB
