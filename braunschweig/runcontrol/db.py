"""SQLite persistence for runcontrol (runs, queue, events).

Single-writer usage (the one app process); WAL mode so the web thread can
read while the daemon thread writes. All timestamps ISO-8601 UTC strings.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import LaunchHandle, RunSpec, RunStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    label TEXT NOT NULL,
    config_path TEXT NOT NULL,
    status TEXT NOT NULL,
    exit_code INTEGER,
    tmux_session TEXT,
    pid INTEGER,
    log_path TEXT,
    exit_marker_path TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS queue (
    position INTEGER NOT NULL,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS enrichment (
    artifact_key TEXT PRIMARY KEY,
    dir_mtime REAL NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_TERMINAL = (RunStatus.DONE, RunStatus.FAILED, RunStatus.STOPPED)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def insert_run(self, spec: RunSpec, status: RunStatus) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, target, label, config_path, status, created_at) VALUES (?,?,?,?,?,?)",
            (spec.run_id, spec.target, spec.label, spec.config_path, status.value, _now()))
        self._conn.commit()

    def set_status(self, run_id: str, status: RunStatus, *, exit_code: int | None = None) -> None:
        finished = _now() if status in _TERMINAL else None
        self._conn.execute(
            "UPDATE runs SET status=?, exit_code=COALESCE(?, exit_code), finished_at=COALESCE(?, finished_at) WHERE run_id=?",
            (status.value, exit_code, finished, run_id))
        self._conn.commit()

    def attach_handle(self, run_id: str, h: LaunchHandle) -> None:
        self._conn.execute(
            "UPDATE runs SET tmux_session=?, pid=?, log_path=?, exit_marker_path=? WHERE run_id=?",
            (h.tmux_session, h.pid, h.log_path, h.exit_marker_path, run_id))
        self._conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    # -- queue ------------------------------------------------------------
    def enqueue(self, run_id: str) -> None:
        pos = self._conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM queue").fetchone()[0]
        self._conn.execute("INSERT INTO queue (position, run_id) VALUES (?,?)", (pos, run_id))
        self._conn.commit()

    def queue_ids(self) -> list[str]:
        rows = self._conn.execute("SELECT run_id FROM queue ORDER BY position").fetchall()
        return [r["run_id"] for r in rows]

    def dequeue_next(self) -> str | None:
        row = self._conn.execute("SELECT run_id FROM queue ORDER BY position LIMIT 1").fetchone()
        if row is None:
            return None
        self._conn.execute("DELETE FROM queue WHERE run_id=?", (row["run_id"],))
        self._conn.commit()
        return row["run_id"]

    def remove_from_queue(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM queue WHERE run_id=?", (run_id,))
        self._conn.commit()

    def reorder_queue(self, ids: list[str]) -> None:
        current = set(self.queue_ids())
        if set(ids) != current:
            raise ValueError(f"reorder_queue: ids {sorted(ids)} do not match queued runs {sorted(current)}")
        self._conn.execute("DELETE FROM queue")
        self._conn.executemany("INSERT INTO queue (position, run_id) VALUES (?,?)",
                               list(enumerate(ids, start=1)))
        self._conn.commit()

    # -- events -----------------------------------------------------------
    def add_event(self, run_id: str, kind: str, message: str) -> None:
        self._conn.execute("INSERT INTO events (run_id, at, kind, message) VALUES (?,?,?,?)",
                           (run_id, _now(), kind, message))
        self._conn.commit()

    def events(self, run_id: str) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    # -- enrichment cache -------------------------------------------------
    def get_enrichment(self, key: str, dir_mtime: float) -> dict | None:
        row = self._conn.execute(
            "SELECT dir_mtime, payload FROM enrichment WHERE artifact_key=?", (key,)).fetchone()
        if row is None or row["dir_mtime"] != dir_mtime:
            return None
        import json
        return json.loads(row["payload"])

    def put_enrichment(self, key: str, dir_mtime: float, payload: dict) -> None:
        import json
        self._conn.execute(
            "INSERT INTO enrichment (artifact_key, dir_mtime, payload, created_at) "
            "VALUES (?,?,?,?) ON CONFLICT(artifact_key) DO UPDATE SET "
            "dir_mtime=excluded.dir_mtime, payload=excluded.payload, created_at=excluded.created_at",
            (key, dir_mtime, json.dumps(payload), _now()))
        self._conn.commit()
