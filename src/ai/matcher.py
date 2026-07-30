"""Three-phase RAG matching orchestrator — coordinates embed/index/score/summary."""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from src.ai.chunker import Chunker
from src.ai.client import AIClient, JobScoreResult
from src.ai.embedder import Embedder
from src.ai.event_bus import EventBus, MatchEvent
from src.ai.prompts import build_match_user_prompt, build_summary_user_prompt
from src.ai.retriever import Retriever
from src.ai.summarizer import SummaryStreamer, CancelledError, AuthFailedError
from src.ai.vector_store import VectorStore
from src.db.repository import Repository

log = logging.getLogger(__name__)


class MatchTask:
    """Tracks an in-progress or completed match task."""

    def __init__(self, task_id: int, total_jobs: int):
        self.task_id = task_id
        self.total_jobs = total_jobs
        self.completed = 0
        self.phase = ""
        self.index_progress = 0.0
        self.elapsed_ms = 0
        self.status = "running"
        self.error_message = ""
        self._start_time = time.time()

    def update_elapsed(self):
        self.elapsed_ms = int((time.time() - self._start_time) * 1000)

    def to_dict(self) -> dict:
        self.update_elapsed()
        return {
            "type": "match",
            "task_id": self.task_id,
            "status": self.status,
            "total_jobs": self.total_jobs,
            "completed": self.completed,
            "phase": self.phase,
            "index_progress": self.index_progress,
            "elapsed_ms": self.elapsed_ms,
            "error_message": self.error_message,
        }


class Matcher:
    """RAG matching orchestrator: coordinates three-phase produce-consume."""

    def __init__(self, repo: Repository, notify_callback: Callable | None = None,
                 embedder: Embedder | None = None):
        self.repo = repo
        self._notify = notify_callback
        self._embedder = embedder
        self._lock = threading.Lock()
        self._current_task: MatchTask | None = None
        self._cancel_event = threading.Event()
        self._auth_failed = threading.Event()
        self._resume_id = 1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_match(self, resume: str, job_ids: list[str],
                    supplements: list[str] = None, concurrency: int = 3,
                    resume_id: int = None) -> dict:
        """Start a match task. Returns {ok, data: {task_id}} or {ok: False, error: ...}."""
        with self._lock:
            if self._current_task and self._current_task.status == "running":
                return {"ok": False, "error": "已有匹配任务进行中"}

            if not resume.strip():
                return {"ok": False, "error": "请先保存简历"}

            if not job_ids:
                return {"ok": False, "error": "请选择至少一个职位"}

            self._resume_id = resume_id or 1
            log_id = self.repo.create_scrape_log("geek", "match")
            task = MatchTask(task_id=log_id, total_jobs=len(job_ids))
            self._current_task = task
            self._cancel_event.clear()
            self._auth_failed.clear()

        # Clear previous match results for this source before starting fresh
        self.repo.clear_match_results("geek", source_id=self._resume_id)

        self._bus = EventBus(self._notify)
        self._bus.start()

        thread = threading.Thread(
            target=self._orchestrate,
            args=(resume, job_ids, supplements, concurrency),
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
            self._cancel_event.set()
            return {"ok": True}
        return {"ok": False, "error": "没有进行中的匹配任务"}

    def _check_cancel(self):
        if self._cancel_event.is_set():
            raise CancelledError("用户取消")

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def _orchestrate(self, resume, job_ids, supplements, concurrency):
        try:
            self._init_embedder()                            # Phase 0
            self._build_index(resume, job_ids, supplements)  # Phase 1
            results = self._score_jobs(job_ids, concurrency) # Phase 2
            self._generate_summary(results)                  # Phase 3

            self._current_task.status = "completed"
            self._current_task.update_elapsed()
            self._bus.emit(MatchEvent("match_completed",
                                       total_duration_ms=self._current_task.elapsed_ms))
        except CancelledError:
            self._current_task.status = "cancelled"
            self._bus.emit(MatchEvent("cancelled"))
        except AuthFailedError as e:
            self._current_task.status = "failed"
            self._current_task.error_message = str(e)
            self._bus.emit(MatchEvent("error", error=str(e), fatal=True))
        except Exception as e:
            self._current_task.status = "failed"
            self._current_task.error_message = str(e)
            self._bus.emit(MatchEvent("error", error=str(e)))
        finally:
            self._bus.stop()

    # ------------------------------------------------------------------
    # Phase 0: Init embedder
    # ------------------------------------------------------------------

    def _init_embedder(self):
        self._check_cancel()
        self._current_task.phase = "init_model"
        self._bus.emit(MatchEvent("phase_start", "init_model"))

        if self._embedder and self._embedder._model is not None:
            # Model already loaded (pre-initialized by bridge)
            self._bus.emit(MatchEvent("phase_done", "init_model"))
            return

        def progress_callback(progress, status, speed=0.0):
            self._check_cancel()
            self._bus.emit(MatchEvent("model_download_progress",
                                       progress=progress, status=status, speed=speed))

        self._embedder = Embedder()
        self._embedder.ensure_model(progress_callback)

        self._bus.emit(MatchEvent("phase_done", "init_model"))

    # ------------------------------------------------------------------
    # Phase 1: Build index
    # ------------------------------------------------------------------

    def _build_index(self, resume, job_ids, supplements):
        self._check_cancel()
        self._current_task.phase = "build_index"
        chunker = Chunker()
        chunks_to_embed = []

        # Resume chunking — skip if already embedded for this resume_id
        resume_already_indexed = False
        resume_id_str = str(self._resume_id) if self._resume_id else None
        if resume_id_str:
            active = self.repo.get_resume_by_id(self._resume_id)
            if active and active.get("chunk_count", 0) > 0:
                resume_already_indexed = True
                log.info(f"Resume {self._resume_id} already indexed ({active['chunk_count']} chunks), skipping")

        if not resume_already_indexed:
            resume_chunks = chunker.split_resume(resume)
            if resume_id_str:
                for c in resume_chunks:
                    c.resume_id = resume_id_str
            chunks_to_embed.extend(resume_chunks)

        # Job chunking
        job_infos = {}
        for jid in job_ids:
            self._check_cancel()
            details = self.repo.get_scraped_details("geek", job_ids=[jid], limit=1)
            detail = details[0] if details else None
            job_info = self._build_job_info(detail)
            job_infos[jid] = job_info
            chunks = chunker.split_jd(jid, job_info.get("title", ""),
                                       job_info.get("jd", ""),
                                       job_info.get("skill_tags", []))
            chunks_to_embed.extend(chunks)

        # Supplement chunking
        for supp in (supplements or []):
            chunks = chunker.split_resume(supp, source="supplement")
            chunks_to_embed.extend(chunks)

        total = len(chunks_to_embed)
        self._bus.emit(MatchEvent("phase_start", "build_index", total=total))

        # Batch embedding + write to ChromaDB
        BATCH = 16
        vector_store = VectorStore()
        vector_store.clear_jobs()

        for i in range(0, total, BATCH):
            self._check_cancel()
            batch = chunks_to_embed[i:i + BATCH]
            texts = [c.text for c in batch]
            embeddings = self._embedder.embed(texts)
            vector_store.upsert_chunks(batch, embeddings)
            done = min(i + BATCH, total)
            self._bus.emit(MatchEvent("phase_progress", "build_index",
                                       current=done, total=total))

        self._vector_store = vector_store
        self._job_infos = job_infos
        self._retriever = Retriever(vector_store, self._embedder)
        self._bus.emit(MatchEvent("phase_done", "build_index"))

    @staticmethod
    def _build_job_info(detail) -> dict:
        """Convert scraped_detail dict to job_info dict for prompts."""
        if not detail:
            return {"title": "未知", "company": "未知"}
        return {
            "title": detail.get("title", ""),
            "company": detail.get("company_industry", ""),
            "salary": detail.get("salary", ""),
            "location": detail.get("location", ""),
            "company_scale": detail.get("company_scale", ""),
            "company_stage": detail.get("company_stage", ""),
            "company_industry": detail.get("company_industry", ""),
            "skill_tags": detail.get("skill_tags", []),
            "tags_list": detail.get("tags_list", []),
            "jd": detail.get("jd", ""),
        }

    # ------------------------------------------------------------------
    # Phase 2: Score jobs
    # ------------------------------------------------------------------

    def _score_jobs(self, job_ids, concurrency):
        self._check_cancel()
        self._current_task.phase = "job_scoring"
        self._bus.emit(MatchEvent("phase_start", "job_scoring", total=len(job_ids)))

        ai_client = AIClient(self.repo)
        results = []
        completed = 0

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {}
            for jid in job_ids:
                self._check_cancel()
                future = executor.submit(self._score_one_job, jid, ai_client)
                futures[future] = jid

            for future in as_completed(futures):
                if self._cancel_event.is_set() or self._auth_failed.is_set():
                    break

                jid = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    completed += 1

                    self.repo.save_match_result(
                        identity="geek", source_id=self._resume_id,
                        target_job_id=jid,
                        score=result.score,
                        reasoning=result.reasoning,
                        suggestions=result.suggestions,
                        model_name=result.model_name,
                        evidence=result.evidence,
                        gaps=result.gaps,
                        retrieved_chunks=result.retrieved_chunks,
                    )

                    self._bus.emit(MatchEvent(
                        "job_scored",
                        job_id=jid,
                        title=self._job_infos.get(jid, {}).get("title", ""),
                        score=result.score,
                        evidence=result.evidence,
                        reasoning=result.reasoning,
                        gaps=result.gaps,
                        suggestions=result.suggestions,
                        retrieved_chunks=result.retrieved_chunks,
                    ))
                except AuthFailedError as e:
                    self._auth_failed.set()
                    self._bus.emit(MatchEvent(
                        "job_failed", job_id=jid,
                        title=self._job_infos.get(jid, {}).get("title", ""),
                        error=str(e),
                    ))
                    raise  # re-raise so _orchestrate catches it
                except CancelledError:
                    raise  # re-raise so _orchestrate catches it
                except Exception as e:
                    error_msg = str(e)
                    if "认证失败" in error_msg:
                        self._auth_failed.set()
                    self._bus.emit(MatchEvent(
                        "job_failed", job_id=jid,
                        title=self._job_infos.get(jid, {}).get("title", ""),
                        error=error_msg,
                    ))

                self._current_task.completed = completed
                self._bus.emit(MatchEvent("phase_progress", "job_scoring",
                                           current=completed, total=len(job_ids)))

        self._bus.emit(MatchEvent("phase_done", "job_scoring", completed=completed))
        return results

    def _score_one_job(self, job_id, ai_client):
        if self._auth_failed.is_set():
            raise AuthFailedError("认证失败，已中止")
        self._check_cancel()

        job_info = self._job_infos[job_id]
        return self.score_single_job(
            job_id, job_info, ai_client,
            self._embedder, self._retriever, self._resume_id,
        )

    @staticmethod
    def score_single_job(job_id, job_info, ai_client,
                         embedder, retriever, resume_id,
                         cancel_event=None, auth_failed_event=None):
        """Score a single job — reusable by Pipeline and Matcher.

        Args:
            job_id: Job identifier string.
            job_info: Dict with title, skill_tags, etc.
            ai_client: AIClient instance.
            embedder: Embedder instance.
            retriever: Retriever instance.
            resume_id: Resume ID for filtering.
            cancel_event: Optional threading.Event for cancellation.
            auth_failed_event: Optional threading.Event for auth failure.
        """
        if auth_failed_event and auth_failed_event.is_set():
            raise AuthFailedError("认证失败，已中止")
        if cancel_event and cancel_event.is_set():
            raise CancelledError("用户取消")

        skill_tags = job_info.get("skill_tags", [])
        if not isinstance(skill_tags, list):
            skill_tags = []
        query_text = f"{job_info.get('title', '')} {' '.join(skill_tags)}"
        query_emb = embedder.embed_one(query_text)
        resume_id_str = str(resume_id) if resume_id else None
        retrieved = retriever.retrieve_for_job(query_embedding=query_emb, top_k=5, resume_id=resume_id_str)

        user_prompt = build_match_user_prompt(job_info, retrieved)
        chunk_ids = [c.chunk_id for c in retrieved]

        try:
            return ai_client.match_with_evidence(job_id, user_prompt, chunk_ids)
        except ValueError as e:
            if "认证失败" in str(e):
                if auth_failed_event:
                    auth_failed_event.set()
                raise AuthFailedError(str(e))
            raise

    # ------------------------------------------------------------------
    # Phase 3: Generate summary
    # ------------------------------------------------------------------

    def _generate_summary(self, results):
        if not results:
            self._bus.emit(MatchEvent("summary_done", structured=None, raw="无匹配结果"))
            return

        self._check_cancel()
        self._current_task.phase = "summary"
        self._bus.emit(MatchEvent("phase_start", "summary"))

        supplement_chunks = self._retriever.retrieve_supplements(top_k=3)

        user_prompt = build_summary_user_prompt(
            results, self._job_infos, supplement_chunks
        )

        streamer = SummaryStreamer(AIClient(self.repo), self._bus, self._cancel_event)
        summary = streamer.stream(user_prompt)

        self.repo.save_match_summary(
            identity="geek", source_id=self._resume_id,
            structured=summary.structured,
            raw_text=summary.raw,
        )

        self._bus.emit(MatchEvent("phase_done", "summary"))
