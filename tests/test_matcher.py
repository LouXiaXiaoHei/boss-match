"""Tests for three-phase RAG Matcher orchestrator."""

import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.ai.matcher import Matcher, MatchTask
from src.ai.event_bus import MatchEvent
from src.ai.summarizer import CancelledError, AuthFailedError, SummaryResult
from src.ai.chunker import Chunk
from src.ai.vector_store import RetrievalResult
from src.ai.client import JobScoreResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_repo(**overrides):
    """Create a mock Repository with sensible defaults."""
    repo = MagicMock()
    repo.create_scrape_log.return_value = 1
    repo.get_scraped_details.return_value = []
    repo.save_match_result.return_value = None
    repo.save_match_summary.return_value = None
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def _make_job_detail(job_id="j1", title="Python Dev", jd="Write code",
                     skill_tags=None, company_industry="Tech"):
    return {
        "job_id": job_id,
        "title": title,
        "jd": jd,
        "skill_tags": skill_tags or ["Python"],
        "tags_list": [],
        "company_industry": company_industry,
        "salary": "20-40K",
        "location": "Beijing",
        "company_scale": "500-1000",
        "company_stage": "B轮",
    }


def _make_score_result(job_id="j1", score=0.8):
    return JobScoreResult(
        job_id=job_id,
        score=score,
        evidence=[{"claim": "test", "source": "r1", "relevance": "high"}],
        reasoning="Good match",
        gaps=["Need more Go"],
        suggestions=["Learn Go"],
        model_name="test-model",
        retrieved_chunks=["r1"],
    )


def _make_retrieval_result(chunk_id="r1", text="resume text", source="resume",
                           section="skills", score=0.9):
    return RetrievalResult(
        chunk_id=chunk_id,
        text=text,
        source=source,
        section=section,
        score=score,
        metadata={},
    )


def _collect_events(matcher, timeout=5.0):
    """Collect all events emitted during a match run.

    Patches EventBus.emit to capture events in a list.
    Returns list of MatchEvent objects.
    """
    events = []
    original_emit = matcher._bus.emit if hasattr(matcher, '_bus') else None

    def capture_emit(event):
        events.append(event)

    return events, capture_emit


# ---------------------------------------------------------------------------
# MatchTask tests
# ---------------------------------------------------------------------------

class TestMatchTask:
    def test_init_defaults(self):
        task = MatchTask(task_id=1, total_jobs=5)
        assert task.task_id == 1
        assert task.total_jobs == 5
        assert task.completed == 0
        assert task.phase == ""
        assert task.index_progress == 0.0
        assert task.status == "running"
        assert task.error_message == ""

    def test_update_elapsed(self):
        task = MatchTask(task_id=1, total_jobs=5)
        time.sleep(0.05)
        task.update_elapsed()
        assert task.elapsed_ms > 0

    def test_to_dict(self):
        task = MatchTask(task_id=1, total_jobs=5)
        d = task.to_dict()
        assert d["type"] == "match"
        assert d["task_id"] == 1
        assert d["status"] == "running"
        assert "elapsed_ms" in d


# ---------------------------------------------------------------------------
# Matcher start_match validation
# ---------------------------------------------------------------------------

class TestMatcherStartMatch:
    def test_empty_resume_rejected(self):
        m = Matcher(_make_repo())
        result = m.start_match("", ["j1"])
        assert result["ok"] is False
        assert "简历" in result["error"]

    def test_empty_job_ids_rejected(self):
        m = Matcher(_make_repo())
        result = m.start_match("my resume", [])
        assert result["ok"] is False
        assert "职位" in result["error"]

    def test_already_running_rejected(self):
        m = Matcher(_make_repo())
        m._current_task = MatchTask(task_id=1, total_jobs=5)
        m._current_task.status = "running"
        result = m.start_match("resume", ["j1"])
        assert result["ok"] is False
        assert "进行中" in result["error"]

    def test_start_returns_task_id(self):
        repo = _make_repo(create_scrape_log=MagicMock(return_value=42))
        m = Matcher(repo)
        # We need to prevent the background thread from actually running
        m._current_task = None
        with patch.object(threading, 'Thread') as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            result = m.start_match("my resume", ["j1"])
        assert result["ok"] is True
        assert result["data"]["task_id"] == 42


# ---------------------------------------------------------------------------
# cancel / get_progress
# ---------------------------------------------------------------------------

class TestMatcherCancel:
    def test_cancel_no_running_task(self):
        m = Matcher(_make_repo())
        result = m.cancel()
        assert result["ok"] is False

    def test_cancel_running_task(self):
        m = Matcher(_make_repo())
        m._current_task = MatchTask(task_id=1, total_jobs=5)
        m._current_task.status = "running"
        result = m.cancel()
        assert result["ok"] is True
        assert m._cancel_event.is_set()


class TestMatcherGetProgress:
    def test_idle_when_no_task(self):
        m = Matcher(_make_repo())
        result = m.get_progress()
        assert result["ok"] is True
        assert result["data"]["status"] == "idle"

    def test_returns_task_dict(self):
        m = Matcher(_make_repo())
        m._current_task = MatchTask(task_id=1, total_jobs=5)
        result = m.get_progress()
        assert result["ok"] is True
        assert result["data"]["task_id"] == 1


# ---------------------------------------------------------------------------
# Three-phase orchestration integration tests (with mocks)
# ---------------------------------------------------------------------------

class TestOrchestration:
    """Test the full three-phase orchestration with all dependencies mocked."""

    def _run_matcher(self, resume="my resume", job_ids=None, supplements=None,
                     concurrency=1, repo_overrides=None):
        """Run matcher synchronously by calling _orchestrate directly."""
        repo = _make_repo(**(repo_overrides or {}))
        if job_ids is None:
            job_ids = ["j1"]

        # Set up scraped detail for job
        repo.get_scraped_details.return_value = [_make_job_detail()]

        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=len(job_ids))
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        # Create a real EventBus but with a no-op callback
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        m._bus.start()

        return m

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_three_phases_execute(self, MockStreamer, MockRetriever,
                                   MockAIClient, MockChunker,
                                   MockEmbedder, MockVectorStore):
        """Verify all three phases execute in order and emit correct events."""
        # Setup mocks
        embedder = MockEmbedder.return_value
        embedder.ensure_model.return_value = None
        embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        embedder.embed_one.return_value = [0.1, 0.2, 0.3]

        chunker = MockChunker.return_value
        chunker.split_resume.return_value = [Chunk(id="r0", text="resume", source="resume")]
        chunker.split_jd.return_value = [Chunk(id="j1_0", text="job", source="job", job_id="j1")]

        vector_store = MockVectorStore.return_value
        vector_store.clear_jobs.return_value = None
        vector_store.upsert_chunks.return_value = None

        retriever = MockRetriever.return_value
        retriever.retrieve_for_job.return_value = [_make_retrieval_result()]
        retriever.retrieve_supplements.return_value = []

        ai_client = MockAIClient.return_value
        score_result = _make_score_result()
        ai_client.match_with_evidence.return_value = score_result

        streamer = MockStreamer.return_value
        streamer.stream.return_value = SummaryResult(
            structured={"skill_analysis": {}}, raw='{"skill_analysis": {}}'
        )

        # Run
        repo = _make_repo()
        repo.get_scraped_details.return_value = [_make_job_detail()]
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], None, 1)

        # Verify phase events
        event_types = [e.type for e in events]
        assert "phase_start" in event_types
        assert "phase_done" in event_types

        # Check phase order
        phases_started = [e.phase for e in events if e.type == "phase_start"]
        phases_done = [e.phase for e in events if e.type == "phase_done"]
        assert phases_started == ["init_model", "build_index", "job_scoring", "summary"]
        assert phases_done == ["init_model", "build_index", "job_scoring", "summary"]

        # Verify job_scored event
        assert "job_scored" in event_types

        # Verify match_completed
        assert "match_completed" in event_types

        # Verify task completed
        assert m._current_task.status == "completed"

        m._bus.stop()

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_cancel_during_init_model(self, MockStreamer, MockRetriever,
                                       MockAIClient, MockChunker,
                                       MockEmbedder, MockVectorStore):
        """Cancel during phase 0 raises CancelledError and emits 'cancelled'."""
        embedder = MockEmbedder.return_value

        def ensure_model_cb(callback):
            # Simulate cancel during model init
            raise CancelledError("用户取消")

        embedder.ensure_model.side_effect = ensure_model_cb

        repo = _make_repo()
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], None, 1)

        event_types = [e.type for e in events]
        assert "cancelled" in event_types
        assert m._current_task.status == "cancelled"

        m._bus.stop()

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_cancel_during_build_index(self, MockStreamer, MockRetriever,
                                        MockAIClient, MockChunker,
                                        MockEmbedder, MockVectorStore):
        """Cancel during phase 1 raises CancelledError and emits 'cancelled'."""
        embedder = MockEmbedder.return_value
        embedder.ensure_model.return_value = None
        embedder.embed.return_value = [[0.1, 0.2, 0.3]]

        chunker = MockChunker.return_value
        chunker.split_resume.return_value = [Chunk(id="r0", text="resume", source="resume")]
        chunker.split_jd.return_value = [Chunk(id="j1_0", text="job", source="job", job_id="j1")]

        vector_store = MockVectorStore.return_value

        # Cancel will be checked in _build_index loop
        def upsert_chunks_side_effect(chunks, embeddings):
            raise CancelledError("用户取消")

        vector_store.upsert_chunks.side_effect = upsert_chunks_side_effect
        vector_store.clear_jobs.return_value = None

        repo = _make_repo()
        repo.get_scraped_details.return_value = [_make_job_detail()]
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], None, 1)

        event_types = [e.type for e in events]
        assert "cancelled" in event_types
        assert m._current_task.status == "cancelled"

        m._bus.stop()

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_auth_failed_emits_fatal_error(self, MockStreamer, MockRetriever,
                                            MockAIClient, MockChunker,
                                            MockEmbedder, MockVectorStore):
        """AuthFailedError during scoring emits 'error' event with fatal=True."""
        embedder = MockEmbedder.return_value
        embedder.ensure_model.return_value = None
        embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        embedder.embed_one.return_value = [0.1, 0.2, 0.3]

        chunker = MockChunker.return_value
        chunker.split_resume.return_value = [Chunk(id="r0", text="resume", source="resume")]
        chunker.split_jd.return_value = [Chunk(id="j1_0", text="job", source="job", job_id="j1")]

        vector_store = MockVectorStore.return_value
        vector_store.clear_jobs.return_value = None
        vector_store.upsert_chunks.return_value = None

        retriever = MockRetriever.return_value
        retriever.retrieve_for_job.return_value = [_make_retrieval_result()]

        ai_client = MockAIClient.return_value
        ai_client.match_with_evidence.side_effect = AuthFailedError("API 认证失败")

        repo = _make_repo()
        repo.get_scraped_details.return_value = [_make_job_detail()]
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], None, 1)

        # Find the error event
        error_events = [e for e in events if e.type == "error"]
        assert len(error_events) >= 1
        assert error_events[0].data.get("fatal") is True
        assert "认证失败" in error_events[0].data.get("error", "")
        assert m._current_task.status == "failed"

        m._bus.stop()

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_job_scoring_emits_events(self, MockStreamer, MockRetriever,
                                       MockAIClient, MockChunker,
                                       MockEmbedder, MockVectorStore):
        """Job scoring emits job_scored and phase_progress events."""
        embedder = MockEmbedder.return_value
        embedder.ensure_model.return_value = None
        embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        embedder.embed_one.return_value = [0.1, 0.2, 0.3]

        chunker = MockChunker.return_value
        chunker.split_resume.return_value = [Chunk(id="r0", text="resume", source="resume")]
        chunker.split_jd.return_value = [Chunk(id="j1_0", text="job", source="job", job_id="j1")]

        vector_store = MockVectorStore.return_value
        vector_store.clear_jobs.return_value = None
        vector_store.upsert_chunks.return_value = None

        retriever = MockRetriever.return_value
        retriever.retrieve_for_job.return_value = [_make_retrieval_result()]
        retriever.retrieve_supplements.return_value = []

        score_result = _make_score_result()
        ai_client = MockAIClient.return_value
        ai_client.match_with_evidence.return_value = score_result

        streamer = MockStreamer.return_value
        streamer.stream.return_value = SummaryResult(
            structured={"skill_analysis": {}}, raw='{"skill_analysis": {}}'
        )

        repo = _make_repo()
        repo.get_scraped_details.return_value = [_make_job_detail()]
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], None, 1)

        # Check job_scored event
        scored_events = [e for e in events if e.type == "job_scored"]
        assert len(scored_events) == 1
        assert scored_events[0].data["job_id"] == "j1"
        assert scored_events[0].data["score"] == 0.8

        # Check phase_progress for job_scoring
        progress_events = [e for e in events
                          if e.type == "phase_progress" and e.phase == "job_scoring"]
        assert len(progress_events) >= 1

        m._bus.stop()

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_summary_streaming_events(self, MockStreamer, MockRetriever,
                                       MockAIClient, MockChunker,
                                       MockEmbedder, MockVectorStore):
        """Summary streaming emits summary_chunk + summary_done events."""
        embedder = MockEmbedder.return_value
        embedder.ensure_model.return_value = None
        embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        embedder.embed_one.return_value = [0.1, 0.2, 0.3]

        chunker = MockChunker.return_value
        chunker.split_resume.return_value = [Chunk(id="r0", text="resume", source="resume")]
        chunker.split_jd.return_value = [Chunk(id="j1_0", text="job", source="job", job_id="j1")]

        vector_store = MockVectorStore.return_value
        vector_store.clear_jobs.return_value = None
        vector_store.upsert_chunks.return_value = None

        retriever = MockRetriever.return_value
        retriever.retrieve_for_job.return_value = [_make_retrieval_result()]
        retriever.retrieve_supplements.return_value = []

        score_result = _make_score_result()
        ai_client = MockAIClient.return_value
        ai_client.match_with_evidence.return_value = score_result

        # Use real SummaryStreamer behavior: emit summary_chunk then summary_done
        streamer = MockStreamer.return_value
        # Simulate the streamer emitting events through the bus
        def fake_stream(user_prompt):
            # Emit a chunk event (simulating what real streamer does)
            # But we can't access the bus here, so we just return the result
            # The real SummaryStreamer emits these events internally
            return SummaryResult(structured={"skill_analysis": {}}, raw='{"skill_analysis": {}}')

        streamer.stream.side_effect = fake_stream

        repo = _make_repo()
        repo.get_scraped_details.return_value = [_make_job_detail()]
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], None, 1)

        # Verify summary phase events
        summary_start = [e for e in events if e.type == "phase_start" and e.phase == "summary"]
        summary_done = [e for e in events if e.type == "phase_done" and e.phase == "summary"]
        assert len(summary_start) == 1
        assert len(summary_done) == 1

        # Verify save_match_summary was called
        repo.save_match_summary.assert_called_once()

        m._bus.stop()

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_no_results_skips_summary(self, MockStreamer, MockRetriever,
                                       MockAIClient, MockChunker,
                                       MockEmbedder, MockVectorStore):
        """When all jobs fail, no results → summary_done with raw='无匹配结果'."""
        embedder = MockEmbedder.return_value
        embedder.ensure_model.return_value = None
        embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        embedder.embed_one.return_value = [0.1, 0.2, 0.3]

        chunker = MockChunker.return_value
        chunker.split_resume.return_value = [Chunk(id="r0", text="resume", source="resume")]
        chunker.split_jd.return_value = [Chunk(id="j1_0", text="job", source="job", job_id="j1")]

        vector_store = MockVectorStore.return_value
        vector_store.clear_jobs.return_value = None
        vector_store.upsert_chunks.return_value = None

        retriever = MockRetriever.return_value
        retriever.retrieve_for_job.return_value = [_make_retrieval_result()]

        # All jobs fail
        ai_client = MockAIClient.return_value
        ai_client.match_with_evidence.side_effect = Exception("API error")

        repo = _make_repo()
        repo.get_scraped_details.return_value = [_make_job_detail()]
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], None, 1)

        # Should emit summary_done with raw="无匹配结果"
        summary_done_events = [e for e in events if e.type == "summary_done"]
        assert len(summary_done_events) == 1
        assert summary_done_events[0].data.get("raw") == "无匹配结果"

        m._bus.stop()

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_supplements_chunked(self, MockStreamer, MockRetriever,
                                  MockAIClient, MockChunker,
                                  MockEmbedder, MockVectorStore):
        """Supplement materials are chunked and embedded."""
        embedder = MockEmbedder.return_value
        embedder.ensure_model.return_value = None
        embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        embedder.embed_one.return_value = [0.1, 0.2, 0.3]

        chunker = MockChunker.return_value
        chunker.split_resume.return_value = [Chunk(id="r0", text="resume", source="resume")]
        # Second call is for supplement
        chunker.split_jd.return_value = [Chunk(id="j1_0", text="job", source="job", job_id="j1")]

        # split_resume called twice: once for resume, once for supplement
        supp_chunk = Chunk(id="s0", text="supplement text", source="supplement")
        chunker.split_resume.side_effect = [
            [Chunk(id="r0", text="resume", source="resume")],
            [supp_chunk],
        ]

        vector_store = MockVectorStore.return_value
        vector_store.clear_jobs.return_value = None
        vector_store.upsert_chunks.return_value = None

        retriever = MockRetriever.return_value
        retriever.retrieve_for_job.return_value = [_make_retrieval_result()]
        retriever.retrieve_supplements.return_value = []

        ai_client = MockAIClient.return_value
        ai_client.match_with_evidence.return_value = _make_score_result()

        streamer = MockStreamer.return_value
        streamer.stream.return_value = SummaryResult(
            structured={"skill_analysis": {}}, raw='{"skill_analysis": {}}'
        )

        repo = _make_repo()
        repo.get_scraped_details.return_value = [_make_job_detail()]
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], ["supplement text"], 1)

        # Verify split_resume was called with source="supplement"
        assert chunker.split_resume.call_count == 2
        second_call = chunker.split_resume.call_args_list[1]
        assert second_call[0][0] == "supplement text"
        assert second_call[1].get("source") == "supplement"

        m._bus.stop()

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_build_index_progress_events(self, MockStreamer, MockRetriever,
                                          MockAIClient, MockChunker,
                                          MockEmbedder, MockVectorStore):
        """Build index emits phase_progress events with current/total."""
        embedder = MockEmbedder.return_value
        embedder.ensure_model.return_value = None
        embedder.embed.return_value = [[0.1, 0.2, 0.3]] * 20
        embedder.embed_one.return_value = [0.1, 0.2, 0.3]

        # Create 20 chunks to test batching (BATCH=16)
        chunks = [Chunk(id=f"r{i}", text=f"text{i}", source="resume") for i in range(20)]
        chunker = MockChunker.return_value
        chunker.split_resume.return_value = chunks
        chunker.split_jd.return_value = []

        vector_store = MockVectorStore.return_value
        vector_store.clear_jobs.return_value = None
        vector_store.upsert_chunks.return_value = None

        retriever = MockRetriever.return_value
        retriever.retrieve_for_job.return_value = [_make_retrieval_result()]
        retriever.retrieve_supplements.return_value = []

        ai_client = MockAIClient.return_value
        ai_client.match_with_evidence.return_value = _make_score_result()

        streamer = MockStreamer.return_value
        streamer.stream.return_value = SummaryResult(
            structured={"skill_analysis": {}}, raw='{"skill_analysis": {}}'
        )

        repo = _make_repo()
        repo.get_scraped_details.return_value = [_make_job_detail()]
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], None, 1)

        # Check build_index progress events
        index_progress = [e for e in events
                         if e.type == "phase_progress" and e.phase == "build_index"]
        assert len(index_progress) >= 1
        # First batch: 16 of 20, second batch: 20 of 20
        assert index_progress[0].data["current"] == 16
        assert index_progress[0].data["total"] == 20
        assert index_progress[-1].data["current"] == 20

        m._bus.stop()

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_job_failed_event(self, MockStreamer, MockRetriever,
                               MockAIClient, MockChunker,
                               MockEmbedder, MockVectorStore):
        """Non-auth job failure emits job_failed event."""
        embedder = MockEmbedder.return_value
        embedder.ensure_model.return_value = None
        embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        embedder.embed_one.return_value = [0.1, 0.2, 0.3]

        chunker = MockChunker.return_value
        chunker.split_resume.return_value = [Chunk(id="r0", text="resume", source="resume")]
        chunker.split_jd.return_value = [Chunk(id="j1_0", text="job", source="job", job_id="j1")]

        vector_store = MockVectorStore.return_value
        vector_store.clear_jobs.return_value = None
        vector_store.upsert_chunks.return_value = None

        retriever = MockRetriever.return_value
        retriever.retrieve_for_job.return_value = [_make_retrieval_result()]

        ai_client = MockAIClient.return_value
        ai_client.match_with_evidence.side_effect = Exception("API rate limit")

        repo = _make_repo()
        repo.get_scraped_details.return_value = [_make_job_detail()]
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], None, 1)

        # Check job_failed event
        failed_events = [e for e in events if e.type == "job_failed"]
        assert len(failed_events) == 1
        assert failed_events[0].data["job_id"] == "j1"
        assert "API rate limit" in failed_events[0].data["error"]

        m._bus.stop()

    @patch('src.ai.matcher.VectorStore')
    @patch('src.ai.matcher.Embedder')
    @patch('src.ai.matcher.Chunker')
    @patch('src.ai.matcher.AIClient')
    @patch('src.ai.matcher.Retriever')
    @patch('src.ai.matcher.SummaryStreamer')
    def test_cancel_via_cancel_event(self, MockStreamer, MockRetriever,
                                      MockAIClient, MockChunker,
                                      MockEmbedder, MockVectorStore):
        """Setting cancel_event before scoring causes CancelledError."""
        embedder = MockEmbedder.return_value
        embedder.ensure_model.return_value = None
        embedder.embed.return_value = [[0.1, 0.2, 0.3]]
        embedder.embed_one.return_value = [0.1, 0.2, 0.3]

        chunker = MockChunker.return_value
        chunker.split_resume.return_value = [Chunk(id="r0", text="resume", source="resume")]
        chunker.split_jd.return_value = [Chunk(id="j1_0", text="job", source="job", job_id="j1")]

        vector_store = MockVectorStore.return_value
        vector_store.clear_jobs.return_value = None
        vector_store.upsert_chunks.return_value = None

        # Set cancel event during _score_jobs check
        retriever = MockRetriever.return_value

        def retrieve_side_effect(**kwargs):
            # Set cancel when retrieval is called
            raise CancelledError("用户取消")

        retriever.retrieve_for_job.side_effect = retrieve_side_effect

        repo = _make_repo()
        repo.get_scraped_details.return_value = [_make_job_detail()]
        m = Matcher(repo)
        m._current_task = MatchTask(task_id=1, total_jobs=1)
        m._cancel_event = threading.Event()
        m._auth_failed = threading.Event()

        events = []
        from src.ai.event_bus import EventBus
        m._bus = EventBus(lambda x: None)
        original_emit = m._bus.emit

        def capturing_emit(event):
            events.append(event)
            original_emit(event)

        m._bus.emit = capturing_emit
        m._bus.start()

        m._orchestrate("my resume", ["j1"], None, 1)

        assert m._current_task.status == "cancelled"
        event_types = [e.type for e in events]
        assert "cancelled" in event_types

        m._bus.stop()


# ---------------------------------------------------------------------------
# _build_job_info tests
# ---------------------------------------------------------------------------

class TestBuildJobInfo:
    def test_none_detail(self):
        m = Matcher(_make_repo())
        result = m._build_job_info(None)
        assert result["title"] == "未知"
        assert result["company"] == "未知"

    def test_empty_detail(self):
        m = Matcher(_make_repo())
        result = m._build_job_info({})
        # Empty dict is falsy, so returns defaults
        assert result["title"] == "未知"
        assert result["company"] == "未知"

    def test_full_detail(self):
        m = Matcher(_make_repo())
        detail = _make_job_detail()
        result = m._build_job_info(detail)
        assert result["title"] == "Python Dev"
        assert result["company"] == "Tech"
        assert result["salary"] == "20-40K"
        assert result["skill_tags"] == ["Python"]
        assert result["jd"] == "Write code"
