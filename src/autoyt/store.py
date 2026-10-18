import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write_json(path: Path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_version VALUES(1);
                CREATE TABLE IF NOT EXISTS video_jobs(
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    state TEXT NOT NULL, stage INTEGER NOT NULL DEFAULT 0, topic TEXT,
                    error TEXT, retries INTEGER NOT NULL DEFAULT 0, dry_run INTEGER NOT NULL,
                    slot TEXT UNIQUE, content_hash TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS artifacts(
                    job_id TEXT NOT NULL REFERENCES video_jobs(id), kind TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(job_id,kind));
                CREATE TABLE IF NOT EXISTS topics(
                    fingerprint TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                    job_id TEXT UNIQUE REFERENCES video_jobs(id));
                CREATE TABLE IF NOT EXISTS scheduled_runs(slot TEXT PRIMARY KEY, job_id TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS upload_attempts(
                    job_id TEXT PRIMARY KEY REFERENCES video_jobs(id), state TEXT NOT NULL,
                    session_uri TEXT, progress INTEGER NOT NULL DEFAULT 0, video_id TEXT,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS youtube_uploads(
                    job_id TEXT PRIMARY KEY REFERENCES video_jobs(id), video_id TEXT UNIQUE NOT NULL,
                    privacy TEXT NOT NULL CHECK(privacy IN ('private','public')), uploaded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS application_events(
                    id INTEGER PRIMARY KEY, job_id TEXT, created_at TEXT NOT NULL,
                    state TEXT NOT NULL, message TEXT NOT NULL);
            ''')
            if db.execute("SELECT max(version) FROM schema_version").fetchone()[0] < 2:
                # Transactional SQLite table rebuild preserves existing private upload records.
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE youtube_uploads_v2("
                           "job_id TEXT PRIMARY KEY REFERENCES video_jobs(id), video_id TEXT UNIQUE NOT NULL, "
                           "privacy TEXT NOT NULL CHECK(privacy IN ('private','public')), uploaded_at TEXT NOT NULL)")
                db.execute("INSERT INTO youtube_uploads_v2 SELECT * FROM youtube_uploads")
                db.execute("DROP TABLE youtube_uploads")
                db.execute("ALTER TABLE youtube_uploads_v2 RENAME TO youtube_uploads")
                db.execute("INSERT OR IGNORE INTO schema_version VALUES(2)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, dry_run=False, topic=None, slot=None, limit=3, tz=None):
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz or "Asia/Kolkata")
        today = datetime.now(zone).date()
        job_id = uuid.uuid4().hex[:16]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if slot and db.execute("SELECT 1 FROM scheduled_runs WHERE slot=?", (slot,)).fetchone():
                return None
            count = sum(datetime.fromisoformat(r[0]).astimezone(zone).date() == today
                        for r in db.execute("SELECT created_at FROM video_jobs WHERE dry_run=0"))
            if not dry_run and count >= limit:
                return None
            db.execute("INSERT INTO video_jobs(id,created_at,updated_at,state,dry_run,topic,slot) "
                       "VALUES(?,?,?,'CREATED',?,?,?)", (job_id, now(), now(), dry_run, topic, slot))
            if slot:
                db.execute("INSERT INTO scheduled_runs VALUES(?,?)", (slot, job_id))
        return job_id

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown job ID")
        return dict(row)

    def jobs(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM video_jobs ORDER BY created_at DESC LIMIT 100")]

    def update(self, job_id, state, **fields):
        if set(fields) - {"stage", "topic", "error", "retries", "content_hash"}:
            raise ValueError("Unknown job field")
        with self.connect() as db:
            db.execute("UPDATE video_jobs SET state=?,updated_at=?" + "".join(f",{k}=?" for k in fields)
                       + " WHERE id=?", (state, now(), *fields.values(), job_id))
            db.execute("INSERT INTO application_events(job_id,created_at,state,message) VALUES(?,?,?,?)",
                       (job_id, now(), state, fields.get("error", "") or ""))

    def save(self, job_id, kind, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO artifacts VALUES(?,?,?)",
                       (job_id, kind, json.dumps(value, default=str)))

    def load(self, job_id, kind):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM artifacts WHERE job_id=? AND kind=?", (job_id, kind)).fetchone()
        return json.loads(row[0]) if row else None

    def recent_topics(self, days):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            return [r[0] for r in db.execute("SELECT title FROM topics WHERE created_at>?", (cutoff,))]

    def recent_scripts(self, days, excluding):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            rows = db.execute("SELECT a.payload FROM artifacts a JOIN video_jobs j ON a.job_id=j.id "
                              "WHERE a.kind='script' AND j.created_at>? AND j.id!=?", (cutoff, excluding))
            return [json.loads(row[0]) for row in rows]

    def reserve_topic(self, job_id, title, fingerprint):
        with self.connect() as db:
            existing = db.execute("SELECT fingerprint FROM topics WHERE job_id=?", (job_id,)).fetchone()
            if existing and existing[0] == fingerprint:
                return
            db.execute("INSERT INTO topics VALUES(?,?,?,?)", (fingerprint, title, now(), job_id))

    def attempt(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM upload_attempts WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def upload_progress(self, job_id, state, session_uri=None, progress=0, video_id=None):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO upload_attempts VALUES(?,?,?,?,?,?)",
                       (job_id, state, session_uri, progress, video_id, now()))

    def confirm_upload(self, job_id, video_id, privacy="private"):
        if privacy not in ("private", "public"):
            raise ValueError("Unsupported upload visibility")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO youtube_uploads VALUES(?,?,?,?)",
                       (job_id, video_id, privacy, now()))
