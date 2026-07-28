"""Tests for src/ai/client.py — JobScoreResult, match_with_evidence, stream_chat, _parse_json."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.ai.client import AIClient, JobScoreResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_repo():
    """Repository mock that returns valid identity settings."""
    repo = MagicMock()
    repo.get_identity.return_value = {
        "api_key": "test-key",
        "api_base_url": "https://api.example.com/v1",
        "api_model": "test-model",
    }
    return repo


@pytest.fixture
def client(mock_repo):
    return AIClient(mock_repo)


def _make_response(content: str) -> MagicMock:
    """Build a mock OpenAI response with the given content string."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# JobScoreResult
# ---------------------------------------------------------------------------

class TestJobScoreResult:
    def test_to_dict(self):
        r = JobScoreResult(
            job_id="123",
            score=0.85,
            evidence=[{"claim": "Python 5年", "source": "chunk_1", "relevance": "核心技能"}],
            reasoning="匹配度高",
            gaps=["缺少Go经验"],
            suggestions=["学习Go"],
            model_name="gpt-4o",
            retrieved_chunks=["chunk_1", "chunk_2"],
        )
        d = r.to_dict()
        assert d["job_id"] == "123"
        assert d["score"] == 0.85
        assert len(d["evidence"]) == 1
        assert d["reasoning"] == "匹配度高"
        assert d["gaps"] == ["缺少Go经验"]
        assert d["suggestions"] == ["学习Go"]
        assert d["model_name"] == "gpt-4o"
        assert d["retrieved_chunks"] == ["chunk_1", "chunk_2"]

    def test_score_clamped_high(self):
        r = JobScoreResult("j1", 1.5, [], "", [], [], "m", [])
        assert r.score == 1.0

    def test_score_clamped_low(self):
        r = JobScoreResult("j1", -0.3, [], "", [], [], "m", [])
        assert r.score == 0.0

    def test_slots(self):
        r = JobScoreResult("j1", 0.5, [], "", [], [], "m", [])
        with pytest.raises(AttributeError):
            r.nonexistent = "fail"


# ---------------------------------------------------------------------------
# match_with_evidence
# ---------------------------------------------------------------------------

class TestMatchWithEvidence:
    @patch("src.ai.client.OpenAI")
    def test_returns_job_score_result(self, MockOpenAI, client):
        llm_json = json.dumps({
            "score": 0.75,
            "evidence": [{"claim": "5年Python", "source": "chunk_0", "relevance": "核心技能匹配"}],
            "reasoning": "核心技能匹配",
            "gaps": ["缺少管理经验"],
            "suggestions": ["积累团队管理经验"],
        })
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_response(llm_json)
        MockOpenAI.return_value = mock_client

        result = client.match_with_evidence(
            job_id="job-42",
            user_prompt="test prompt",
            retrieved_chunks=["chunk_0"],
        )

        assert isinstance(result, JobScoreResult)
        assert result.job_id == "job-42"
        assert result.score == 0.75
        assert len(result.evidence) == 1
        assert result.reasoning == "核心技能匹配"
        assert result.gaps == ["缺少管理经验"]
        assert result.suggestions == ["积累团队管理经验"]
        assert result.model_name == "test-model"
        assert result.retrieved_chunks == ["chunk_0"]

    @patch("src.ai.client.OpenAI")
    def test_evidence_not_list_fallback(self, MockOpenAI, client):
        llm_json = json.dumps({
            "score": 0.5,
            "evidence": "not a list",
            "reasoning": "ok",
            "gaps": "also not a list",
            "suggestions": "single string",
        })
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_response(llm_json)
        MockOpenAI.return_value = mock_client

        result = client.match_with_evidence("j1", "prompt", [])

        assert result.evidence == []
        assert result.gaps == []
        assert result.suggestions == ["single string"]

    @patch("src.ai.client.OpenAI")
    def test_authentication_error_raises_valueerror(self, MockOpenAI, client):
        from openai import AuthenticationError

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = AuthenticationError(
            message="bad key", response=MagicMock(), body=None
        )
        MockOpenAI.return_value = mock_client

        with pytest.raises(ValueError, match="API 认证失败"):
            client.match_with_evidence("j1", "prompt", [])

    @patch("src.ai.client.OpenAI")
    @patch("src.ai.client.time.sleep")
    def test_rate_limit_retry(self, mock_sleep, MockOpenAI, client):
        from openai import RateLimitError

        llm_json = json.dumps({"score": 0.6, "evidence": [], "reasoning": "ok", "gaps": [], "suggestions": []})
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            RateLimitError(message="rate limited", response=MagicMock(), body=None),
            _make_response(llm_json),
        ]
        MockOpenAI.return_value = mock_client

        result = client.match_with_evidence("j1", "prompt", [])
        assert result.score == 0.6
        mock_sleep.assert_called_once_with(30)

    @patch("src.ai.client.OpenAI")
    def test_connection_error_raises_valueerror(self, MockOpenAI, client):
        from openai import APIConnectionError

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = APIConnectionError(
            message="no network", request=MagicMock()
        )
        MockOpenAI.return_value = mock_client

        with pytest.raises(ValueError, match="API 连接失败"):
            client.match_with_evidence("j1", "prompt", [])

    @patch("src.ai.client.OpenAI")
    def test_unparseable_json_raises_valueerror(self, MockOpenAI, client):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_response("not json at all!!!")
        MockOpenAI.return_value = mock_client

        with pytest.raises(ValueError, match="无法解析为 JSON"):
            client.match_with_evidence("j1", "prompt", [])

    @patch("src.ai.client.OpenAI")
    def test_score_clamped_in_result(self, MockOpenAI, client):
        llm_json = json.dumps({"score": 2.0, "evidence": [], "reasoning": "ok", "gaps": [], "suggestions": []})
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_response(llm_json)
        MockOpenAI.return_value = mock_client

        result = client.match_with_evidence("j1", "prompt", [])
        assert result.score == 1.0


# ---------------------------------------------------------------------------
# stream_chat
# ---------------------------------------------------------------------------

class TestStreamChat:
    @patch("src.ai.client.OpenAI")
    def test_returns_stream_iterator(self, MockOpenAI, client):
        mock_stream = iter(["chunk1", "chunk2"])
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_stream
        MockOpenAI.return_value = mock_client

        messages = [{"role": "user", "content": "hello"}]
        result = client.stream_chat(messages)

        # Should be an iterator
        assert iter(result) is not None
        chunks = list(result)
        assert chunks == ["chunk1", "chunk2"]

        # Verify call args
        mock_client.chat.completions.create.assert_called_once_with(
            model="test-model",
            messages=messages,
            temperature=0.4,
            stream=True,
        )

    @patch("src.ai.client.OpenAI")
    def test_custom_temperature(self, MockOpenAI, client):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([])
        MockOpenAI.return_value = mock_client

        client.stream_chat([{"role": "user", "content": "hi"}], temperature=0.7)

        mock_client.chat.completions.create.assert_called_once_with(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            stream=True,
        )


# ---------------------------------------------------------------------------
# _parse_json (two-stage)
# ---------------------------------------------------------------------------

class TestParseJson:
    def test_direct_json(self):
        data = {"score": 0.8, "reasoning": "good"}
        assert AIClient._parse_json(json.dumps(data)) == data

    def test_markdown_code_block(self):
        inner = json.dumps({"score": 0.5})
        content = f"```json\n{inner}\n```"
        assert AIClient._parse_json(content) == {"score": 0.5}

    def test_markdown_code_block_no_lang(self):
        inner = json.dumps({"score": 0.3})
        content = f"```\n{inner}\n```"
        assert AIClient._parse_json(content) == {"score": 0.3}

    def test_invalid_returns_none(self):
        assert AIClient._parse_json("not json") is None

    def test_invalid_in_code_block_returns_none(self):
        assert AIClient._parse_json("```json\nnot json\n```") is None


# ---------------------------------------------------------------------------
# _create_client
# ---------------------------------------------------------------------------

class TestCreateClient:
    def test_missing_identity_raises(self):
        repo = MagicMock()
        repo.get_identity.return_value = None
        client = AIClient(repo)
        with pytest.raises(ValueError, match="请先在设置页配置 API 信息"):
            client._create_client()

    def test_missing_api_key_raises(self):
        repo = MagicMock()
        repo.get_identity.return_value = {"api_key": "", "api_base_url": "https://api.example.com/v1"}
        client = AIClient(repo)
        with pytest.raises(ValueError, match="请先在设置页配置 API Key"):
            client._create_client()
