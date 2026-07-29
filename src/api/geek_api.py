"""Geek-side job search orchestration — background scraping with progress tracking."""

import json
import logging
import threading
from typing import Callable

from src.core.scraper import scrape_list, scrape_details
from src.core.city import resolve_city, list_cities
from src.core.constants import (
    SCALE_MAP, STAGE_MAP, SALARY_MAP, EXPERIENCE_MAP, DEGREE_MAP, INDUSTRY_MAP,
    GEEK_CDP_PORT,
)
from src.core.chrome import is_cdp_ready
from src.db.repository import Repository
from src.ai.matcher import Matcher

log = logging.getLogger(__name__)


class ScrapeTask:
    """Represents an in-progress or completed scrape task."""

    def __init__(self, task_id: int, keyword: str, city: str, max_pages: int):
        self.task_id = task_id
        self.keyword = keyword
        self.city = city
        self.max_pages = max_pages
        self.cancel_event = threading.Event()
        self.status = "running"
        self.current_page = 0
        self.total_pages = max_pages
        self.jobs_found = 0
        self.details_scraped = 0
        self.phase = "list"
        self.error_message = ""


class GeekAPI:
    """Orchestrates geek-side scraping with thread safety and progress tracking."""

    def __init__(self, repo: Repository, notify_callback: Callable | None = None,
                 embedder=None):
        self.repo = repo
        self._notify = notify_callback
        self._lock = threading.Lock()
        self._current_task: ScrapeTask | None = None
        self._matcher = Matcher(repo, notify_callback=notify_callback, embedder=embedder)

    # ---- Public API (called from bridge.py on main thread) ----

    def search_jobs(self, keyword: str, city: str, max_pages: int,
                    filters_json: str = "{}") -> dict:
        """Start a background scrape task. Returns {ok, data: {task_id}} or error."""
        with self._lock:
            if self._current_task and self._current_task.status == "running":
                return {"ok": False, "error": "已有抓取任务进行中"}

        if not is_cdp_ready(GEEK_CDP_PORT):
            return {"ok": False, "error": "Chrome 未启动或未登录，请先在登录页启动 Chrome"}

        filters = {}
        try:
            filters = json.loads(filters_json) if filters_json else {}
        except (json.JSONDecodeError, ValueError):
            return {"ok": False, "error": "筛选参数格式错误"}

        validated = self._validate_filters(filters)
        if validated is None:
            return {"ok": False, "error": "筛选参数值无效"}

        city_name, _ = resolve_city(city)
        log_id = self.repo.create_scrape_log(
            identity="geek", task_type="search",
            keyword=keyword, city=city_name,
        )

        task = ScrapeTask(log_id, keyword, city_name, max_pages)
        with self._lock:
            self._current_task = task

        t = threading.Thread(
            target=self._run_scrape,
            args=(task, keyword, city, max_pages, validated),
            daemon=True,
        )
        t.start()
        return {"ok": True, "data": {"task_id": log_id}}

    def get_scrape_progress(self) -> dict:
        """Return current scrape task progress."""
        with self._lock:
            task = self._current_task
        if not task:
            return {"ok": True, "data": {"status": "idle"}}
        return {
            "ok": True,
            "data": {
                "task_id": task.task_id,
                "status": task.status,
                "phase": task.phase,
                "current_page": task.current_page,
                "total_pages": task.total_pages,
                "jobs_found": task.jobs_found,
                "details_scraped": task.details_scraped,
                "keyword": task.keyword,
                "city": task.city,
                "error_message": task.error_message,
            },
        }

    def cancel_scrape(self) -> dict:
        """Request cancellation of the current scrape task."""
        with self._lock:
            task = self._current_task
        if not task or task.status != "running":
            return {"ok": False, "error": "没有进行中的抓取任务"}
        task.cancel_event.set()
        return {"ok": True, "data": {"message": "已发送取消请求"}}

    def get_scraped_jobs(self, keyword: str = "", city: str = "",
                         limit: int = 50, offset: int = 0) -> dict:
        """Read scraped jobs from DB."""
        jobs = self.repo.get_scraped_jobs(
            "geek", keyword=keyword or None, city=city or None,
            limit=limit, offset=offset,
        )
        total = self.repo.count_scraped_jobs("geek")
        return {
            "ok": True,
            "data": {
                "jobs": jobs,
                "total": total,
                "limit": limit,
                "offset": offset,
            },
        }

    def get_scraped_details(self, job_ids_json: str = "[]",
                            limit: int = 100) -> dict:
        """Read scraped job details from DB."""
        job_ids = []
        try:
            job_ids = json.loads(job_ids_json) if job_ids_json else []
        except (json.JSONDecodeError, ValueError):
            return {"ok": False, "error": "job_ids 格式错误"}
        details = self.repo.get_scraped_details(
            "geek", job_ids=job_ids or None, limit=limit,
        )
        return {"ok": True, "data": {"details": details}}

    def list_cities(self, keyword: str = "") -> dict:
        """City autocomplete."""
        cities = list_cities(keyword=keyword or None, use_live=True)
        return {"ok": True, "data": {"cities": cities}}

    def get_filter_options(self) -> dict:
        """Return all filter maps for frontend dropdowns."""
        return {
            "ok": True,
            "data": {
                "salary": SALARY_MAP,
                "experience": EXPERIENCE_MAP,
                "degree": DEGREE_MAP,
                "scale": SCALE_MAP,
                "stage": STAGE_MAP,
                "industry": INDUSTRY_MAP,
            },
        }

    # ---- Internal ----

    def _validate_filters(self, filters: dict) -> dict | None:
        """Translate human-readable filter values to API codes."""
        result = {}
        filter_maps = {
            "salary": SALARY_MAP,
            "experience": EXPERIENCE_MAP,
            "degree": DEGREE_MAP,
            "scale": SCALE_MAP,
            "stage": STAGE_MAP,
        }
        for key, value in filters.items():
            if not value:
                continue
            if key not in filter_maps:
                continue
            fmap = filter_maps[key]
            if value in fmap:
                result[key] = fmap[value]
            else:
                log.warning(f"Invalid filter value: {key}={value}")
                return None
        return result

    def _notify_progress(self, task: ScrapeTask):
        """Push progress to frontend via evaluate_js callback."""
        if self._notify:
            payload = json.dumps({
                "task_id": task.task_id,
                "status": task.status,
                "phase": task.phase,
                "current_page": task.current_page,
                "total_pages": task.total_pages,
                "jobs_found": task.jobs_found,
                "details_scraped": task.details_scraped,
                "keyword": task.keyword,
                "city": task.city,
                "error_message": task.error_message,
            }, ensure_ascii=False)
            try:
                self._notify(payload)
            except Exception as e:
                log.debug(f"通知前端失败: {e}")

    def _on_list_progress(self, task: ScrapeTask, page: int, max_pages: int,
                          jobs_found: int, phase: str, new_jobs: list | None = None):
        """Callback for scrape_list per-page progress. Saves jobs incrementally."""
        task.current_page = page
        task.total_pages = max_pages
        task.jobs_found = jobs_found
        task.phase = "list"
        if new_jobs:
            city_name = task.city
            self.repo.save_scraped_jobs(new_jobs, "geek", keyword=task.keyword, city=city_name)
        self._notify_progress(task)

    def _on_detail_progress(self, task: ScrapeTask, index: int, total: int,
                            _jobs_found: int, phase: str, detail: dict | None = None):
        """Callback for scrape_details per-detail progress. Saves detail incrementally."""
        task.details_scraped = index
        task.phase = "details"
        if detail:
            self.repo.save_scraped_detail_single(detail, "geek")
        self._notify_progress(task)

    def _run_scrape(self, task: ScrapeTask, keyword: str, city: str,
                    max_pages: int, filters: dict):
        """Worker thread entry point. Runs the full scrape pipeline."""
        try:
            # Phase 1: Scrape job list (each page saved incrementally via callback)
            task.phase = "list"
            self._notify_progress(task)

            list_result = scrape_list(
                keyword, city, max_pages, filters, identity="geek",
                progress_callback=lambda pg, mp, jf, ph, nj=None: self._on_list_progress(task, pg, mp, jf, ph, nj),
                cancel_event=task.cancel_event,
            )

            if task.cancel_event.is_set():
                task.status = "cancelled"
                self.repo.finish_scrape_log(task.task_id, "cancelled",
                                            items_scraped=task.jobs_found)
                self._notify_progress(task)
                return

            if not list_result.get("ok"):
                task.status = "failed"
                task.error_message = list_result.get("error", "列表抓取失败")
                self.repo.finish_scrape_log(task.task_id, "failed",
                                            items_scraped=0,
                                            error_message=task.error_message)
                self._notify_progress(task)
                return

            jobs = list_result["data"]["jobs"]
            task.jobs_found = len(jobs)
            task.current_page = max_pages
            task.phase = "saving"
            self._notify_progress(task)

            # Phase 2: Scrape details (skip already-cached, save each incrementally)
            if jobs:
                all_job_ids = [j.get("job_id", "") for j in jobs if j.get("job_id")]
                existing_ids = self.repo.get_existing_detail_job_ids("geek", all_job_ids)
                jobs_to_scrape = [j for j in jobs if j.get("job_id", "") not in existing_ids]
                skipped = len(jobs) - len(jobs_to_scrape)
                if skipped:
                    log.info(f"跳过 {skipped} 条已有详情缓存")

                if jobs_to_scrape:
                    task.phase = "details"
                    task.details_scraped = 0
                    self._notify_progress(task)

                    detail_result = scrape_details(
                        jobs_to_scrape, identity="geek",
                        progress_callback=lambda idx, total, jf, ph, d=None: self._on_detail_progress(task, idx, total, jf, ph, d),
                        cancel_event=task.cancel_event,
                    )

                    if task.cancel_event.is_set():
                        task.status = "cancelled"
                        self.repo.finish_scrape_log(task.task_id, "cancelled",
                                                    items_scraped=task.jobs_found)
                        self._notify_progress(task)
                        return

                    if not detail_result.get("ok"):
                        log.warning(f"详情抓取失败: {detail_result.get('error', '')}")
                else:
                    log.info("所有详情已缓存，跳过详情抓取")

            # Done
            task.status = "completed"
            self.repo.finish_scrape_log(task.task_id, "completed",
                                        items_scraped=task.jobs_found)
            self._notify_progress(task)

        except Exception as e:
            log.exception("抓取任务异常")
            task.status = "failed"
            task.error_message = str(e)
            try:
                self.repo.finish_scrape_log(task.task_id, "failed",
                                            items_scraped=task.jobs_found,
                                            error_message=str(e))
            except Exception:
                pass
            self._notify_progress(task)

    # ---- Match Methods ----

    def start_match(self, resume: str, job_ids: list, supplements: list = None) -> dict:
        """Start matching selected jobs against the given resume."""
        if not job_ids:
            return {"ok": False, "error": "请选择至少一个职位"}

        if not resume.strip():
            return {"ok": False, "error": "请先保存简历"}

        return self._matcher.start_match(resume, job_ids, supplements)

    def get_match_summary(self) -> dict:
        """Return the latest match summary."""
        row = self.repo.get_match_summary("geek", source_id=1)
        if not row:
            return {"ok": True, "data": {"summary": None}}
        return {"ok": True, "data": {"summary": row}}

    def get_match_progress(self) -> dict:
        """Return current match task progress."""
        return self._matcher.get_progress()

    def cancel_match(self) -> dict:
        """Cancel current match task."""
        return self._matcher.cancel()

    def get_match_results(self, source_id: int = 1, limit: int = 50,
                          offset: int = 0) -> dict:
        """Read match results from DB with job details."""
        results = self.repo.get_match_results_with_jobs(
            "geek", source_id=source_id, limit=limit, offset=offset,
        )
        total = len(self.repo.get_match_results("geek", source_id=source_id, limit=10000))
        return {
            "ok": True,
            "data": {
                "results": results,
                "total": total,
                "limit": limit,
                "offset": offset,
            },
        }
