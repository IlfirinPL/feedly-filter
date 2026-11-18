"""SQLite history: runs, marked entries, last run per feed. Size is capped."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
create table if not exists runs (
    id integer primary key,
    feed_id text not null,
    feed_name text not null,
    started text not null,
    finished text,
    dry_run integer not null,
    processed integer not null default 0,
    marked integer not null default 0,
    error text not null default ''
);
create table if not exists marks (
    id integer primary key,
    run_id integer not null references runs(id) on delete cascade,
    feed_id text not null,
    feed_name text not null,
    entry_id text not null,
    title text not null,
    published text not null,
    reason text not null,
    action text not null
);
create index if not exists marks_run on marks(run_id);
create table if not exists feed_state (
    feed_id text primary key,
    last_run text not null
);
create table if not exists requests (
    day text not null,
    kind text not null,
    status integer not null,
    count integer not null default 0,
    primary key (day, kind, status)
);
"""

PRUNE_BATCH = 500


class History:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def start_run(self, feed_id: str, feed_name: str, dry_run: bool, started: datetime) -> int:
        with self._lock:
            cursor = self._db.execute(
                "insert into runs (feed_id, feed_name, started, dry_run) values (?, ?, ?, ?)",
                (feed_id, feed_name, started.isoformat(), int(dry_run)),
            )
            self._db.commit()
            return cursor.lastrowid

    def finish_run(self, run_id: int, processed: int, marked: int, error: str, finished: datetime) -> None:
        with self._lock:
            self._db.execute(
                "update runs set finished = ?, processed = ?, marked = ?, error = ? where id = ?",
                (finished.isoformat(), processed, marked, error, run_id),
            )
            self._db.commit()

    def add_marks(self, run_id: int, rows: list[tuple[str, str, str, str, str, str, str]]) -> None:
        """rows: (feed_id, feed_name, entry_id, title, published, reason, action)."""
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "insert into marks (run_id, feed_id, feed_name, entry_id, title, published, reason, action) "
                "values (?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, *row) for row in rows],
            )
            self._db.commit()

    def set_last_run(self, feed_id: str, when: datetime) -> None:
        with self._lock:
            self._db.execute(
                "insert into feed_state (feed_id, last_run) values (?, ?) "
                "on conflict(feed_id) do update set last_run = excluded.last_run",
                (feed_id, when.isoformat()),
            )
            self._db.commit()

    def last_runs(self) -> dict[str, datetime]:
        with self._lock:
            rows = self._db.execute("select feed_id, last_run from feed_state").fetchall()
        return {feed_id: datetime.fromisoformat(last_run) for feed_id, last_run in rows}

    def recent_runs(self, limit: int = 20) -> list[tuple]:
        with self._lock:
            return self._db.execute(
                "select id, feed_name, started, finished, dry_run, processed, marked, error "
                "from runs order by id desc limit ?",
                (limit,),
            ).fetchall()

    def recent_marks(self, feed_id: str | None = None, run_id: int | None = None, limit: int = 200) -> list[tuple]:
        clauses, params = [], []
        if feed_id:
            clauses.append("feed_id = ?")
            params.append(feed_id)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = f" where {' and '.join(clauses)}" if clauses else ""
        query = "select feed_name, entry_id, title, published, reason, action from marks" + where
        with self._lock:
            return self._db.execute(query + " order by id desc limit ?", (*params, limit)).fetchall()

    def record_request(self, kind: str, status: int) -> None:
        """Count one HTTP attempt. Stored per UTC day, so the table stays small."""
        day = datetime.now(UTC).date().isoformat()
        with self._lock:
            self._db.execute(
                "insert into requests (day, kind, status, count) values (?, ?, ?, 1) "
                "on conflict(day, kind, status) do update set count = count + 1",
                (day, kind, status),
            )
            self._db.commit()

    def request_stats(self, since: str) -> list[tuple[str, int, int]]:
        """(kind, status, count) for days on or after `since` (ISO date)."""
        with self._lock:
            return self._db.execute(
                "select kind, status, sum(count) from requests where day >= ? group by kind, status",
                (since,),
            ).fetchall()

    def daily_requests(self, since: str) -> list[tuple[str, int]]:
        """(day, count) for days on or after `since` (ISO date)."""
        with self._lock:
            return self._db.execute(
                "select day, sum(count) from requests where day >= ? group by day order by day",
                (since,),
            ).fetchall()

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def prune(self, limit_bytes: int) -> int:
        """Delete the oldest runs (and their marks) until the file fits the limit. Returns runs removed."""
        removed = 0
        with self._lock:
            while self.size_bytes() > limit_bytes:
                victims = [row[0] for row in self._db.execute(
                    "select id from runs order by id limit ?", (PRUNE_BATCH,)).fetchall()]
                if not victims:
                    break
                placeholders = ",".join("?" * len(victims))
                self._db.execute(f"delete from marks where run_id in ({placeholders})", victims)
                self._db.execute(f"delete from runs where id in ({placeholders})", victims)
                self._db.commit()
                # VACUUM shrinks the file; without it the size check would never drop.
                self._db.execute("vacuum")
                removed += len(victims)
        return removed


def file_size_mb(path: Path) -> float:
    return os.path.getsize(path) / (1024 * 1024) if path.exists() else 0.0
