"""SQLite 持久层（B4 表结构）。WAL 模式，每次操作独立连接。"""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery (
    delivery_id TEXT PRIMARY KEY,
    event       TEXT,
    payload     TEXT,
    received_at TEXT
);
CREATE TABLE IF NOT EXISTS job (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id  TEXT UNIQUE,
    recording_id INTEGER,
    event        TEXT,
    status       TEXT DEFAULT 'pending',   -- pending/done/failed
    attempts     INTEGER DEFAULT 0,
    next_run_at  REAL DEFAULT 0,
    error        TEXT,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS alert (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id  INTEGER,
    segment_index INTEGER,
    speaker       TEXT,
    start_time    REAL,
    end_time      REAL,
    term          TEXT,
    level         TEXT,
    category      TEXT,
    snippet       TEXT,
    confidence    REAL DEFAULT 1.0,
    source        TEXT DEFAULT 'lexicon',  -- lexicon/llm
    status        TEXT DEFAULT 'open',     -- open/confirmed/ignored
    created_at    TEXT,
    UNIQUE(recording_id, segment_index, term, level)
);
CREATE TABLE IF NOT EXISTS scan (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id INTEGER,
    started_at   TEXT,
    finished_at  TEXT,
    hits         INTEGER,
    max_level    TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_rec ON alert(recording_id);
CREATE INDEX IF NOT EXISTS idx_job_due ON job(status, next_run_at);
"""


def init_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as con:
        con.executescript(SCHEMA)


@contextmanager
def connect(db_path: Path):
    con = sqlite3.connect(str(db_path), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        yield con
        con.commit()
    finally:
        con.close()


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


# ---------- delivery ----------

def delivery_seen(con, delivery_id: str) -> bool:
    return con.execute("SELECT 1 FROM delivery WHERE delivery_id=?", (delivery_id,)).fetchone() is not None


def save_delivery(con, delivery_id: str, event: str, payload: str):
    con.execute("INSERT OR IGNORE INTO delivery(delivery_id, event, payload, received_at) VALUES (?,?,?,?)",
                (delivery_id, event, payload, now()))


# ---------- job ----------

def enqueue_job(con, delivery_id: str, recording_id: int, event: str) -> bool:
    """返回 False 表示重复投递（幂等）。"""
    cur = con.execute(
        "INSERT OR IGNORE INTO job(delivery_id, recording_id, event, created_at, updated_at) VALUES (?,?,?,?,?)",
        (delivery_id, recording_id, event, now(), now()))
    return cur.rowcount > 0


def due_jobs(con, max_attempts: int, limit: int = 10):
    return con.execute(
        "SELECT * FROM job WHERE status='pending' AND next_run_at<=? AND attempts<? ORDER BY id LIMIT ?",
        (time.time(), max_attempts, limit)).fetchall()


def job_failed(con, job_id: int, attempts: int, error: str, retry_in: float):
    con.execute("UPDATE job SET attempts=?, error=?, next_run_at=?, updated_at=? WHERE id=?",
                (attempts, error[:500], time.time() + retry_in, now(), job_id))


def job_done(con, job_id: int):
    con.execute("UPDATE job SET status='done', error=NULL, updated_at=? WHERE id=?", (now(), job_id))


def job_dead(con, job_id: int, error: str):
    con.execute("UPDATE job SET status='failed', error=?, updated_at=? WHERE id=?", (error[:500], now(), job_id))


# ---------- alert / scan ----------

def save_alerts(con, hits):
    """INSERT OR IGNORE —— UNIQUE(recording_id, segment_index, term, level) 天然去重。"""
    for h in hits:
        con.execute(
            "INSERT OR IGNORE INTO alert(recording_id, segment_index, speaker, start_time, end_time, "
            "term, level, category, snippet, confidence, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (h["recording_id"], h.get("segment_index"), h.get("speaker"), h.get("start_time"),
             h.get("end_time"), h["term"], h["level"], h.get("category", ""),
             (h.get("text") or "")[:300], h.get("confidence", 1.0), h.get("source", "lexicon"), now()))


def clear_alerts(con, recording_id: int):
    """重扫前清掉本录音旧命中（重转写后词表可能已变化）。"""
    con.execute("DELETE FROM alert WHERE recording_id=?", (recording_id,))


def alerts_for(con, recording_id: int):
    return con.execute(
        "SELECT * FROM alert WHERE recording_id=? ORDER BY start_time", (recording_id,)).fetchall()


def save_scan(con, recording_id: int, hits: int, max_level):
    con.execute("INSERT INTO scan(recording_id, started_at, finished_at, hits, max_level) VALUES (?,?,?,?,?)",
                (recording_id, now(), now(), hits, max_level))


def last_scan(con, recording_id: int):
    return con.execute(
        "SELECT * FROM scan WHERE recording_id=? ORDER BY id DESC LIMIT 1", (recording_id,)).fetchone()


def recent_alerts(con, limit: int = 200):
    return con.execute(
        "SELECT * FROM alert ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
