"""SQLite: kết nối, migration có phiên bản, hàm truy vấn tiện dụng.

Mỗi lần gọi mở một kết nối ngắn (WAL + busy_timeout) để API và worker là hai
tiến trình riêng vẫn dùng chung an toàn.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .config import get_settings

MIGRATIONS: list[str] = [
    # 1 — lược đồ: một văn phòng duy nhất (bảng teams chỉ có đúng một dòng, id=1) làm nhạc Suno + video nhạc.
    # Đầu vào là YÊU CẦU của bạn (link bài mẫu + thể loại); mỗi bước để lại một báo cáo trên dòng thời gian.
    """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

    CREATE TABLE teams (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        name TEXT NOT NULL,
        slug TEXT NOT NULL,
        paused INTEGER NOT NULL DEFAULT 0,
        workspace_dir TEXT NOT NULL,
        created_at REAL NOT NULL
    );

    CREATE TABLE employees (
        id TEXT PRIMARY KEY,
        team_id INTEGER REFERENCES teams(id),
        role TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        cli TEXT NOT NULL,
        model TEXT,
        fallback_cli TEXT,
        instructions TEXT NOT NULL,
        allowed_tools TEXT NOT NULL,
        io_spec TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'idle',
        current_job_id INTEGER
    );

    CREATE TABLE memories (
        id INTEGER PRIMARY KEY,
        scope TEXT NOT NULL CHECK(scope IN ('company','team','job')),
        team_id INTEGER REFERENCES teams(id),
        job_id INTEGER,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'user',
        active INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL
    );

    CREATE TABLE requests (
        id INTEGER PRIMARY KEY,
        team_id INTEGER NOT NULL REFERENCES teams(id),
        title TEXT NOT NULL,
        links TEXT NOT NULL DEFAULT '[]',
        genre TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        output_type TEXT NOT NULL CHECK(output_type IN ('single','long','both')),
        track_count INTEGER NOT NULL CHECK(track_count BETWEEN 1 AND 30),
        vocals TEXT NOT NULL CHECK(vocals IN ('vocal','instrumental')),
        lyrics_language TEXT NOT NULL DEFAULT 'vi',
        stage TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        stage_note TEXT,
        lead_plan TEXT,
        research TEXT,
        prompts TEXT,
        prompts_version INTEGER NOT NULL DEFAULT 0,
        revisions INTEGER NOT NULL DEFAULT 0,
        outputs TEXT,
        final_note TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    CREATE TABLE tracks (
        id INTEGER PRIMARY KEY,
        request_id INTEGER NOT NULL REFERENCES requests(id),
        idx INTEGER NOT NULL,
        title TEXT NOT NULL,
        audio_path TEXT,
        audio_name TEXT,
        duration REAL,
        uploaded_at REAL,
        UNIQUE(request_id, idx)
    );

    CREATE TABLE request_files (
        id INTEGER PRIMARY KEY,
        request_id INTEGER NOT NULL REFERENCES requests(id),
        kind TEXT NOT NULL CHECK(kind IN ('image','reference_audio')),
        path TEXT NOT NULL,
        filename TEXT NOT NULL,
        created_at REAL NOT NULL
    );

    CREATE TABLE request_reports (
        id INTEGER PRIMARY KEY,
        request_id INTEGER NOT NULL REFERENCES requests(id),
        step TEXT NOT NULL,
        role TEXT,
        author TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        data TEXT,
        job_id INTEGER,
        created_at REAL NOT NULL
    );
    CREATE INDEX request_reports_req ON request_reports(request_id, id);

    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        team_id INTEGER REFERENCES teams(id),
        request_id INTEGER,
        kind TEXT NOT NULL,
        role TEXT,
        status TEXT NOT NULL DEFAULT 'queued',
        priority INTEGER NOT NULL DEFAULT 100,
        payload TEXT NOT NULL DEFAULT '{}',
        result TEXT,
        checkpoint TEXT NOT NULL DEFAULT '{}',
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        next_run_at REAL NOT NULL DEFAULT 0,
        lease_owner TEXT,
        lease_expires_at REAL,
        idempotency_key TEXT UNIQUE,
        side_effect INTEGER NOT NULL DEFAULT 0,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        error_kind TEXT,
        error TEXT,
        wait_reason TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX jobs_claim ON jobs(status, next_run_at, priority);

    CREATE TABLE cli_runs (
        id INTEGER PRIMARY KEY,
        job_id INTEGER,
        employee_id TEXT,
        cli TEXT NOT NULL,
        model TEXT,
        session_id TEXT,
        resumed INTEGER NOT NULL DEFAULT 0,
        pid INTEGER,
        args TEXT,
        status TEXT NOT NULL DEFAULT 'running',
        error_kind TEXT,
        exit_code INTEGER,
        stdout_path TEXT,
        stderr_path TEXT,
        tool_calls TEXT,
        observed_model TEXT,
        started_at REAL NOT NULL,
        ended_at REAL
    );

    CREATE TABLE cli_sessions (
        id INTEGER PRIMARY KEY,
        team_id INTEGER,
        employee_id TEXT NOT NULL,
        cli TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        session_id TEXT NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(employee_id, cli, scope_key)
    );

    CREATE TABLE handoffs (
        id INTEGER PRIMARY KEY,
        job_id INTEGER NOT NULL,
        from_cli TEXT NOT NULL,
        to_cli TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at REAL NOT NULL
    );

    CREATE TABLE provider_state (
        cli TEXT PRIMARY KEY,
        blocked_until REAL,
        reason TEXT,
        updated_at REAL NOT NULL
    );

    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        team_id INTEGER,
        job_id INTEGER,
        request_id INTEGER,
        level TEXT NOT NULL DEFAULT 'info',
        type TEXT NOT NULL,
        message TEXT NOT NULL,
        data TEXT
    );
    CREATE INDEX events_team ON events(team_id, id);

    CREATE TABLE workers (
        id TEXT PRIMARY KEY,
        pid INTEGER NOT NULL,
        started_at REAL NOT NULL,
        heartbeat_at REAL NOT NULL,
        stopped_at REAL
    );

    CREATE TABLE reports (
        id INTEGER PRIMARY KEY,
        team_id INTEGER,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        job_id INTEGER,
        created_at REAL NOT NULL
    );
    """,
]


def _connect() -> sqlite3.Connection:
    s = get_settings()
    conn = sqlite3.connect(s.db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def tx(immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """Giao dịch ghi. BEGIN IMMEDIATE để lấy khóa ghi ngay (dùng cho nhận việc)."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def migrate() -> int:
    with connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = c.execute("SELECT version FROM schema_version").fetchone()
        current = row[0] if row else 0
        if not row:
            c.execute("INSERT INTO schema_version VALUES (0)")
        for i, sql in enumerate(MIGRATIONS[current:], start=current + 1):
            c.execute("BEGIN IMMEDIATE")
            try:
                for stmt in [x for x in sql.split(";") if x.strip()]:
                    c.execute(stmt)
                c.execute("UPDATE schema_version SET version=?", (i,))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return len(MIGRATIONS)


def row(conn: sqlite3.Connection, sql: str, *args: Any) -> dict | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def rows(conn: sqlite3.Connection, sql: str, *args: Any) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def q1(sql: str, *args: Any) -> dict | None:
    with connect() as c:
        return row(c, sql, *args)


def qa(sql: str, *args: Any) -> list[dict]:
    with connect() as c:
        return rows(c, sql, *args)


def execute(sql: str, *args: Any) -> int:
    with connect() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def now() -> float:
    return time.time()


def jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def jload(v: str | None, default: Any = None) -> Any:
    if v is None or v == "":
        return default
    return json.loads(v)


# --- cài đặt công ty -------------------------------------------------------

DEFAULT_COMPANY = {
    "company_running": False,
    "allow_cli_fallback": False,
}


def get_setting(key: str) -> Any:
    r = q1("SELECT value FROM settings WHERE key=?", key)
    if r is None:
        return DEFAULT_COMPANY.get(key)
    return json.loads(r["value"])


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        key, json.dumps(value),
    )


def company_settings() -> dict:
    out = dict(DEFAULT_COMPANY)
    for r in qa("SELECT key, value FROM settings"):
        out[r["key"]] = json.loads(r["value"])
    return out
