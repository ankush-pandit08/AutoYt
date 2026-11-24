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
                db.execute("CREATE TABLE youtube_uploads_v2("
                           "job_id TEXT PRIMARY KEY REFERENCES video_jobs(id), video_id TEXT UNIQUE NOT NULL, "
                           "privacy TEXT NOT NULL CHECK(privacy IN ('private','public')), uploaded_at TEXT NOT NULL)")
                db.execute("INSERT INTO youtube_uploads_v2 SELECT * FROM youtube_uploads")
                db.execute("DROP TABLE youtube_uploads")
                db.execute("ALTER TABLE youtube_uploads_v2 RENAME TO youtube_uploads")
                db.execute("INSERT OR IGNORE INTO schema_version VALUES(2)")
            if db.execute("SELECT max(version) FROM schema_version").fetchone()[0] < 3:
                # Migration 3: Podcast platform tables
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS podcast_sources (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        show_name TEXT NOT NULL,
                        youtube_channel_id TEXT,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        config_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS podcast_episodes (
                        id TEXT PRIMARY KEY,
                        source_id TEXT NOT NULL,
                        video_id TEXT NOT NULL,
                        video_url TEXT NOT NULL,
                        title TEXT NOT NULL,
                        channel_name TEXT NOT NULL DEFAULT '',
                        published_at TEXT NOT NULL,
                        duration_seconds INTEGER,
                        thumbnail_url TEXT,
                        description TEXT,
                        discovery_score REAL,
                        topics_matched TEXT,
                        status TEXT NOT NULL DEFAULT 'discovered',
                        rights_status TEXT NOT NULL DEFAULT 'unknown',
                        rights_basis TEXT,
                        rights_notes TEXT,
                        rights_verified_at TEXT,
                        discovered_at TEXT NOT NULL,
                        processed_at TEXT,
                        error_message TEXT,
                        UNIQUE(source_id, video_id)
                    );
                    CREATE TABLE IF NOT EXISTS podcast_clips (
                        id TEXT PRIMARY KEY,
                        episode_id TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        clip_index INTEGER NOT NULL,
                        start_seconds REAL NOT NULL,
                        end_seconds REAL NOT NULL,
                        duration_seconds REAL NOT NULL,
                        transcript_text TEXT,
                        ai_score REAL,
                        score_breakdown TEXT,
                        generated_title TEXT,
                        generated_description TEXT,
                        hashtags TEXT,
                        status TEXT NOT NULL DEFAULT 'candidate',
                        output_path TEXT,
                        thumbnail_path TEXT,
                        youtube_video_id TEXT,
                        youtube_url TEXT,
                        upload_status TEXT,
                        review_notes TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(episode_id) REFERENCES podcast_episodes(id)
                    );
                ''')
                db.execute("INSERT OR IGNORE INTO schema_version VALUES(3)")

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

    def create(self, dry_run=False, topic=None, slot=None, limit=6, tz=None):
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz or "Asia/Kolkata")
        today = datetime.now(zone).date()
        job_id = uuid.uuid4().hex[:16]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if slot and db.execute("SELECT 1 FROM scheduled_runs WHERE slot=?", (slot,)).fetchone():
                return None
            count = sum(datetime.fromisoformat(r[0]).astimezone(zone).date() == today
                        for r in db.execute("SELECT created_at FROM video_jobs WHERE dry_run=0 AND slot IS NOT NULL"))
            if slot and not dry_run and count >= limit:
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

    def jobs(self, limit=100):
        with self.connect() as db:
            query = "SELECT * FROM video_jobs ORDER BY created_at DESC"
            if limit is None:
                return [dict(r) for r in db.execute(query)]
            return [dict(r) for r in db.execute(query + " LIMIT ?", (limit,))]

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

    def recent_topics(self, days, visibility=None):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            return [r[0] for r in db.execute(
                "SELECT t.title FROM topics t JOIN video_jobs j ON j.id=t.job_id "
                "WHERE t.created_at>? AND j.dry_run=0 AND (?!='public' OR j.state!='AWAITING_REVIEW') "
                "AND (j.state NOT IN ('FAILED','SKIPPED') "
                "OR EXISTS(SELECT 1 FROM upload_attempts u WHERE u.job_id=j.id))", (cutoff, visibility or "private"))]

    def recent_research_rejections(self, hours=6):
        """Temporarily avoid repeatedly checking stories with insufficient evidence in new runs."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.connect() as db:
            rows = db.execute("SELECT a.payload FROM artifacts a JOIN video_jobs j ON a.job_id=j.id "
                              "WHERE a.kind='research_attempts' AND j.updated_at>? AND j.dry_run=0",
                              (cutoff,))
            return list(dict.fromkeys(attempt["topic"] for row in rows for attempt in json.loads(row[0])
                                      if isinstance(attempt, dict) and attempt.get("topic")))

    def rejected_topic_history(self, excluding=""):
        """New runs never automatically revisit skipped/failed topics, including previews."""
        titles, urls = set(), set()
        def remember(value):
            if isinstance(value, str):
                if value.strip():
                    titles.add(value)
            elif isinstance(value, dict):
                remember(value.get("title") or value.get("topic"))
                if value.get("url"):
                    urls.add(value["url"])
        with self.connect() as db:
            for row in db.execute("SELECT topic FROM video_jobs WHERE state IN ('FAILED','SKIPPED') AND id!=?",
                                  (excluding,)):
                remember(row[0])
            for row in db.execute("SELECT a.kind,a.payload FROM artifacts a JOIN video_jobs j ON j.id=a.job_id "
                                  "WHERE j.id!=? AND (a.kind='research_attempts' OR "
                                  "(a.kind='topic' AND j.state IN ('FAILED','SKIPPED')))", (excluding,)):
                value = json.loads(row["payload"])
                for item in value if row["kind"] == "research_attempts" else [value]:
                    remember(item)
        return {"titles": sorted(titles), "urls": sorted(urls)}

    def recent_scripts(self, days, excluding, visibility=None):
        return self.recent_artifacts("script", days, excluding, visibility)

    def recent_research(self, days, excluding="", visibility=None):
        return self.recent_artifacts("research", days, excluding, visibility)

    def recent_artifacts(self, kind, days, excluding, visibility=None):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            rows = db.execute("SELECT a.payload FROM artifacts a JOIN video_jobs j ON a.job_id=j.id "
                              "WHERE a.kind=? AND j.created_at>? AND j.id!=? AND j.dry_run=0 "
                              "AND (?!='public' OR j.state!='AWAITING_REVIEW') "
                              "AND (j.state NOT IN ('FAILED','SKIPPED') OR EXISTS(SELECT 1 FROM "
                              "upload_attempts u WHERE u.job_id=j.id))", (kind, cutoff, excluding, visibility or "private"))
            return [json.loads(row[0]) for row in rows]

    def reserve_topic(self, job_id, title, fingerprint, visibility=None, days=14):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT fingerprint FROM topics WHERE job_id=?", (job_id,)).fetchone()
            if existing and existing[0] == fingerprint:
                return
            owner = db.execute("SELECT j.id,j.state,j.dry_run,t.created_at FROM topics t JOIN video_jobs j "
                               "ON j.id=t.job_id WHERE t.fingerprint=?", (fingerprint,)).fetchone()
            if owner:
                attempted = db.execute("SELECT 1 FROM upload_attempts WHERE job_id=?", (owner["id"],)).fetchone()
                private_test = visibility == "public" and owner["state"] == "AWAITING_REVIEW" and db.execute(
                    "SELECT 1 FROM youtube_uploads WHERE job_id=? AND privacy='private'", (owner["id"],)).fetchone()
                # Only completed publications expire. Active or ambiguous uploads keep their reservation.
                expired = owner["created_at"] <= cutoff and owner["state"] in ("PUBLISHED", "AWAITING_REVIEW")
                if not (private_test or expired) and (
                        attempted or (not owner["dry_run"] and owner["state"] not in ("FAILED", "SKIPPED"))):
                    raise sqlite3.IntegrityError("Topic reserved by a live or uploaded job")
                db.execute("DELETE FROM topics WHERE fingerprint=?", (fingerprint,))
            if existing:
                if db.execute("SELECT 1 FROM upload_attempts WHERE job_id=?", (job_id,)).fetchone():
                    raise ValueError("Cannot change the topic of an attempted upload")
                db.execute("DELETE FROM topics WHERE job_id=?", (job_id,))
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

    def daily_upload_count(self, tz, scheduled_only=False):
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz)
        today = datetime.now(zone).date()
        with self.connect() as db:
            query = "SELECT u.uploaded_at FROM youtube_uploads u JOIN video_jobs j ON j.id=u.job_id"
            if scheduled_only:
                query += " WHERE j.slot IS NOT NULL"
            return sum(datetime.fromisoformat(row[0]).astimezone(zone).date() == today
                       for row in db.execute(query))


    # -------------------------------------------------------------------------
    # Podcast platform — episode helpers
    # -------------------------------------------------------------------------

    def save_podcast_episode(self, episode: dict) -> None:
        """Insert or replace a podcast episode record."""
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO podcast_episodes("
                "id, source_id, video_id, video_url, title, channel_name, published_at, "
                "duration_seconds, thumbnail_url, description, discovery_score, topics_matched, "
                "status, rights_status, rights_basis, rights_notes, rights_verified_at, "
                "discovered_at, processed_at, error_message"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    episode["id"],
                    episode["source_id"],
                    episode["video_id"],
                    episode.get("video_url", ""),
                    episode.get("title", ""),
                    episode.get("channel_name", ""),
                    episode.get("published_at", now()),
                    episode.get("duration_seconds"),
                    episode.get("thumbnail_url"),
                    episode.get("description"),
                    episode.get("discovery_score"),
                    json.dumps(episode.get("topics_matched") or [], ensure_ascii=False),
                    episode.get("status", "discovered"),
                    episode.get("rights_status", "unknown"),
                    episode.get("rights_basis"),
                    episode.get("rights_notes"),
                    episode.get("rights_verified_at"),
                    episode.get("discovered_at", now()),
                    episode.get("processed_at"),
                    episode.get("error_message"),
                ),
            )

    def get_podcast_episode(self, episode_id: str) -> dict | None:
        """Return a single podcast episode by its ID, or None."""
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM podcast_episodes WHERE id=?", (episode_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["topics_matched"] = json.loads(d.get("topics_matched") or "[]")
        return d

    def list_podcast_episodes(
        self,
        status: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List podcast episodes with optional filters."""
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if source_id:
            clauses.append("source_id=?")
            params.append(source_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM podcast_episodes {where} "
                "ORDER BY discovery_score DESC, discovered_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["topics_matched"] = json.loads(d.get("topics_matched") or "[]")
            result.append(d)
        return result

    def update_episode_status(self, episode_id: str, status: str, **kwargs) -> None:
        """Update status and optional fields on a podcast episode."""
        allowed = {
            "rights_status", "rights_basis", "rights_notes", "rights_verified_at",
            "processed_at", "error_message", "discovery_score",
        }
        extra = set(kwargs) - allowed
        if extra:
            raise ValueError(f"Unknown episode fields: {extra}")
        sets = "status=?" + "".join(f",{k}=?" for k in kwargs)
        with self.connect() as db:
            db.execute(
                f"UPDATE podcast_episodes SET {sets} WHERE id=?",
                (status, *kwargs.values(), episode_id),
            )

    # -------------------------------------------------------------------------
    # Podcast platform — clip helpers
    # -------------------------------------------------------------------------

    def save_podcast_clip(self, clip: dict) -> None:
        """Insert or replace a podcast clip record."""
        ts = now()
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO podcast_clips("
                "id, episode_id, source_id, clip_index, start_seconds, end_seconds, "
                "duration_seconds, transcript_text, ai_score, score_breakdown, "
                "generated_title, generated_description, hashtags, status, "
                "output_path, thumbnail_path, youtube_video_id, youtube_url, "
                "upload_status, review_notes, created_at, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    clip["id"],
                    clip["episode_id"],
                    clip["source_id"],
                    clip["clip_index"],
                    clip["start_seconds"],
                    clip["end_seconds"],
                    clip["duration_seconds"],
                    clip.get("transcript_text"),
                    clip.get("ai_score"),
                    json.dumps(clip.get("score_breakdown") or {}, ensure_ascii=False),
                    clip.get("generated_title"),
                    clip.get("generated_description"),
                    json.dumps(clip.get("hashtags") or [], ensure_ascii=False),
                    clip.get("status", "candidate"),
                    clip.get("output_path"),
                    clip.get("thumbnail_path"),
                    clip.get("youtube_video_id"),
                    clip.get("youtube_url"),
                    clip.get("upload_status"),
                    clip.get("review_notes"),
                    clip.get("created_at", ts),
                    clip.get("updated_at", ts),
                ),
            )

    def get_podcast_clip(self, clip_id: str) -> dict | None:
        """Return a single podcast clip by its ID, or None."""
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM podcast_clips WHERE id=?", (clip_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["score_breakdown"] = json.loads(d.get("score_breakdown") or "{}")
        d["hashtags"] = json.loads(d.get("hashtags") or "[]")
        return d

    def list_podcast_clips(
        self,
        episode_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List podcast clips with optional filters."""
        clauses, params = [], []
        if episode_id:
            clauses.append("episode_id=?")
            params.append(episode_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM podcast_clips {where} "
                "ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["score_breakdown"] = json.loads(d.get("score_breakdown") or "{}")
            d["hashtags"] = json.loads(d.get("hashtags") or "[]")
            result.append(d)
        return result

    def update_clip_status(self, clip_id: str, status: str, **kwargs) -> None:
        """Update status and optional fields on a podcast clip."""
        allowed = {
            "output_path", "thumbnail_path", "youtube_video_id", "youtube_url",
            "upload_status", "review_notes", "ai_score",
        }
        extra = set(kwargs) - allowed
        if extra:
            raise ValueError(f"Unknown clip fields: {extra}")
        sets = "status=?,updated_at=?" + "".join(f",{k}=?" for k in kwargs)
        with self.connect() as db:
            db.execute(
                f"UPDATE podcast_clips SET {sets} WHERE id=?",
                (status, now(), *kwargs.values(), clip_id),
            )
