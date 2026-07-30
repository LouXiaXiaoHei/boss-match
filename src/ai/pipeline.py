"""Search-Match Pipeline: scrape → detail → score → summary in one flow."""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from src.ai.chunker import Chunker
from src.ai.client import AIClient
from src.ai.embedder import Embedder
from src.ai.event_bus import EventBus, MatchEvent
from src.ai.matcher import Matcher
from src.ai.prompts import build_summary_user_prompt
from src.ai.retriever import Retriever
from src.ai.summarizer import SummaryStreamer, CancelledError, AuthFailedError
from src.ai.vector_store import VectorStore
from src.core.scraper import scrape_list, scrape_details
from src.core.city import resolve_city
from src.core.chrome import is_cdp_ready
from src.core.constants import GEEK_CDP_PORT
from src.db.repository import Repository

log = logging.getLogger(__name__)


class PipelineTask:
    """Tracks an in-progress or completed pipeline task."""

    def __init__(self, task_id: int, keyword: str, city: str, max_pages: int,
                 resume_id: int):
        self.task_id = task_id
        self.keyword = keyword
        self.city = city
        self.max_pages = max_pages
        self.resume_id = resume_id
        self.cancel_event = threading.Event()
        self.auth_failed_event = threading.Event()
        self.status = "running"
        self.phase = "init"           # init, scraping, scoring, summary
        self.jobs_found = 0
        self.details_scraped = 0
        self.details_skipped = 0
        self.jobs_scored = 0
        self.jobs_skipped = 0
        self.current_page = 0
        self.total_pages = max_pages
        self.error_message = ""
        self._start_time = time.time()
        self.elapsed_ms = 0

    def update_elapsed(self):
        self.elapsed_ms = int((time.time() - self._start_time) * 1000)

    def to_dict(self) -> dict:
        self.update_elapsed()
        return {
            "type": "pipeline",
            "task_id": self.task_id,
            "status": self.status,
            "phase": self.phase,
            "keyword": self.keyword,
            "city": self.city,
            "jobs_found": self.jobs_found,
            "details_scraped": self.details_scraped,
            "details_skipped": self.details_skipped,
            "jobs_scored": self.jobs_scored,
            "jobs_skipped": self.jobs_skipped,
            "current_page": self.current_page,
            "total_pages": self.total_pages,
            "resume_id": self.resume_id,
            "elapsed_ms": self.elapsed_ms,
            "error_message": self.error_message,
        }


class SearchMatchPipeline:
    """One-flow pipeline: scrape jobs → scrape details → score → summary."""

    def __init__(self, repo: Repository, notify_callback: Callable | None = None,
                 embedder: Embedder | None = None):
        self.repo = repo
        self._notify = notify_callback
        self._embedder = embedder
        self._lock = threading.Lock()
        self._current_task: PipelineTask | None = None

    def _check_cancel(self):
        if self._current_task and self._current_task.cancel_event.is_set():
            raise CancelledError("用户取消")

    def _emit(self, event: MatchEvent):
        if self._bus:
            self._bus.emit(event)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, resume_id: int, keyword: str, city: str,
              max_pages: int = 3, filters: dict | None = None,
              min_score: float = 0.0, supplements: list[str] | None = None,
              concurrency: int = 3) -> dict:
        """Start the pipeline. Returns {ok, data: {task_id}} or error."""
        with self._lock:
            if self._current_task and self._current_task.status == "running":
                return {"ok": False, "error": "已有流水线任务进行中"}

        if not is_cdp_ready(GEEK_CDP_PORT):
            return {"ok": False, "error": "Chrome 未启动或未登录"}

        resume = self.repo.get_resume_by_id(resume_id)
        if not resume or not resume.get("content", "").strip():
            return {"ok": False, "error": "简历内容为空，请先保存简历"}

        city_name, _ = resolve_city(city)
        log_id = self.repo.create_scrape_log(
            identity="geek", task_type="pipeline",
            keyword=keyword, city=city_name,
        )

        task = PipelineTask(log_id, keyword, city_name, max_pages, resume_id)
        with self._lock:
            self._current_task = task

        self._bus = EventBus(self._notify)
        self._bus.start()

        thread = threading.Thread(
            target=self._run_pipeline,
            args=(task, resume["content"], filters or {},
                  min_score, supplements or [], concurrency),
            daemon=True,
        )
        thread.start()
        return {"ok": True, "data": {"task_id": log_id}}

    def get_progress(self) -> dict:
        with self._lock:
            task = self._current_task
        if not task:
            return {"ok": True, "data": {"type": "pipeline", "status": "idle"}}
        return {"ok": True, "data": task.to_dict()}

    def cancel(self) -> dict:
        with self._lock:
            task = self._current_task
        if not task or task.status != "running":
            return {"ok": False, "error": "没有进行中的流水线任务"}
        task.cancel_event.set()
        return {"ok": True}

    # ------------------------------------------------------------------
    # Pipeline orchestration
    # ------------------------------------------------------------------

    def _run_pipeline(self, task: PipelineTask, resume_text: str,
                      filters: dict, min_score: float,
                      supplements: list[str], concurrency: int):
        try:
            # Phase 0: Init embedder
            task.phase = "init"
            self._emit(MatchEvent("phase_start", "init_model"))
            self._init_embedder()
            self._emit(MatchEvent("phase_done", "init_model"))

            # Phase 1: Ensure resume is indexed
            self._ensure_resume_indexed(task, resume_text, supplements)

            # Phase 2: Scrape + score flow
            self._scrape_and_score(task, resume_text, filters, min_score,
                                   supplements, concurrency)

            # Phase 3: Summary
            self._generate_pipeline_summary(task)

            task.status = "completed"
            task.update_elapsed()
            self._emit(MatchEvent("pipeline_completed",
                                  total_duration_ms=task.elapsed_ms))
        except CancelledError:
            task.status = "cancelled"
            self._emit(MatchEvent("cancelled"))
        except AuthFailedError as e:
            task.status = "failed"
            task.error_message = str(e)
            self._emit(MatchEvent("error", error=str(e), fatal=True))
        except Exception as e:
            task.status = "failed"
            task.error_message = str(e)
            log.exception("Pipeline failed")
            self._emit(MatchEvent("error", error=str(e)))
        finally:
            self._bus.stop()

    def _init_embedder(self):
        self._check_cancel()
        if self._embedder and self._embedder._model is not None:
            return

        def progress_callback(progress, status, speed=0.0):
            self._check_cancel()
            self._emit(MatchEvent("model_download_progress",
                                   progress=progress, status=status, speed=speed))

        self._embedder = Embedder()
        self._embedder.ensure_model(progress_callback)

    def _ensure_resume_indexed(self, task: PipelineTask,
                               resume_text: str, supplements: list[str]):
        """Ensure resume chunks are in ChromaDB. Build if needed."""
        self._check_cancel()
        task.phase = "scoring"  # will set properly in _scrape_and_score
        resume_id_str = str(task.resume_id)

        active = self.repo.get_resume_by_id(task.resume_id)
        if active and active.get("chunk_count", 0) > 0:
            log.info(f"Resume {task.resume_id} already indexed, skipping")
            return

        chunker = Chunker()
        chunks = chunker.split_resume(resume_text, source="resume")
        for c in chunks:
            c.resume_id = resume_id_str

        for supp in supplements:
            supp_chunks = chunker.split_resume(supp, source="supplement")
            chunks.extend(supp_chunks)

        if not chunks:
            return

        embeddings = self._embedder.embed([c.text for c in chunks])
        vs = VectorStore()
        vs.clear_resume(resume_id_str)
        vs.upsert_chunks(chunks, embeddings)
        self.repo.update_resume_chunk_count(task.resume_id, len(chunks))
        log.info(f"Resume {task.resume_id} indexed: {len(chunks)} chunks")

    # ------------------------------------------------------------------
    # Phase 2: Scrape + Score
    # ------------------------------------------------------------------

    def _scrape_and_score(self, task: PipelineTask, resume_text: str,
                          filters: dict, min_score: float,
                          supplements: list[str], concurrency: int):
        """Scrape pages incrementally, score each batch as details arrive."""
        self._check_cancel()
        task.phase = "scraping"
        self._emit(MatchEvent("phase_start", "pipeline_scraping",
                               keyword=task.keyword, city=task.city))

        # Prepare scoring infrastructure
        resume_id_str = str(task.resume_id)
        vector_store = VectorStore()
        retriever = Retriever(vector_store, self._embedder)
        ai_client = AIClient(self.repo)

        # Build job_info cache from DB details
        job_infos: dict[str, dict] = {}
        scored_job_ids: set[str] = set(self._get_scored_job_ids(task.resume_id))

        # Scrape list with per-page callback
        all_jobs = []

        def on_page_done(page, max_pages, jobs_found, phase, new_jobs=None):
            self._check_cancel()
            task.current_page = page
            task.total_pages = max_pages
            task.jobs_found = jobs_found
            task.phase = "scraping"

            if new_jobs:
                self.repo.save_scraped_jobs(new_jobs, "geek",
                                            keyword=task.keyword, city=task.city)
                all_jobs.extend(new_jobs)

                # Immediately try to score newly scraped jobs
                self._score_batch(
                    new_jobs, task, job_infos, scored_job_ids,
                    ai_client, retriever, resume_id_str, min_score, concurrency,
                )

            self._emit(MatchEvent("phase_progress", "pipeline_scraping",
                                   current=page, total=max_pages,
                                   jobs_found=jobs_found,
                                   jobs_scored=task.jobs_scored))

        list_result = scrape_list(
            task.keyword, task.city, task.max_pages, filters,
            identity="geek",
            progress_callback=on_page_done,
            cancel_event=task.cancel_event,
        )

        if task.cancel_event.is_set():
            raise CancelledError("用户取消")

        if not list_result.get("ok"):
            raise RuntimeError(list_result.get("error", "列表抓取失败"))

        # Scrape details for jobs that don't have them yet
        task.phase = "scoring"
        self._emit(MatchEvent("phase_start", "pipeline_scoring",
                               total=task.jobs_found))

        if all_jobs:
            all_job_ids = [j.get("job_id", "") for j in all_jobs if j.get("job_id")]
            existing_ids = self.repo.get_existing_detail_job_ids("geek", all_job_ids)
            jobs_to_scrape = [j for j in all_jobs
                              if j.get("job_id", "") not in existing_ids]
            task.details_skipped = len(all_jobs) - len(jobs_to_scrape)

            if jobs_to_scrape:
                def on_detail_done(idx, total, _jf, phase, detail=None):
                    self._check_cancel()
                    task.details_scraped = idx
                    if detail:
                        self.repo.save_scraped_detail_single(detail, "geek")
                        # Score this detail immediately
                        jid = detail.get("job_id", "")
                        if jid and jid not in scored_job_ids:
                            self._score_batch(
                                [detail], task, job_infos, scored_job_ids,
                                ai_client, retriever, resume_id_str,
                                min_score, concurrency=1,
                            )

                scrape_details(
                    jobs_to_scrape, identity="geek",
                    progress_callback=on_detail_done,
                    cancel_event=task.cancel_event,
                )

            # Score any remaining jobs that have details but weren't scored
            remaining = [j for j in all_jobs
                         if j.get("job_id", "") not in scored_job_ids]
            if remaining:
                self._score_batch(
                    remaining, task, job_infos, scored_job_ids,
                    ai_client, retriever, resume_id_str, min_score, concurrency,
                )

        self._emit(MatchEvent("phase_done", "pipeline_scoring",
                               jobs_scored=task.jobs_scored))

    def _score_batch(self, jobs: list, task: PipelineTask,
                     job_infos: dict, scored_job_ids: set,
                     ai_client: AIClient, retriever: Retriever,
                     resume_id_str: str, min_score: float,
                     concurrency: int):
        """Score a batch of jobs. Skips already-scored and missing details."""
        to_score = []
        for job in jobs:
            jid = job.get("job_id", "")
            if not jid or jid in scored_job_ids:
                task.jobs_skipped += 1
                continue

            # Build job_info — try DB details first, then fall back to list data
            if jid not in job_infos:
                details = self.repo.get_scraped_details("geek", job_ids=[jid], limit=1)
                if details:
                    job_infos[jid] = Matcher._build_job_info(None, details[0])
                elif job.get("title"):
                    job_infos[jid] = {
                        "title": job.get("title", ""),
                        "company": job.get("boss_name", ""),
                        "salary": job.get("salary", ""),
                        "location": job.get("location", ""),
                        "skill_tags": [],
                        "tags_list": [],
                        "jd": "",
                    }
                else:
                    continue  # No usable info

            to_score.append(jid)

        if not to_score:
            return

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {}
            for jid in to_score:
                self._check_cancel()
                future = executor.submit(
                    Matcher.score_single_job,
                    jid, job_infos[jid], ai_client,
                    self._embedder, retriever, task.resume_id,
                    task.cancel_event, task.auth_failed_event,
                )
                futures[future] = jid

            for future in as_completed(futures):
                if task.cancel_event.is_set() or task.auth_failed_event.is_set():
                    break

                jid = futures[future]
                try:
                    result = future.result()
                    scored_job_ids.add(jid)
                    task.jobs_scored += 1

                    # Filter by min_score
                    if min_score > 0 and result.score < min_score:
                        continue

                    self.repo.save_match_result(
                        identity="geek", source_id=task.resume_id,
                        target_job_id=jid,
                        score=result.score,
                        reasoning=result.reasoning,
                        suggestions=result.suggestions,
                        model_name=result.model_name,
                        evidence=result.evidence,
                        gaps=result.gaps,
                        retrieved_chunks=result.retrieved_chunks,
                    )

                    self._emit(MatchEvent(
                        "job_scored",
                        job_id=jid,
                        title=job_infos.get(jid, {}).get("title", ""),
                        score=result.score,
                        evidence=result.evidence,
                        reasoning=result.reasoning,
                        gaps=result.gaps,
                        suggestions=result.suggestions,
                        retrieved_chunks=result.retrieved_chunks,
                    ))
                except AuthFailedError:
                    task.auth_failed_event.set()
                    raise
                except CancelledError:
                    raise
                except Exception as e:
                    error_msg = str(e)
                    if "认证失败" in error_msg:
                        task.auth_failed_event.set()
                    self._emit(MatchEvent(
                        "job_failed", job_id=jid,
                        title=job_infos.get(jid, {}).get("title", ""),
                        error=error_msg,
                    ))

                self._emit(MatchEvent("phase_progress", "pipeline_scoring",
                                       jobs_scored=task.jobs_scored,
                                       jobs_found=task.jobs_found))

    def _get_scored_job_ids(self, resume_id: int) -> list[str]:
        """Get already-scored job IDs for this resume from DB."""
        results = self.repo.get_match_results("geek", source_id=resume_id, limit=10000)
        return [r.get("target_job_id", "") for r in results if r.get("target_job_id")]

    # ------------------------------------------------------------------
    # Phase 3: Summary
    # ------------------------------------------------------------------

    def _generate_pipeline_summary(self, task: PipelineTask):
        results = self.repo.get_match_results("geek", source_id=task.resume_id, limit=10000)
        if not results:
            self._emit(MatchEvent("summary_done", structured=None, raw="无匹配结果"))
            return

        self._check_cancel()
        task.phase = "summary"
        self._emit(MatchEvent("phase_start", "summary"))

        # Build job_infos from match results
        job_infos = {}
        for r in results:
            jid = r.get("target_job_id", "")
            details = self.repo.get_scraped_details("geek", job_ids=[jid], limit=1)
            if details:
                job_infos[jid] = Matcher._build_job_info(None, details[0])

        # Convert DB rows to score-like objects for summary prompt
        from src.ai.client import JobScoreResult
        score_results = []
        for r in results:
            score_results.append(JobScoreResult(
                job_id=r.get("target_job_id", ""),
                score=r.get("score", 0),
                evidence=r.get("evidence", []) if isinstance(r.get("evidence"), list) else [],
                reasoning=r.get("reasoning", ""),
                gaps=r.get("gaps", []) if isinstance(r.get("gaps"), list) else [],
                suggestions=r.get("suggestions", []) if isinstance(r.get("suggestions"), list) else [],
                model_name=r.get("model_name", ""),
                retrieved_chunks=r.get("retrieved_chunks", []) if isinstance(r.get("retrieved_chunks"), list) else [],
            ))

        vector_store = VectorStore()
        retriever = Retriever(vector_store, self._embedder)
        supplement_chunks = retriever.retrieve_supplements(top_k=3)

        user_prompt = build_summary_user_prompt(score_results, job_infos, supplement_chunks)

        streamer = SummaryStreamer(AIClient(self.repo), self._bus, task.cancel_event)
        summary = streamer.stream(user_prompt)

        self.repo.save_match_summary(
            identity="geek", source_id=task.resume_id,
            structured=summary.structured,
            raw_text=summary.raw,
        )

        self._emit(MatchEvent("phase_done", "summary"))
