"""OpenAI-compatible API client for AI matching."""

import json
import logging
import re
import time

from openai import OpenAI, APIConnectionError, AuthenticationError, RateLimitError

from src.ai.prompts import MATCH_SYSTEM_PROMPT, build_match_user_prompt
from src.db.repository import Repository

log = logging.getLogger(__name__)


class JobScoreResult:
    """Result of a single job match with evidence."""

    __slots__ = ("job_id", "score", "evidence", "reasoning", "gaps",
                 "suggestions", "model_name", "retrieved_chunks")

    def __init__(self, job_id: str, score: float, evidence: list[dict],
                 reasoning: str, gaps: list[str], suggestions: list[str],
                 model_name: str, retrieved_chunks: list[str]):
        self.job_id = job_id
        self.score = max(0.0, min(1.0, score))
        self.evidence = evidence
        self.reasoning = reasoning
        self.gaps = gaps
        self.suggestions = suggestions
        self.model_name = model_name
        self.retrieved_chunks = retrieved_chunks

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "score": self.score,
            "evidence": self.evidence,
            "reasoning": self.reasoning,
            "gaps": self.gaps,
            "suggestions": self.suggestions,
            "model_name": self.model_name,
            "retrieved_chunks": self.retrieved_chunks,
        }


class AIClient:
    """Thin wrapper around OpenAI SDK for job matching."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def _create_client(self) -> tuple[OpenAI, str]:
        """Build OpenAI client from DB settings. Raises ValueError if api_key is missing."""
        settings = self.repo.get_identity()
        if not settings:
            raise ValueError("请先在设置页配置 API 信息")
        api_key = settings.get("api_key", "")
        if not api_key:
            raise ValueError("请先在设置页配置 API Key")
        base_url = settings.get("api_base_url", "https://api.openai.com/v1")
        model = settings.get("api_model", "gpt-4o")
        client = OpenAI(base_url=base_url, api_key=api_key)
        return client, model

    def match_with_evidence(self, job_id: str, user_prompt: str,
                            retrieved_chunks: list[str]) -> JobScoreResult:
        """Call LLM for evidence-based scoring. Returns JobScoreResult."""
        client, model = self._create_client()

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": MATCH_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
        except AuthenticationError as e:
            raise ValueError(f"API 认证失败，请检查 API Key: {e}") from e
        except RateLimitError:
            log.warning("API 速率限制，30秒后重试")
            time.sleep(30)
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": MATCH_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
        except APIConnectionError as e:
            raise ValueError(f"API 连接失败: {e}") from e

        content = response.choices[0].message.content
        parsed = self._parse_json(content)
        if parsed is None:
            raise ValueError(f"LLM 返回内容无法解析为 JSON: {content[:200]}")

        score = float(parsed.get("score", 0))
        evidence = parsed.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = []
        reasoning = str(parsed.get("reasoning", ""))
        gaps = parsed.get("gaps", [])
        if not isinstance(gaps, list):
            gaps = []
        suggestions = parsed.get("suggestions", [])
        if isinstance(suggestions, str):
            suggestions = [suggestions]

        return JobScoreResult(
            job_id=job_id,
            score=max(0.0, min(1.0, score)),
            evidence=evidence,
            reasoning=reasoning,
            gaps=gaps,
            suggestions=suggestions,
            model_name=model,
            retrieved_chunks=retrieved_chunks,
        )

    def stream_chat(self, messages: list[dict], temperature: float = 0.4) -> any:
        """Open a streaming LLM call. Returns an iterator of chat completion chunks.

        Caller iterates over chunks and reads chunk.choices[0].delta.content.
        """
        client, model = self._create_client()

        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )

    # -- Backward compat: will be removed when B5 rewrites matcher.py --

    def match_job_seeker(self, resume: str, job_detail: dict) -> JobScoreResult:
        """Legacy compat: build prompt from resume text + job detail, call LLM.

        Deprecated — B5 Matcher rewrite will use match_with_evidence() directly.
        """
        job_id = job_detail.get("job_id", "")
        user_prompt = build_match_user_prompt(job_detail, [])
        return self.match_with_evidence(job_id, user_prompt, retrieved_chunks=[])

    @staticmethod
    def _parse_json(content: str) -> dict | None:
        """Two-stage JSON parsing: direct parse, then regex from markdown code block."""
        # Stage 1: direct parse
        try:
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            pass
        # Stage 2: extract from markdown code block
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                pass
        return None
