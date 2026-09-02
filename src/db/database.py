"""SQLite database management for BossMatch."""

import os
import sqlite3
import logging

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS identity (
    id INTEGER PRIMARY KEY CHECK (id=1),
    mode TEXT NOT NULL CHECK (mode IN ('geek','boss')) DEFAULT 'geek',
    api_base_url TEXT DEFAULT 'https://api.openai.com/v1',
    api_key TEXT DEFAULT '',
    api_model TEXT DEFAULT 'gpt-4o',
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS geek_resume (
    id INTEGER PRIMARY KEY CHECK (id=1),
    content TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS boss_job_desc (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT DEFAULT '',
    content TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scraped_job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    identity TEXT NOT NULL CHECK (identity IN ('geek','boss')),
    title TEXT DEFAULT '',
    salary TEXT DEFAULT '',
    salary_source TEXT DEFAULT '',
    location TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    boss_name TEXT DEFAULT '',
    boss_active_status TEXT DEFAULT '',
    company_scale TEXT DEFAULT '',
    company_stage TEXT DEFAULT '',
    company_industry TEXT DEFAULT '',
    skills TEXT DEFAULT '',
    job_labels TEXT DEFAULT '',
    welfare TEXT DEFAULT '',
    job_link TEXT DEFAULT '',
    company_link TEXT DEFAULT '',
    search_keyword TEXT DEFAULT '',
    search_city TEXT DEFAULT '',
    scraped_at TEXT DEFAULT (datetime('now')),
    UNIQUE(job_id, identity)
);

CREATE TABLE IF NOT EXISTS scraped_detail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    identity TEXT NOT NULL,
    title TEXT DEFAULT '',
    company TEXT DEFAULT '',
    salary TEXT DEFAULT '',
    location TEXT DEFAULT '',
    boss_active_status TEXT DEFAULT '',
    tags_list TEXT DEFAULT '',
    skill_tags TEXT DEFAULT '[]',
    jd TEXT DEFAULT '',
    scraped_at TEXT DEFAULT (datetime('now')),
    UNIQUE(job_id, identity)
);

CREATE TABLE IF NOT EXISTS match_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    target_job_id TEXT NOT NULL,
    score REAL DEFAULT 0,
    reasoning TEXT DEFAULT '',
    suggestions TEXT DEFAULT '[]',
    model_name TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(identity, source_id, target_job_id)
);

CREATE TABLE IF NOT EXISTS chrome_state (
    id INTEGER PRIMARY KEY CHECK (id=1),
    geek_cdp_port INTEGER DEFAULT 9222,
    boss_cdp_port INTEGER DEFAULT 9223,
    geek_logged_in INTEGER DEFAULT 0,
    boss_logged_in INTEGER DEFAULT 0,
    geek_login_checked_at TEXT DEFAULT '',
    boss_login_checked_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scrape_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    keyword TEXT DEFAULT '',
    city TEXT DEFAULT '',
    items_scraped INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_scraped_job_identity ON scraped_job(identity);
CREATE INDEX IF NOT EXISTS idx_scraped_detail_job_id ON scraped_detail(job_id);
CREATE INDEX IF NOT EXISTS idx_match_result_identity_source ON match_result(identity, source_id);

CREATE TABLE IF NOT EXISTS geek_resume_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    summary TEXT DEFAULT '',
    is_active INTEGER DEFAULT 0,
    chunk_count INTEGER DEFAULT 0,
    file_source TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS match_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL DEFAULT 'geek',
    source_id INTEGER NOT NULL DEFAULT 1,
    structured TEXT,
    raw_text TEXT,
    model_name TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(identity, source_id)
);

CREATE TABLE IF NOT EXISTS resume_optimization (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    job_title TEXT DEFAULT '',
    company_name TEXT DEFAULT '',
    original_content TEXT NOT NULL DEFAULT '',
    optimized_content TEXT NOT NULL DEFAULT '',
    diff_sections TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    model_name TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (resume_id) REFERENCES geek_resume_list(id),
    UNIQUE(resume_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_resume_optimization_resume_id ON resume_optimization(resume_id);
CREATE INDEX IF NOT EXISTS idx_resume_optimization_job_id ON resume_optimization(job_id);

CREATE TABLE IF NOT EXISTS interview_prep (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    job_title TEXT DEFAULT '',
    company_name TEXT DEFAULT '',
    questions TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    model_name TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (resume_id) REFERENCES geek_resume_list(id),
    UNIQUE(resume_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_interview_prep_resume_id ON interview_prep(resume_id);
CREATE INDEX IF NOT EXISTS idx_interview_prep_job_id ON interview_prep(job_id);
"""


class Database:
    def __init__(self, db_path=None):
        if db_path is None:
            base_dir = os.path.expanduser("~/.boss-match")
            os.makedirs(base_dir, exist_ok=True)
            db_path = os.path.join(base_dir, "boss-match.db")
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._conn()
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
            self._migrate(conn)
        finally:
            conn.close()

    def _migrate(self, conn):
        """Run schema migrations for existing databases."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chrome_state)").fetchall()}
        if "geek_login_checked_at" not in cols:
            conn.execute("ALTER TABLE chrome_state ADD COLUMN geek_login_checked_at TEXT DEFAULT ''")
        if "boss_login_checked_at" not in cols:
            conn.execute("ALTER TABLE chrome_state ADD COLUMN boss_login_checked_at TEXT DEFAULT ''")

        match_cols = {r[1] for r in conn.execute("PRAGMA table_info(match_result)").fetchall()}
        if "evidence" not in match_cols:
            conn.execute("ALTER TABLE match_result ADD COLUMN evidence TEXT")
        if "gaps" not in match_cols:
            conn.execute("ALTER TABLE match_result ADD COLUMN gaps TEXT")
        if "retrieved_chunks" not in match_cols:
            conn.execute("ALTER TABLE match_result ADD COLUMN retrieved_chunks TEXT")

        # Migrate geek_resume -> geek_resume_list
        new_cols = {r[1] for r in conn.execute("PRAGMA table_info(geek_resume_list)").fetchall()}
        if not new_cols:
            pass
        try:
            old_row = conn.execute("SELECT content FROM geek_resume WHERE id=1").fetchone()
            new_count = conn.execute("SELECT COUNT(*) FROM geek_resume_list").fetchone()[0]
            if old_row and old_row["content"] and new_count == 0:
                content = old_row["content"]
                summary = content[:100].replace("\n", " ") if len(content) > 100 else content.replace("\n", " ")
                conn.execute(
                    """INSERT INTO geek_resume_list (name, content, summary, is_active, chunk_count, file_source)
                       VALUES ('我的简历', ?, ?, 1, 0, 'migrated')""",
                    (content, summary),
                )
        except Exception:
            pass

        # Ensure resume_optimization has UNIQUE constraint (recreate if missing)
        try:
            idx_rows = conn.execute("PRAGMA index_list(resume_optimization)").fetchall()
            has_unique = any("UNIQUE" in str(r) or "unique" in str(r) for r in idx_rows)
            if not has_unique and len(idx_rows) >= 0:
                # Table exists but lacks UNIQUE(resume_id, job_id) — migrate data
                conn.execute("ALTER TABLE resume_optimization RENAME TO resume_optimization_old")
                conn.execute("""CREATE TABLE IF NOT EXISTS resume_optimization (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resume_id INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    job_title TEXT DEFAULT '',
                    company_name TEXT DEFAULT '',
                    original_content TEXT NOT NULL DEFAULT '',
                    optimized_content TEXT NOT NULL DEFAULT '',
                    diff_sections TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'pending',
                    model_name TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (resume_id) REFERENCES geek_resume_list(id),
                    UNIQUE(resume_id, job_id)
                )""")
                conn.execute("""INSERT OR IGNORE INTO resume_optimization
                    SELECT id, resume_id, job_id, job_title, company_name,
                           original_content, optimized_content, diff_sections,
                           status, model_name, error_message, created_at
                    FROM resume_optimization_old""")
                conn.execute("DROP TABLE resume_optimization_old")
        except Exception:
            pass  # Table may not exist yet (first run)

        conn.commit()

    def get_identity(self):
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM identity WHERE id=1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def upsert_identity(self, mode="geek", api_base_url="", api_key="", api_model="gpt-4o"):
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO identity (id, mode, api_base_url, api_key, api_model)
                   VALUES (1, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     mode=excluded.mode, api_base_url=excluded.api_base_url,
                     api_key=excluded.api_key, api_model=excluded.api_model,
                     updated_at=datetime('now')""",
                (mode, api_base_url, api_key, api_model),
            )
            conn.commit()
        finally:
            conn.close()
