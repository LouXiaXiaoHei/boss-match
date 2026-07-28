"""CRUD repository for BossMatch SQLite database."""

import json
import logging

from src.db.database import Database

log = logging.getLogger(__name__)


class Repository:
    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    # ---- Identity ----

    def get_identity(self) -> dict | None:
        return self.db.get_identity()

    def upsert_identity(self, mode="geek", api_base_url="", api_key="", api_model="gpt-4o"):
        self.db.upsert_identity(mode, api_base_url, api_key, api_model)

    # ---- Geek Resume ----

    def get_resume(self) -> str:
        conn = self.db._conn()
        try:
            row = conn.execute("SELECT content FROM geek_resume WHERE id=1").fetchone()
            return row["content"] if row else ""
        finally:
            conn.close()

    def save_resume(self, content: str):
        conn = self.db._conn()
        try:
            conn.execute(
                """INSERT INTO geek_resume (id, content) VALUES (1, ?)
                   ON CONFLICT(id) DO UPDATE SET content=excluded.content, updated_at=datetime('now')""",
                (content,),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- Boss Job Desc ----

    def list_job_descs(self, active_only: bool = True) -> list[dict]:
        conn = self.db._conn()
        try:
            sql = "SELECT * FROM boss_job_desc"
            if active_only:
                sql += " WHERE is_active=1"
            sql += " ORDER BY created_at DESC"
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_job_desc(self, jd_id: int) -> dict | None:
        conn = self.db._conn()
        try:
            row = conn.execute("SELECT * FROM boss_job_desc WHERE id=?", (jd_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def save_job_desc(self, title: str, content: str) -> int:
        conn = self.db._conn()
        try:
            cur = conn.execute(
                "INSERT INTO boss_job_desc (title, content) VALUES (?, ?)",
                (title, content),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def update_job_desc(self, jd_id: int, title: str = None, content: str = None, is_active: int = None):
        conn = self.db._conn()
        try:
            sets = []
            params = []
            if title is not None:
                sets.append("title=?")
                params.append(title)
            if content is not None:
                sets.append("content=?")
                params.append(content)
            if is_active is not None:
                sets.append("is_active=?")
                params.append(is_active)
            if not sets:
                return
            params.append(jd_id)
            conn.execute(f"UPDATE boss_job_desc SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
        finally:
            conn.close()

    # ---- Scraped Jobs ----

    def save_scraped_job_single(self, job: dict, identity: str, keyword: str = "", city: str = ""):
        """Save a single scraped job (for incremental/progressive saving)."""
        conn = self.db._conn()
        try:
            conn.execute(
                """INSERT INTO scraped_job
                   (job_id, identity, title, salary, salary_source, location, tags,
                    boss_name, boss_active_status, company_scale, company_stage,
                    company_industry, skills, job_labels, welfare, job_link,
                    company_link, search_keyword, search_city)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(job_id, identity) DO UPDATE SET
                     title=excluded.title, salary=excluded.salary,
                     salary_source=excluded.salary_source, location=excluded.location,
                     tags=excluded.tags, boss_name=excluded.boss_name,
                     boss_active_status=excluded.boss_active_status,
                     company_scale=excluded.company_scale,
                     company_stage=excluded.company_stage,
                     company_industry=excluded.company_industry,
                     skills=excluded.skills, job_labels=excluded.job_labels,
                     welfare=excluded.welfare, job_link=excluded.job_link,
                     company_link=excluded.company_link,
                     search_keyword=excluded.search_keyword,
                     search_city=excluded.search_city,
                     scraped_at=datetime('now')""",
                (
                    job.get("job_id", ""),
                    identity,
                    job.get("title", ""),
                    job.get("salary", ""),
                    job.get("salary_source", ""),
                    job.get("location", ""),
                    job.get("tags", ""),
                    job.get("boss_name", ""),
                    job.get("boss_active_status", ""),
                    job.get("company_scale", ""),
                    job.get("company_stage", ""),
                    job.get("company_industry", ""),
                    job.get("skills", ""),
                    job.get("job_labels", ""),
                    job.get("welfare", ""),
                    job.get("job_link", ""),
                    job.get("company_link", ""),
                    keyword,
                    city,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def save_scraped_jobs(self, jobs: list[dict], identity: str, keyword: str = "", city: str = ""):
        conn = self.db._conn()
        try:
            for job in jobs:
                conn.execute(
                    """INSERT INTO scraped_job
                       (job_id, identity, title, salary, salary_source, location, tags,
                        boss_name, boss_active_status, company_scale, company_stage,
                        company_industry, skills, job_labels, welfare, job_link,
                        company_link, search_keyword, search_city)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(job_id, identity) DO UPDATE SET
                         title=excluded.title, salary=excluded.salary,
                         salary_source=excluded.salary_source, location=excluded.location,
                         tags=excluded.tags, boss_name=excluded.boss_name,
                         boss_active_status=excluded.boss_active_status,
                         company_scale=excluded.company_scale,
                         company_stage=excluded.company_stage,
                         company_industry=excluded.company_industry,
                         skills=excluded.skills, job_labels=excluded.job_labels,
                         welfare=excluded.welfare, job_link=excluded.job_link,
                         company_link=excluded.company_link,
                         search_keyword=excluded.search_keyword,
                         search_city=excluded.search_city,
                         scraped_at=datetime('now')""",
                    (
                        job.get("job_id", ""),
                        identity,
                        job.get("title", ""),
                        job.get("salary", ""),
                        job.get("salary_source", ""),
                        job.get("location", ""),
                        job.get("tags", ""),
                        job.get("boss_name", ""),
                        job.get("boss_active_status", ""),
                        job.get("company_scale", ""),
                        job.get("company_stage", ""),
                        job.get("company_industry", ""),
                        job.get("skills", ""),
                        job.get("job_labels", ""),
                        job.get("welfare", ""),
                        job.get("job_link", ""),
                        job.get("company_link", ""),
                        keyword,
                        city,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def get_scraped_jobs(self, identity: str, keyword: str = None, city: str = None,
                         limit: int = 100, offset: int = 0) -> list[dict]:
        conn = self.db._conn()
        try:
            sql = "SELECT * FROM scraped_job WHERE identity=?"
            params = [identity]
            if keyword:
                sql += " AND search_keyword=?"
                params.append(keyword)
            if city:
                sql += " AND search_city=?"
                params.append(city)
            sql += " ORDER BY scraped_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count_scraped_jobs(self, identity: str) -> int:
        conn = self.db._conn()
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM scraped_job WHERE identity=?", (identity,)).fetchone()
            return row["cnt"]
        finally:
            conn.close()

    # ---- Scraped Details ----

    def save_scraped_detail_single(self, detail: dict, identity: str):
        """Save a single scraped detail (for incremental/progressive saving)."""
        conn = self.db._conn()
        try:
            skill_tags = detail.get("skill_tags", [])
            if isinstance(skill_tags, list):
                skill_tags = json.dumps(skill_tags, ensure_ascii=False)
            conn.execute(
                """INSERT INTO scraped_detail
                   (job_id, identity, title, company, salary, location,
                    boss_active_status, tags_list, skill_tags, jd)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(job_id, identity) DO UPDATE SET
                     title=excluded.title, company=excluded.company,
                     salary=excluded.salary, location=excluded.location,
                     boss_active_status=excluded.boss_active_status,
                     tags_list=excluded.tags_list, skill_tags=excluded.skill_tags,
                     jd=excluded.jd, scraped_at=datetime('now')""",
                (
                    detail.get("job_id", ""),
                    identity,
                    detail.get("title", ""),
                    detail.get("company", ""),
                    detail.get("salary", ""),
                    detail.get("location", ""),
                    detail.get("boss_active_status", ""),
                    detail.get("tags_list", ""),
                    skill_tags,
                    detail.get("jd", ""),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_existing_detail_job_ids(self, identity: str, job_ids: list[str]) -> set[str]:
        """Return the set of job_ids that already have detail records in DB."""
        if not job_ids:
            return set()
        conn = self.db._conn()
        try:
            placeholders = ",".join("?" * len(job_ids))
            sql = f"SELECT job_id FROM scraped_detail WHERE identity=? AND job_id IN ({placeholders})"
            rows = conn.execute(sql, [identity] + job_ids).fetchall()
            return {r["job_id"] for r in rows}
        finally:
            conn.close()

    def save_scraped_details(self, details: list[dict], identity: str):
        conn = self.db._conn()
        try:
            for d in details:
                skill_tags = d.get("skill_tags", [])
                if isinstance(skill_tags, list):
                    skill_tags = json.dumps(skill_tags, ensure_ascii=False)
                conn.execute(
                    """INSERT INTO scraped_detail
                       (job_id, identity, title, company, salary, location,
                        boss_active_status, tags_list, skill_tags, jd)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(job_id, identity) DO UPDATE SET
                         title=excluded.title, company=excluded.company,
                         salary=excluded.salary, location=excluded.location,
                         boss_active_status=excluded.boss_active_status,
                         tags_list=excluded.tags_list, skill_tags=excluded.skill_tags,
                         jd=excluded.jd, scraped_at=datetime('now')""",
                    (
                        d.get("job_id", ""),
                        identity,
                        d.get("title", ""),
                        d.get("company", ""),
                        d.get("salary", ""),
                        d.get("location", ""),
                        d.get("boss_active_status", ""),
                        d.get("tags_list", ""),
                        skill_tags,
                        d.get("jd", ""),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def get_scraped_details(self, identity: str, job_ids: list[str] = None,
                            limit: int = 100) -> list[dict]:
        conn = self.db._conn()
        try:
            sql = "SELECT * FROM scraped_detail WHERE identity=?"
            params = [identity]
            if job_ids:
                placeholders = ",".join("?" * len(job_ids))
                sql += f" AND job_id IN ({placeholders})"
                params.extend(job_ids)
            sql += " ORDER BY scraped_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ---- Match Results ----

    def save_match_result(self, identity: str, source_id: int, target_job_id: str,
                          score: float, reasoning: str, suggestions: list, model_name: str,
                          evidence: list = None, gaps: list = None,
                          retrieved_chunks: list = None):
        conn = self.db._conn()
        try:
            suggestions_json = json.dumps(suggestions, ensure_ascii=False)
            evidence_json = json.dumps(evidence or [], ensure_ascii=False)
            gaps_json = json.dumps(gaps or [], ensure_ascii=False)
            chunks_json = json.dumps(retrieved_chunks or [], ensure_ascii=False)
            conn.execute(
                """INSERT INTO match_result
                   (identity, source_id, target_job_id, score, reasoning, suggestions,
                    model_name, evidence, gaps, retrieved_chunks)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(identity, source_id, target_job_id) DO UPDATE SET
                     score=excluded.score, reasoning=excluded.reasoning,
                     suggestions=excluded.suggestions, model_name=excluded.model_name,
                     evidence=excluded.evidence, gaps=excluded.gaps,
                     retrieved_chunks=excluded.retrieved_chunks,
                     created_at=datetime('now')""",
                (identity, source_id, target_job_id, score, reasoning,
                 suggestions_json, model_name, evidence_json, gaps_json, chunks_json),
            )
            conn.commit()
        finally:
            conn.close()

    def get_match_results(self, identity: str, source_id: int = None,
                          limit: int = 50) -> list[dict]:
        conn = self.db._conn()
        try:
            sql = "SELECT * FROM match_result WHERE identity=?"
            params = [identity]
            if source_id is not None:
                sql += " AND source_id=?"
                params.append(source_id)
            sql += " ORDER BY score DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("suggestions"), str):
                    try:
                        d["suggestions"] = json.loads(d["suggestions"])
                    except (json.JSONDecodeError, ValueError):
                        d["suggestions"] = []
                for field in ("evidence", "gaps", "retrieved_chunks"):
                    if isinstance(d.get(field), str):
                        try:
                            d[field] = json.loads(d[field])
                        except (json.JSONDecodeError, ValueError):
                            d[field] = []
                    elif d.get(field) is None:
                        d[field] = []
                results.append(d)
            return results
        finally:
            conn.close()

    def get_match_results_with_jobs(self, identity: str, source_id: int = None,
                                     limit: int = 50, offset: int = 0) -> list[dict]:
        """JOIN match_result + scraped_job + scraped_detail for display."""
        conn = self.db._conn()
        try:
            sql = """SELECT mr.id, mr.identity, mr.source_id, mr.target_job_id,
                            mr.score, mr.reasoning, mr.suggestions, mr.model_name, mr.created_at,
                            mr.evidence, mr.gaps, mr.retrieved_chunks,
                            sj.title, sj.salary, sj.location, sj.company_scale,
                            sj.company_stage, sj.company_industry, sj.skills,
                            sj.boss_name, sj.boss_active_status, sj.tags,
                            sd.jd, sd.skill_tags, sd.tags_list, sd.company as detail_company
                     FROM match_result mr
                     LEFT JOIN scraped_job sj ON mr.target_job_id = sj.job_id AND sj.identity = ?
                     LEFT JOIN scraped_detail sd ON mr.target_job_id = sd.job_id AND sd.identity = ?
                     WHERE mr.identity = ?"""
            params = [identity, identity, identity]
            if source_id is not None:
                sql += " AND mr.source_id = ?"
                params.append(source_id)
            sql += " ORDER BY mr.score DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("suggestions"), str):
                    try:
                        d["suggestions"] = json.loads(d["suggestions"])
                    except (json.JSONDecodeError, ValueError):
                        d["suggestions"] = []
                if isinstance(d.get("skill_tags"), str):
                    try:
                        d["skill_tags"] = json.loads(d["skill_tags"])
                    except (json.JSONDecodeError, ValueError):
                        d["skill_tags"] = []
                for field in ("evidence", "gaps", "retrieved_chunks"):
                    if isinstance(d.get(field), str):
                        try:
                            d[field] = json.loads(d[field])
                        except (json.JSONDecodeError, ValueError):
                            d[field] = []
                    elif d.get(field) is None:
                        d[field] = []
                # Prefer detail_company over company_industry for display
                if d.get("detail_company"):
                    d["company"] = d.pop("detail_company")
                else:
                    d["company"] = d.get("company_industry", "")
                results.append(d)
            return results
        finally:
            conn.close()

    def get_existing_match_job_ids(self, identity: str, source_id: int = 1) -> set[str]:
        """Return set of job_ids already matched for this identity/source."""
        conn = self.db._conn()
        try:
            sql = "SELECT target_job_id FROM match_result WHERE identity=? AND source_id=?"
            rows = conn.execute(sql, (identity, source_id)).fetchall()
            return {r["target_job_id"] for r in rows}
        finally:
            conn.close()

    # ---- Match Summary ----

    def save_match_summary(self, identity: str, source_id: int = 1,
                           structured: dict = None, raw_text: str = "",
                           model_name: str = ""):
        conn = self.db._conn()
        try:
            structured_json = json.dumps(structured, ensure_ascii=False) if structured else ""
            conn.execute(
                """INSERT INTO match_summary (identity, source_id, structured, raw_text, model_name)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(identity, source_id) DO UPDATE SET
                     structured=excluded.structured, raw_text=excluded.raw_text,
                     model_name=excluded.model_name, created_at=datetime('now')""",
                (identity, source_id, structured_json, raw_text, model_name),
            )
            conn.commit()
        finally:
            conn.close()

    def get_match_summary(self, identity: str, source_id: int = 1) -> dict | None:
        conn = self.db._conn()
        try:
            row = conn.execute(
                "SELECT * FROM match_summary WHERE identity=? AND source_id=?",
                (identity, source_id),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            if isinstance(d.get("structured"), str) and d["structured"]:
                try:
                    d["structured"] = json.loads(d["structured"])
                except (json.JSONDecodeError, ValueError):
                    d["structured"] = None
            return d
        finally:
            conn.close()

    # ---- Chrome State ----

    def get_chrome_state(self) -> dict | None:
        conn = self.db._conn()
        try:
            row = conn.execute("SELECT * FROM chrome_state WHERE id=1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_chrome_state(self, **kwargs):
        conn = self.db._conn()
        try:
            # Ensure row exists
            conn.execute("INSERT OR IGNORE INTO chrome_state (id) VALUES (1)")
            sets = []
            params = []
            for key in ("geek_cdp_port", "boss_cdp_port", "geek_logged_in", "boss_logged_in",
                        "geek_login_checked_at", "boss_login_checked_at"):
                if key in kwargs:
                    sets.append(f"{key}=?")
                    params.append(kwargs[key])
            if sets:
                params.append(1)
                conn.execute(
                    f"UPDATE chrome_state SET {', '.join(sets)}, updated_at=datetime('now') WHERE id=?",
                    params,
                )
            conn.commit()
        finally:
            conn.close()

    def get_cached_login_state(self, identity: str, cache_ttl_minutes: int = 30) -> dict | None:
        """Return cached login state if within TTL, else None."""
        conn = self.db._conn()
        try:
            row = conn.execute("SELECT * FROM chrome_state WHERE id=1").fetchone()
            if not row:
                return None
            d = dict(row)
            logged_in = bool(d.get(f"{identity}_logged_in", 0))
            checked_at = d.get(f"{identity}_login_checked_at", "")
            if not logged_in or not checked_at:
                return None
            # Check TTL
            from datetime import datetime, timedelta
            try:
                checked_time = datetime.fromisoformat(checked_at)
            except (ValueError, TypeError):
                return None
            if datetime.utcnow() - checked_time > timedelta(minutes=cache_ttl_minutes):
                return None
            return {"logged_in": True, "checked_at": checked_at}
        finally:
            conn.close()

    # ---- Scrape Log ----

    def create_scrape_log(self, identity: str, task_type: str, keyword: str = "",
                          city: str = "") -> int:
        conn = self.db._conn()
        try:
            cur = conn.execute(
                "INSERT INTO scrape_log (identity, task_type, status, keyword, city) VALUES (?, ?, 'running', ?, ?)",
                (identity, task_type, keyword, city),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def finish_scrape_log(self, log_id: int, status: str, items_scraped: int = 0,
                          error_message: str = ""):
        conn = self.db._conn()
        try:
            conn.execute(
                """UPDATE scrape_log SET status=?, items_scraped=?, error_message=?,
                   finished_at=datetime('now') WHERE id=?""",
                (status, items_scraped, error_message, log_id),
            )
            conn.commit()
        finally:
            conn.close()

    def update_scrape_log(self, log_id: int, items_scraped: int = None,
                          status: str = None, error_message: str = None):
        conn = self.db._conn()
        try:
            sets = []
            params = []
            if items_scraped is not None:
                sets.append("items_scraped=?")
                params.append(items_scraped)
            if status is not None:
                sets.append("status=?")
                params.append(status)
            if error_message is not None:
                sets.append("error_message=?")
                params.append(error_message)
            if sets:
                params.append(log_id)
                conn.execute(
                    f"UPDATE scrape_log SET {', '.join(sets)} WHERE id=?", params
                )
                conn.commit()
        finally:
            conn.close()

    def get_latest_scrape_log(self, identity: str) -> dict | None:
        conn = self.db._conn()
        try:
            row = conn.execute(
                "SELECT * FROM scrape_log WHERE identity=? ORDER BY id DESC LIMIT 1",
                (identity,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
