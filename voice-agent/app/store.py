"""Call log, kept in a local SQLite file."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CallStore:
    def __init__(self, path: Path | str):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS calls (
                    sid TEXT PRIMARY KEY,
                    from_number TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    transcript TEXT NOT NULL DEFAULT '[]',
                    summary TEXT,
                    finalized INTEGER NOT NULL DEFAULT 0
                )"""
            )
            self._db.commit()

    def start_call(self, sid: str, from_number: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO calls (sid, from_number, started_at) VALUES (?, ?, ?)",
                (sid, from_number, _now()),
            )
            self._db.commit()

    def add_line(self, sid: str, role: str, text: str) -> None:
        with self._lock:
            row = self._db.execute("SELECT transcript FROM calls WHERE sid = ?", (sid,)).fetchone()
            if row is None:
                return
            transcript = json.loads(row["transcript"])
            transcript.append({"role": role, "text": text, "at": _now()})
            self._db.execute(
                "UPDATE calls SET transcript = ? WHERE sid = ?", (json.dumps(transcript), sid)
            )
            self._db.commit()

    def claim_for_finalize(self, sid: str) -> bool:
        """Marks the call as finished. Returns False if it was already finalized."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE calls SET finalized = 1, ended_at = ? WHERE sid = ? AND finalized = 0",
                (_now(), sid),
            )
            self._db.commit()
            return cur.rowcount == 1

    def save_summary(self, sid: str, summary: dict | None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE calls SET summary = ? WHERE sid = ?",
                (json.dumps(summary) if summary is not None else None, sid),
            )
            self._db.commit()

    def get(self, sid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM calls WHERE sid = ?", (sid,)).fetchone()
        return self._row(row) if row else None

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["transcript"] = json.loads(data["transcript"] or "[]")
        data["summary"] = json.loads(data["summary"]) if data["summary"] else None
        return data
