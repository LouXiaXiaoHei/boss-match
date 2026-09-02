"""Interview preparation orchestrator — background generation with progress tracking."""

import json
import logging
import threading

from src.ai.prompts import INTERVIEW_PREP_SYSTEM_PROMPT, build_interview_prep_user_prompt
from src.ai.client import AIClient
from src.db.repository import Repository

log = logging.getLogger(__name__)


class InterviewPrepTask:
    """Represents an in-progress or completed interview prep task."""

    def __init__(self, prep_id: int, resume_id: int, job_id: str):
        self.prep_id = prep_id
        self.resume_id = resume_id
        self.job_id = job_id
        self.cancel_event = threading.Event()
        self.status = "generating"
        self.error_message = ""


class InterviewPrepper:
    """Orchestrates interview preparation with thread safety and progress tracking."""

    def __init__(self, repo: Repository, notify_callback=None, embedder=None):
        self.repo = repo
        self._notify = notify_callback
        self._lock = threading.Lock()
        self._current_task: InterviewPrepTask | None = None

    # ---- Public API ----

    def start_prep(self, resume_id: int, job_id: str) -> dict:
        """Start a background interview prep task. Returns {ok, data: {prep_id}}."""
        with self._lock:
            if self._current_task and self._current_task.status == "generating":
                return {"ok": False, "error": "已有面试准备任务进行中"}

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

        # Create interview prep record
        prep_id = self.repo.save_interview_prep(
            resume_id=resume_id, job_id=job_id,
            job_title=job_title, company_name=company_name,
            status="generating",
        )

        task = InterviewPrepTask(prep_id, resume_id, job_id)
        with self._lock:
            self._current_task = task

        t = threading.Thread(
            target=self._run_prep,
            args=(task, resume["content"], job_id, job_details, match_data),
            daemon=True,
        )
        t.start()
        return {"ok": True, "data": {"prep_id": prep_id}}

    def get_progress(self) -> dict:
        """Return current interview prep task progress."""
        with self._lock:
            task = self._current_task
        if not task:
            return {"ok": True, "data": {"status": "idle"}}
        return {
            "ok": True,
            "data": {
                "prep_id": task.prep_id,
                "status": task.status,
                "error_message": task.error_message,
            },
        }

    def cancel(self) -> dict:
        """Request cancellation of the current interview prep task."""
        with self._lock:
            task = self._current_task
        if not task or task.status != "generating":
            return {"ok": False, "error": "没有进行中的面试准备任务"}
        task.cancel_event.set()
        return {"ok": True, "data": {"message": "已发送取消请求"}}

    # ---- Internal ----

    def _notify_progress(self, task: InterviewPrepTask,
                         event_type: str = "interview_prep_progress"):
        """Push progress to frontend via evaluate_js callback."""
        if self._notify:
            payload = json.dumps({
                "type": event_type,
                "prep_id": task.prep_id,
                "status": task.status,
                "error_message": task.error_message,
            }, ensure_ascii=False)
            try:
                self._notify(payload)
            except Exception as e:
                log.debug(f"通知前端失败: {e}")

    def _run_prep(self, task: InterviewPrepTask, resume_text: str,
                  job_id: str, job_details: list, match_data: dict | None):
        """Worker thread entry point. Runs the full interview prep pipeline."""
        try:
            # Build job info
            job_info = {}
            skill_tags = []
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

            # Get match data: gaps, suggestions, evidence
            gaps = match_data.get("gaps", []) if match_data else []
            suggestions = match_data.get("suggestions", []) if match_data else []
            evidence = match_data.get("evidence", []) if match_data else []

            # Build user prompt
            user_prompt = build_interview_prep_user_prompt(
                resume_text=resume_text,
                job_info=job_info,
                gaps=gaps,
                suggestions=suggestions,
                evidence=evidence,
                skill_tags=skill_tags,
            )

            # Call LLM
            ai_client = AIClient(self.repo)
            client, model = ai_client._create_client()

            self._notify_progress(task)

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": INTERVIEW_PREP_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.4,
            )

            if task.cancel_event.is_set():
                task.status = "cancelled"
                self.repo.update_interview_prep(task.prep_id, status="cancelled")
                self._notify_progress(task, "interview_prep_failed")
                return

            content = response.choices[0].message.content
            parsed = AIClient._parse_json(content)
            if parsed is None:
                raise ValueError(f"LLM 返回内容无法解析为 JSON: {content[:200]}")

            # Extract questions
            questions = parsed.get("questions", [])
            if not isinstance(questions, list):
                questions = []

            # Store overall_strategy alongside questions
            overall_strategy = parsed.get("overall_strategy", {})
            # Embed overall_strategy as a special key in questions for DB storage
            if isinstance(overall_strategy, dict) and overall_strategy:
                questions_with_meta = {"items": questions, "overall_strategy": overall_strategy}
            else:
                questions_with_meta = {"items": questions, "overall_strategy": {}}

            # Save result
            self.repo.update_interview_prep(
                task.prep_id,
                questions=questions_with_meta,
                status="completed",
                model_name=model,
            )

            task.status = "completed"
            self._notify_progress(task, "interview_prep_completed")
            log.info(f"Interview prep {task.prep_id} completed: {len(questions)} questions")

        except Exception as e:
            log.exception("面试准备任务异常")
            task.status = "failed"
            task.error_message = str(e)
            try:
                self.repo.update_interview_prep(
                    task.prep_id,
                    status="failed",
                    error_message=str(e),
                )
            except Exception:
                pass
            self._notify_progress(task, "interview_prep_failed")
