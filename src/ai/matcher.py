"""Batch matching orchestration — background threading with progress tracking."""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from src.ai.client import AIClient, JobScoreResult
from src.db.repository import Repository

log = logging.getLogger(__name__)


class MatchTask:
    """Tracks an in-progress or completed match task."""

    def __init__(self, task_id: int, total_jobs: int):
        self.task_id = task_id
        self.total_jobs = total_jobs
        self.completed = 0
        self.skipped = 0
        self.cancel_event = threading.Event()
        self.status = "running"
        self.error_message = ""
        self.current_job_title = ""

    def to_dict(self) -> dict:
        return {
            "type": "match",
            "task_id": self.task_id,
            "status": self.status,
            "total_jobs": self.total_jobs,
            "completed": self.completed,
            "skipped": self.skipped,
            "current_job_title": self.current_job_title,
            "error_message": self.error_message,
        }


class Matcher:
    """Orchestrates batch job-resume matching in a background thread."""

    def __init__(self, repo: Repository, notify_callback: Callable | None = None):
        self.repo = repo
        self._notify = notify_callback
        self._lock = threading.Lock()
        self._current_task: MatchTask | None = None

    def start_match(self, resume: str, job_ids: list[str], concurrency: int = 3) -> dict:
        """Start a match task. Returns {ok, data: {task_id}} or {ok: False, error: ...}."""
        with self._lock:
            if self._current_task and self._current_task.status == "running":
                return {"ok": False, "error": "已有匹配任务进行中"}

            if not resume.strip():
                return {"ok": False, "error": "请先保存简历"}

            if not job_ids:
                return {"ok": False, "error": "请选择至少一个职位"}

            log_id = self.repo.create_scrape_log("geek", "match")
            task = MatchTask(task_id=log_id, total_jobs=len(job_ids))
            self._current_task = task

        thread = threading.Thread(
            target=self._run_match,
            args=(task, resume, job_ids, concurrency),
            daemon=True,
        )
        thread.start()
        return {"ok": True, "data": {"task_id": task.task_id}}

    def get_progress(self) -> dict:
        """Return current match task progress."""
        if not self._current_task:
            return {"ok": True, "data": {"type": "match", "status": "idle"}}
        return {"ok": True, "data": self._current_task.to_dict()}

    def cancel(self) -> dict:
        """Request cancellation of current match task."""
        if self._current_task and self._current_task.status == "running":
            self._current_task.cancel_event.set()
            return {"ok": True}
        return {"ok": False, "error": "没有进行中的匹配任务"}

    def _run_match(self, task: MatchTask, resume: str, job_ids: list[str], concurrency: int):
        """Worker thread entry point."""
        try:
            # Cache check: skip already-matched jobs
            existing = self.repo.get_existing_match_job_ids("geek", source_id=1)
            cached_ids = existing & set(job_ids)
            jobs_to_match = [jid for jid in job_ids if jid not in cached_ids]

            if cached_ids:
                task.skipped = len(cached_ids)
                task.completed = len(cached_ids)
                log.info(f"跳过 {task.skipped} 条已有匹配缓存")
                self._notify_progress(task)

            if not jobs_to_match:
                task.status = "completed"
                task.current_job_title = ""
                self._notify_progress(task)
                self.repo.finish_scrape_log(task.task_id, "completed", task.completed)
                return

            # Load job details
            details = self.repo.get_scraped_details("geek", job_ids=jobs_to_match, limit=len(jobs_to_match))
            detail_map = {d["job_id"]: d for d in details}

            # Load job list for fallback + title display
            jobs = self.repo.get_scraped_jobs("geek", limit=1000, offset=0)
            job_map = {j["job_id"]: j for j in jobs}

            # Build match items: (job_id, detail_dict, title)
            match_items = []
            for jid in jobs_to_match:
                detail = detail_map.get(jid)
                job = job_map.get(jid, {})
                title = (detail or job).get("title", "未知职位")
                if not detail:
                    # Build minimal detail from scraped_job
                    detail = {
                        "title": job.get("title", ""),
                        "company": job.get("company_industry", ""),
                        "salary": job.get("salary", ""),
                        "location": job.get("location", ""),
                        "jd": "",
                        "skill_tags": job.get("skills", ""),
                        "tags_list": job.get("tags", ""),
                    }
                match_items.append((jid, detail, title))

            # Concurrent matching
            auth_failed = threading.Event()

            def do_match(item: tuple) -> tuple[str, JobScoreResult | None, str | None]:
                jid, detail, title = item
                if task.cancel_event.is_set() or auth_failed.is_set():
                    return jid, None, "cancelled"
                try:
                    result = AIClient(self.repo).match_job_seeker(resume, detail)
                    return jid, result, None
                except ValueError as e:
                    err = str(e)
                    if "认证失败" in err:
                        auth_failed.set()
                        return jid, None, err
                    log.warning(f"匹配职位 {title} 失败: {err}")
                    return jid, None, err
                except Exception as e:
                    log.warning(f"匹配职位 {title} 异常: {e}")
                    return jid, None, str(e)

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {executor.submit(do_match, item): item for item in match_items}

                for future in as_completed(futures):
                    if task.cancel_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                    jid, result, error = future.result()

                    if result:
                        self.repo.save_match_result(
                            identity="geek",
                            source_id=1,
                            target_job_id=jid,
                            score=result.score,
                            reasoning=result.reasoning,
                            suggestions=result.suggestions,
                            model_name=result.model_name,
                        )
                        task.completed += 1
                    elif error and "认证失败" in error:
                        task.status = "failed"
                        task.error_message = error
                        break
                    else:
                        task.completed += 1

                    # Update current job title for progress display
                    item = futures[future]
                    task.current_job_title = item[2] if result else ""
                    self._notify_progress(task)

            if task.status == "running":
                task.status = "completed"
                self._notify_progress(task)

            self.repo.finish_scrape_log(
                task.task_id,
                task.status,
                task.completed,
                task.error_message,
            )

        except Exception as e:
            log.error(f"匹配任务异常: {e}", exc_info=True)
            task.status = "failed"
            task.error_message = str(e)
            self._notify_progress(task)
            try:
                self.repo.finish_scrape_log(task.task_id, "failed", task.completed, str(e))
            except Exception:
                pass

    def _notify_progress(self, task: MatchTask):
        """Push progress to frontend via evaluate_js callback."""
        if self._notify:
            try:
                self._notify(json.dumps(task.to_dict(), ensure_ascii=False))
            except Exception as e:
                log.debug(f"Match progress notification failed: {e}")
