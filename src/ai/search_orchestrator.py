"""AI-powered search condition inference from resume content."""

import logging

from src.ai.client import AIClient
from src.ai.prompts import (
    SEARCH_ORCHESTRATOR_SYSTEM_PROMPT,
    build_search_orchestrator_user_prompt,
)
from src.core.constants import (
    SALARY_MAP, EXPERIENCE_MAP, DEGREE_MAP, SCALE_MAP, STAGE_MAP,
)
from src.db.repository import Repository

log = logging.getLogger(__name__)

# Reverse maps: Chinese label -> API code
_SALARY_REV = {v: k for k, v in SALARY_MAP.items()}
_EXP_REV = {v: k for k, v in EXPERIENCE_MAP.items()}
_DEGREE_REV = {v: k for k, v in DEGREE_MAP.items()}
_SCALE_REV = {v: k for k, v in SCALE_MAP.items()}
_STAGE_REV = {v: k for k, v in STAGE_MAP.items()}


class SearchOrchestrator:
    """Infer BOSS zhipin search conditions from resume text using LLM."""

    def __init__(self, repo: Repository):
        self._repo = repo
        self._ai = AIClient(repo)

    def infer_search_conditions(self, resume_id: int | None = None,
                                resume_text: str = "") -> dict:
        """Return inferred search conditions dict with API codes.

        Returns:
            {
                "keywords": str,
                "city": str,
                "salary": str (code),
                "experience": str (code),
                "degree": str (code),
                "scale": str (code),
                "stage": str (code),
                "reasoning": str,
            }
        """
        if not resume_text:
            if resume_id:
                row = self._repo.get_resume_by_id(resume_id)
                if row:
                    resume_text = row.get("content", "")
            if not resume_text:
                active = self._repo.get_active_resume()
                if active:
                    resume_text = active.get("content", "")
        if not resume_text:
            return {"ok": False, "error": "没有可用的简历内容"}

        try:
            client, model = self._ai._create_client()
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        user_prompt = build_search_orchestrator_user_prompt(resume_text)

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SEARCH_ORCHESTRATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
            )
        except Exception as e:
            log.error(f"Search orchestrator LLM call failed: {e}")
            return {"ok": False, "error": f"AI 推断失败: {e}"}

        content = response.choices[0].message.content
        parsed = AIClient._parse_json(content)
        if not parsed:
            return {"ok": False, "error": "AI 返回内容无法解析"}

        result = self._validate_and_map(parsed)
        result["ok"] = True
        return result

    def _validate_and_map(self, raw: dict) -> dict:
        """Map LLM Chinese labels to API codes, dropping invalid values."""
        keywords = raw.get("keywords", [])
        if isinstance(keywords, list):
            keywords = " ".join(keywords)
        keywords = str(keywords).strip() if keywords else ""

        city = raw.get("city")
        city = str(city).strip() if city and city != "null" else ""

        salary_code = self._map_field(raw.get("salary"), SALARY_MAP)
        exp_code = self._map_field(raw.get("experience"), EXPERIENCE_MAP)
        degree_code = self._map_field(raw.get("degree"), DEGREE_MAP)
        scale_code = self._map_field(raw.get("scale"), SCALE_MAP)
        stage_code = self._map_field(raw.get("stage"), STAGE_MAP)

        return {
            "keywords": keywords,
            "city": city,
            "salary": salary_code,
            "experience": exp_code,
            "degree": degree_code,
            "scale": scale_code,
            "stage": stage_code,
            "reasoning": str(raw.get("reasoning", "")),
        }

    @staticmethod
    def _map_field(value, label_map: dict) -> str:
        """Map a Chinese label to API code. Returns empty string if invalid."""
        if not value or value == "null":
            return ""
        value = str(value).strip()
        # Direct match
        if value in label_map:
            return label_map[value]
        # Already a code
        if value in label_map.values():
            return value
        log.debug(f"SearchOrchestrator: unmapped filter value '{value}'")
        return ""
