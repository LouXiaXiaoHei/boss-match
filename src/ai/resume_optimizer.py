"""Resume optimization orchestrator — background optimization with progress tracking."""

import json
import logging
import threading

from src.ai.prompts import OPTIMIZE_SYSTEM_PROMPT, build_optimize_user_prompt
from src.ai.client import AIClient
from src.db.repository import Repository

log = logging.getLogger(__name__)


class OptimizationTask:
    """Represents an in-progress or completed optimization task."""

    def __init__(self, optimization_id: int, resume_id: int, job_id: str):
        self.optimization_id = optimization_id
        self.resume_id = resume_id
        self.job_id = job_id
        self.cancel_event = threading.Event()
        self.status = "generating"
        self.error_message = ""


class ResumeOptimizer:
    """Orchestrates resume optimization with thread safety and progress tracking."""

    def __init__(self, repo: Repository, notify_callback=None, embedder=None):
        self.repo = repo
        self._notify = notify_callback
        self._lock = threading.Lock()
        self._current_task: OptimizationTask | None = None

    # ---- Public API ----

    def start_optimization(self, resume_id: int, job_id: str) -> dict:
        """Start a background optimization task. Returns {ok, data: {optimization_id}}."""
        with self._lock:
            if self._current_task and self._current_task.status == "generating":
                return {"ok": False, "error": "已有优化任务进行中"}

        # Validate resume exists
        resume = self.repo.get_resume_by_id(resume_id)
        if not resume:
            return {"ok": False, "error": "简历不存在"}
        if not resume.get("content", "").strip():
            return {"ok": False, "error": "简历内容为空"}

        # Get job detail for title/company
        job_details = self.repo.get_scraped_details("geek", job_ids=[job_id], limit=1)
        job_title = ""
        company_name = ""
        if job_details:
            job_title = job_details[0].get("title", "")
            company_name = job_details[0].get("company", "")

        # Check if match result exists for this resume+job
        match_results = self.repo.get_match_results("geek", source_id=resume_id, limit=100)
        match_data = None
        for mr in match_results:
            if mr.get("target_job_id") == job_id:
                match_data = mr
                break

        # Create optimization record
        optimization_id = self.repo.save_optimization(
            resume_id=resume_id, job_id=job_id,
            job_title=job_title, company_name=company_name,
            original_content=resume["content"],
            status="generating",
        )

        task = OptimizationTask(optimization_id, resume_id, job_id)
        with self._lock:
            self._current_task = task

        t = threading.Thread(
            target=self._run_optimization,
            args=(task, resume["content"], job_id, job_details, match_data),
            daemon=True,
        )
        t.start()
        return {"ok": True, "data": {"optimization_id": optimization_id}}

    def get_progress(self) -> dict:
        """Return current optimization task progress."""
        with self._lock:
            task = self._current_task
        if not task:
            return {"ok": True, "data": {"status": "idle"}}
        return {
            "ok": True,
            "data": {
                "optimization_id": task.optimization_id,
                "status": task.status,
                "error_message": task.error_message,
            },
        }

    def cancel(self) -> dict:
        """Request cancellation of the current optimization task."""
        with self._lock:
            task = self._current_task
        if not task or task.status != "generating":
            return {"ok": False, "error": "没有进行中的优化任务"}
        task.cancel_event.set()
        return {"ok": True, "data": {"message": "已发送取消请求"}}

    # ---- Internal ----

    def _notify_progress(self, task: OptimizationTask, event_type: str = "optimization_progress"):
        """Push progress to frontend via evaluate_js callback."""
        if self._notify:
            payload = json.dumps({
                "type": event_type,
                "optimization_id": task.optimization_id,
                "status": task.status,
                "error_message": task.error_message,
            }, ensure_ascii=False)
            try:
                self._notify(payload)
            except Exception as e:
                log.debug(f"通知前端失败: {e}")

    def _run_optimization(self, task: OptimizationTask, resume_text: str,
                          job_id: str, job_details: list, match_data: dict | None):
        """Worker thread entry point. Runs the full optimization pipeline."""
        try:
            # Build job info
            job_info = {}
            if job_details:
                jd = job_details[0]
                skill_tags = jd.get("skill_tags", [])
                if isinstance(skill_tags, str):
                    try:
                        skill_tags = json.loads(skill_tags)
                    except (json.JSONDecodeError, ValueError):
                        skill_tags = []
                job_info = {
                    "title": jd.get("title", ""),
                    "company": jd.get("company", ""),
                    "salary": jd.get("salary", ""),
                    "location": jd.get("location", ""),
                    "skill_tags": skill_tags,
                    "tags_list": jd.get("tags_list", ""),
                    "jd": jd.get("jd", ""),
                }

            # Get match data: gaps, suggestions, evidence, retrieved_chunks
            gaps = match_data.get("gaps", []) if match_data else []
            suggestions = match_data.get("suggestions", []) if match_data else []
            evidence = match_data.get("evidence", []) if match_data else []
            retrieved_chunks = match_data.get("retrieved_chunks", []) if match_data else []

            # Build user prompt
            user_prompt = build_optimize_user_prompt(
                resume_text=resume_text,
                job_info=job_info,
                gaps=gaps,
                suggestions=suggestions,
                evidence=evidence,
                retrieved_chunks=retrieved_chunks,
            )

            # Call LLM
            ai_client = AIClient(self.repo)
            client, model = ai_client._create_client()

            self._notify_progress(task)

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": OPTIMIZE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
            )

            if task.cancel_event.is_set():
                task.status = "cancelled"
                self.repo.update_optimization(task.optimization_id, status="cancelled")
                self._notify_progress(task, "optimization_failed")
                return

            content = response.choices[0].message.content
            parsed = AIClient._parse_json(content)
            if parsed is None:
                raise ValueError(f"LLM 返回内容无法解析为 JSON: {content[:200]}")

            # Extract diff sections
            sections = parsed.get("sections", [])
            if not isinstance(sections, list):
                sections = []

            # Compute optimized content by applying all revisions
            optimized_content = resume_text
            for sec in sections:
                if sec.get("action") == "rewrite" and sec.get("revised"):
                    # Replace original with revised in the optimized content
                    original = sec.get("original", "")
                    if original and original in optimized_content:
                        optimized_content = optimized_content.replace(original, sec["revised"], 1)
                elif sec.get("action") == "add" and sec.get("revised"):
                    # Append new sections
                    optimized_content += "\n\n" + sec["revised"]

            # Save result
            self.repo.update_optimization(
                task.optimization_id,
                optimized_content=optimized_content,
                diff_sections=sections,
                status="completed",
                model_name=model,
            )

            task.status = "completed"
            self._notify_progress(task, "optimization_completed")
            log.info(f"Optimization {task.optimization_id} completed: {len(sections)} sections")

        except Exception as e:
            log.exception("优化任务异常")
            task.status = "failed"
            task.error_message = str(e)
            try:
                self.repo.update_optimization(
                    task.optimization_id,
                    status="failed",
                    error_message=str(e),
                )
            except Exception:
                pass
            self._notify_progress(task, "optimization_failed")
