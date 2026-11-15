"""Durable SQLite run/event storage. Images stay in the run artifact directory."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class RunDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    artifact_path TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT,
                    model TEXT,
                    observation_mode TEXT,
                    status TEXT NOT NULL DEFAULT 'created',
                    total_tokens INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    time TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    call_id TEXT,
                    observation_version INTEGER,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run_kind ON events(run_id, kind, event_id);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_run(self, run_id, artifact_path, time):
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs(run_id,artifact_path,started_at,updated_at) VALUES (?,?,?,?)",
                (run_id, str(Path(artifact_path).resolve()), time, time),
            )

    def append(self, run_id, record):
        kind, timestamp = record["event"], record["time"]
        payload = {k: v for k, v in record.items() if k not in {"event", "time"}}
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(run_id,time,kind,call_id,observation_version,payload_json) VALUES (?,?,?,?,?,?)",
                (run_id, timestamp, kind, payload.get("call_id"), payload.get("observation_version"), encoded),
            )
            db.execute("UPDATE runs SET updated_at=? WHERE run_id=?", (timestamp, run_id))
            if kind == "start":
                db.execute(
                    "UPDATE runs SET model=?, observation_mode=?, status='running' WHERE run_id=?",
                    (payload.get("model"), payload.get("observation_mode"), run_id),
                )
            elif kind == "user":
                db.execute("UPDATE runs SET status='running' WHERE run_id=?", (run_id,))
            elif kind == "pause":
                db.execute("UPDATE runs SET status=? WHERE run_id=?", (payload["status"], run_id))
            elif kind == "interrupted":
                db.execute("UPDATE runs SET status='interrupted' WHERE run_id=?", (run_id,))
            elif kind == "close":
                db.execute("UPDATE runs SET closed_at=? WHERE run_id=?", (timestamp, run_id))
            usage = payload.get("usage") or {}
            if "total_tokens" in usage:
                db.execute(
                    "UPDATE runs SET total_tokens=total_tokens+? WHERE run_id=?", (usage["total_tokens"], run_id)
                )

    def runs(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM runs ORDER BY started_at DESC")]

    def events(self, run_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY event_id", (run_id,))]
