"""SQLite data layer for StreamCast.

Two tables:
  streams  — one broadcast target (name, RTMP key, YouTube URL, schedule, state)
  videos   — files queued under a stream, with encode status and play order
"""
import sqlite3
import time
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS streams (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    rtmp_key      TEXT DEFAULT '',
    youtube_url   TEXT DEFAULT '',
    loop_queue    INTEGER DEFAULT 1,      -- 1 = repeat the queue forever (24/7)
    is_live       INTEGER DEFAULT 0,
    pid           INTEGER,                -- ffmpeg process id when live
    scheduled_at  REAL,                   -- unix ts for a planned start, or NULL
    created_at    REAL NOT NULL,
    shuffle       INTEGER DEFAULT 0,      -- 1 = random play enabled
    stream_type   TEXT DEFAULT 'video',   -- 'video' | 'music'
    loop_video_id INTEGER,                -- music: video row looped as the background
    mix_video_audio INTEGER DEFAULT 0,    -- music: mix the background video's own sound
    video_volume  REAL DEFAULT 0.5,       -- music: background video sound level (0..2)
    music_volume  REAL DEFAULT 1.0,       -- music: playlist audio level (0..2)
    stream_volume REAL DEFAULT 1.0,       -- video: playback audio level (0..2)
    quality_mode  TEXT DEFAULT 'balanced', -- 'quality' | 'balanced' | 'performance'
    last_error    TEXT DEFAULT ''          -- why the last session ended (survives restarts)
);

CREATE TABLE IF NOT EXISTS videos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id     INTEGER NOT NULL,
    orig_name     TEXT NOT NULL,
    stored_name   TEXT NOT NULL,          -- filename inside uploads/
    encoded_name  TEXT,                   -- filename inside encoded/ once ready
    status        TEXT DEFAULT 'waiting_encode',  -- waiting_encode|encoding|completed|error
    error_msg     TEXT DEFAULT '',
    duration      REAL DEFAULT 0,
    progress      REAL DEFAULT 0,
    encode_pid    INTEGER,
    position      INTEGER DEFAULT 0,      -- play order within the stream
    kind          TEXT DEFAULT 'video',   -- 'video' | 'audio' (music stream tracks)
    width         INTEGER,                -- source resolution (px), for the UI
    height        INTEGER,
    size          REAL,                   -- encoded file size (bytes)
    encode_preset TEXT DEFAULT 'balanced', -- quality mode this copy was made with
    prev_encoded_name TEXT,               -- previous-quality copy kept while re-encoding
    prev_encode_preset TEXT,
    created_at    REAL NOT NULL,
    FOREIGN KEY (stream_id) REFERENCES streams(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    note          TEXT DEFAULT '',           -- who this password belongs to
    role          TEXT DEFAULT 'worker',     -- 'admin' | 'worker' | 'viewer'
    password_hash TEXT NOT NULL,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS stream_access (
    user_id   INTEGER NOT NULL,
    stream_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, stream_id)
);

CREATE INDEX IF NOT EXISTS idx_videos_stream_status ON videos(stream_id, status);
CREATE INDEX IF NOT EXISTS idx_videos_stream_position ON videos(stream_id, position);
"""

def _connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def migrate_db():
    """Ensures all necessary columns exist in the database."""
    with get_db() as db:
        try:
            db.execute("ALTER TABLE videos ADD COLUMN progress REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE videos ADD COLUMN encode_pid INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE streams ADD COLUMN shuffle INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE streams ADD COLUMN stream_type TEXT DEFAULT 'video'")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE streams ADD COLUMN loop_video_id INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE streams ADD COLUMN mix_video_audio INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE streams ADD COLUMN video_volume REAL DEFAULT 0.5")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE streams ADD COLUMN music_volume REAL DEFAULT 1.0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE streams ADD COLUMN stream_volume REAL DEFAULT 1.0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE streams ADD COLUMN quality_mode TEXT DEFAULT 'balanced'")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE videos ADD COLUMN encode_preset TEXT DEFAULT 'balanced'")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE videos ADD COLUMN prev_encoded_name TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE videos ADD COLUMN prev_encode_preset TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE videos ADD COLUMN kind TEXT DEFAULT 'video'")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE videos ADD COLUMN width INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE videos ADD COLUMN height INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE videos ADD COLUMN size REAL")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE streams ADD COLUMN owner_id INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE streams ADD COLUMN last_error TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

def init_db():
    config.ensure_dirs()
    with get_db() as db:
        db.executescript(SCHEMA)
        migrate_db()

# --- Streams ----------------------------------------------------------------
def create_stream(name, rtmp_key="", youtube_url="", stream_type="video",
                  quality_mode="balanced", owner_id=None):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO streams (name, rtmp_key, youtube_url, stream_type, quality_mode, owner_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, rtmp_key, youtube_url, stream_type, quality_mode, owner_id, time.time()),
        )
        return cur.lastrowid

def get_stream(stream_id):
    with get_db() as db:
        return db.execute("SELECT * FROM streams WHERE id = ?", (stream_id,)).fetchone()

def list_streams():
    with get_db() as db:
        rows = db.execute("SELECT * FROM streams ORDER BY created_at DESC").fetchall()
        # Two grouped queries for all streams instead of per-stream aggregates.
        counts = {
            r["stream_id"]: r["c"]
            for r in db.execute(
                "SELECT stream_id, COUNT(*) c FROM videos GROUP BY stream_id"
            ).fetchall()
        }
        by_stream = {}
        for a in db.execute(
            "SELECT stream_id, kind, COALESCE(SUM(duration),0) dur, COALESCE(SUM(size),0) sz "
            "FROM videos GROUP BY stream_id, kind"
        ).fetchall():
            d = by_stream.setdefault(a["stream_id"], {})
            d[a["kind"] or "video"] = (a["dur"], a["sz"])
        result = []
        for r in rows:
            d = dict(r)
            kinds = by_stream.get(r["id"], {})
            d["video_count"] = counts.get(r["id"], 0)
            # Per-kind aggregates for the dashboard (runtime = bigger of the two).
            d["video_duration"] = kinds.get("video", (0, 0))[0]
            d["audio_duration"] = kinds.get("audio", (0, 0))[0]
            d["total_size"] = sum(sz for _, sz in kinds.values())
            result.append(d)
        return result

def update_stream(stream_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with get_db() as db:
        try:
            db.execute(
                f"UPDATE streams SET {cols} WHERE id = ?",
                (*fields.values(), stream_id),
            )
        except sqlite3.OperationalError:
            # Schema drifted (columns added by a newer version of the code) —
            # repair in place and retry instead of failing the save silently.
            migrate_db()
            db.execute(
                f"UPDATE streams SET {cols} WHERE id = ?",
                (*fields.values(), stream_id),
            )

def delete_stream(stream_id):
    with get_db() as db:
        db.execute("DELETE FROM streams WHERE id = ?", (stream_id,))
        db.execute("DELETE FROM stream_access WHERE stream_id = ?", (stream_id,))

def set_live(stream_id, is_live, pid=None):
    update_stream(stream_id, is_live=1 if is_live else 0, pid=pid)

# --- Videos -----------------------------------------------------------------
def add_video(stream_id, orig_name, stored_name, kind="video"):
    with get_db() as db:
        pos = db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 p FROM videos WHERE stream_id = ?",
            (stream_id,),
        ).fetchone()["p"]
        cur = db.execute(
            "INSERT INTO videos (stream_id, orig_name, stored_name, kind, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (stream_id, orig_name, stored_name, kind, pos, time.time()),
        )
        return cur.lastrowid

def get_video(video_id):
    with get_db() as db:
        return db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()

def list_videos(stream_id):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM videos WHERE stream_id = ? ORDER BY position, id",
            (stream_id,),
        ).fetchall()

def list_all_videos():
    with get_db() as db:
        return db.execute("SELECT * FROM videos").fetchall()

def list_ready_videos(stream_id, kind=None):
    """Completed files in play order — this is the actual 24/7 playlist."""
    query = ("SELECT * FROM videos WHERE stream_id = ? AND status = 'completed'")
    args = [stream_id]
    if kind:
        query += " AND kind = ?"
        args.append(kind)
    query += " ORDER BY position, id"
    with get_db() as db:
        return db.execute(query, args).fetchall()

def stream_totals(stream_id):
    """(video_duration, audio_duration, total_size) aggregates for a stream."""
    with get_db() as db:
        rows = db.execute(
            "SELECT kind, COALESCE(SUM(duration),0) dur, COALESCE(SUM(size),0) sz "
            "FROM videos WHERE stream_id = ? GROUP BY kind",
            (stream_id,),
        ).fetchall()
    by = {(r["kind"] or "video"): (r["dur"], r["sz"]) for r in rows}
    total_size = sum(sz for _, sz in by.values())
    return by.get("video", (0, 0))[0], by.get("audio", (0, 0))[0], total_size


def update_video(video_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with get_db() as db:
        db.execute(
            f"UPDATE videos SET {cols} WHERE id = ?",
            (*fields.values(), video_id),
        )

def delete_video(video_id):
    with get_db() as db:
        db.execute("DELETE FROM videos WHERE id = ?", (video_id,))

def count_encoding_videos():
    """Files currently queued for / being normalized (dashboard chip)."""
    with get_db() as db:
        return db.execute(
            "SELECT COUNT(*) c FROM videos WHERE status IN ('waiting_encode', 'encoding')"
        ).fetchone()["c"]

def reorder_videos(stream_id, ordered_ids):
    with get_db() as db:
        for pos, vid in enumerate(ordered_ids):
            db.execute(
                "UPDATE videos SET position = ? WHERE id = ? AND stream_id = ?",
                (pos, vid, stream_id),
            )

def next_encode_job():
    """Oldest video still waiting to be normalized."""
    with get_db() as db:
        return db.execute(
            "SELECT * FROM videos WHERE status = 'waiting_encode' "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
# --- Users (worker accounts) -------------------------------------------------
def create_user(note, role, password_hash):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO users (note, role, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (note, role, password_hash, time.time()),
        )
        return cur.lastrowid

def list_users():
    with get_db() as db:
        return db.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()

def get_user(user_id):
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

def update_user_password(user_id, password_hash):
    with get_db() as db:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (password_hash, user_id))

def update_user(user_id, note=None, role=None):
    with get_db() as db:
        if note is not None:
            db.execute("UPDATE users SET note = ? WHERE id = ?", (note, user_id))
        if role is not None:
            db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))

def delete_user(user_id):
    with get_db() as db:
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))

def find_user_by_password(password, check_hash):
    """Password-only login: try the password against every account hash."""
    for u in list_users():
        if check_hash(u["password_hash"], password):
            return u
    return None

# --- Settings (key-value) ----------------------------------------------------
def get_setting(key, default=None):
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key, value):
    with get_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

# --- Shared stream access (admin grants workers extra streams) ---------------
def list_shared_stream_ids(user_id):
    with get_db() as db:
        return [r["stream_id"] for r in db.execute(
            "SELECT stream_id FROM stream_access WHERE user_id = ?", (user_id,))]

def set_shared_streams(user_id, stream_ids):
    with get_db() as db:
        db.execute("DELETE FROM stream_access WHERE user_id = ?", (user_id,))
        for sid in stream_ids:
            db.execute(
                "INSERT OR IGNORE INTO stream_access (user_id, stream_id) VALUES (?, ?)",
                (user_id, int(sid)),
            )
