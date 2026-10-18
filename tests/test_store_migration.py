import sqlite3

from autoyt.store import Store


def test_private_upload_records_survive_v1_to_v2_migration(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE schema_version(version INTEGER PRIMARY KEY);
            INSERT INTO schema_version VALUES(1);
            CREATE TABLE video_jobs(id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, state TEXT,
                stage INTEGER DEFAULT 0, topic TEXT, error TEXT, retries INTEGER DEFAULT 0,
                dry_run INTEGER, slot TEXT UNIQUE, content_hash TEXT UNIQUE);
            INSERT INTO video_jobs(id,created_at,updated_at,state,dry_run)
                VALUES('original','2026-10-18','2026-10-18','AWAITING_REVIEW',0);
            CREATE TABLE youtube_uploads(job_id TEXT PRIMARY KEY REFERENCES video_jobs(id),
                video_id TEXT UNIQUE NOT NULL, privacy TEXT NOT NULL CHECK(privacy='private'),
                uploaded_at TEXT NOT NULL);
            INSERT INTO youtube_uploads VALUES('original','original-video','private','2026-10-18');
        """)
    store = Store(path)
    with store.connect() as db:
        assert tuple(db.execute("SELECT * FROM youtube_uploads").fetchone()) == (
            "original", "original-video", "private", "2026-10-18")
        assert db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 2
    job = store.create()
    store.confirm_upload(job, "new-video", privacy="public")
    Store(path)  # Opening again must not redo or lose the migration.
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM youtube_uploads").fetchone()[0] == 2
