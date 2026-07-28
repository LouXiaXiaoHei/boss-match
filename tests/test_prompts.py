"""Tests for prompt templates (B2 refactor)."""

import pytest
from src.ai.prompts import (
    MATCH_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    build_match_user_prompt,
    build_summary_user_prompt,
)


# --- Helper classes ---


class FakeChunk:
    """Simulates a RetrievalResult-like chunk with chunk_id, text, section, score."""

    def __init__(self, chunk_id, text, section, score):
        self.chunk_id = chunk_id
        self.text = text
        self.section = section
        self.score = score


class FakeJobResult:
    """Simulates a match result with job_id, score, gaps, suggestions."""

    def __init__(self, job_id, score, gaps, suggestions):
        self.job_id = job_id
        self.score = score
        self.gaps = gaps
        self.suggestions = suggestions


# --- MATCH_SYSTEM_PROMPT tests ---


class TestMatchSystemPrompt:
    def test_contains_four_iron_rules(self):
        """MATCH_SYSTEM_PROMPT must contain the 4 铁律."""
        for i in range(1, 5):
            assert f"{i}." in MATCH_SYSTEM_PROMPT, f"Missing iron rule #{i}"

    def test_contains_evidence_field(self):
        assert '"evidence"' in MATCH_SYSTEM_PROMPT

    def test_contains_gaps_field(self):
        assert '"gaps"' in MATCH_SYSTEM_PROMPT

    def test_contains_suggestions_field(self):
        assert '"suggestions"' in MATCH_SYSTEM_PROMPT

    def test_contains_score_field(self):
        assert '"score"' in MATCH_SYSTEM_PROMPT

    def test_contains_reasoning_field(self):
        assert '"reasoning"' in MATCH_SYSTEM_PROMPT

    def test_contains_source_in_evidence(self):
        assert '"source"' in MATCH_SYSTEM_PROMPT

    def test_contains_relevance_in_evidence(self):
        assert '"relevance"' in MATCH_SYSTEM_PROMPT

    def test_contains_scoring_criteria(self):
        """Scoring bands 0.8~1.0, 0.5~0.8, 0.0~0.5 must be present."""
        assert "0.8~1.0" in MATCH_SYSTEM_PROMPT
        assert "0.5~0.8" in MATCH_SYSTEM_PROMPT
        assert "0.0~0.5" in MATCH_SYSTEM_PROMPT

    def test_iron_rule_no_fabrication(self):
        assert "不得编造" in MATCH_SYSTEM_PROMPT

    def test_iron_rule_insufficient_evidence(self):
        assert "依据不足" in MATCH_SYSTEM_PROMPT


# --- SUMMARY_SYSTEM_PROMPT tests ---


class TestSummarySystemPrompt:
    def test_contains_three_iron_rules(self):
        """SUMMARY_SYSTEM_PROMPT must contain the 3 铁律."""
        for i in range(1, 4):
            assert f"{i}." in SUMMARY_SYSTEM_PROMPT, f"Missing iron rule #{i}"

    def test_contains_skill_analysis(self):
        assert '"skill_analysis"' in SUMMARY_SYSTEM_PROMPT

    def test_contains_interview_prep(self):
        assert '"interview_prep"' in SUMMARY_SYSTEM_PROMPT

    def test_contains_action_plan(self):
        assert '"action_plan"' in SUMMARY_SYSTEM_PROMPT

    def test_contains_company_analysis(self):
        assert '"company_analysis"' in SUMMARY_SYSTEM_PROMPT

    def test_contains_overall_strategy(self):
        assert '"overall_strategy"' in SUMMARY_SYSTEM_PROMPT

    def test_contains_common_requirements(self):
        assert '"common_requirements"' in SUMMARY_SYSTEM_PROMPT

    def test_contains_missing_skills(self):
        assert '"missing_skills"' in SUMMARY_SYSTEM_PROMPT

    def test_contains_likely_questions(self):
        assert '"likely_questions"' in SUMMARY_SYSTEM_PROMPT

    def test_iron_rule_no_fabrication(self):
        assert "不得编造" in SUMMARY_SYSTEM_PROMPT

    def test_iron_rule_source_reference(self):
        assert "依据来源" in SUMMARY_SYSTEM_PROMPT


# --- build_match_user_prompt tests ---


class TestBuildMatchUserPrompt:
    def setup_method(self):
        self.job_info = {
            "title": "高级Python工程师",
            "company": "某科技公司",
            "salary": "30-50K",
            "location": "北京",
            "company_scale": "500-1000人",
            "company_stage": "B轮",
            "company_industry": "互联网",
            "skill_tags": ["Python", "Django", "Redis"],
            "tags_list": ["五险一金", "弹性工作"],
            "jd": "负责后端系统开发与维护",
        }
        self.chunks = [
            FakeChunk("chunk_1", "5年Python开发经验", "工作经历", 0.92),
            FakeChunk("chunk_2", "熟悉Django和Flask框架", "技能", 0.85),
        ]

    def test_new_signature_accepts_job_info_and_chunks(self):
        """New signature: build_match_user_prompt(job_info, resume_chunks)."""
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_evidence_block_contains_chunk_text(self):
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert "5年Python开发经验" in result
        assert "熟悉Django和Flask框架" in result

    def test_evidence_block_contains_section_and_score(self):
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert "来源: 工作经历" in result
        assert "来源: 技能" in result
        assert "相关度: 0.92" in result
        assert "相关度: 0.85" in result

    def test_evidence_block_numbering(self):
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert "[简历片段#1]" in result
        assert "[简历片段#2]" in result

    def test_job_block_contains_title(self):
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert "标题: 高级Python工程师" in result

    def test_job_block_contains_company(self):
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert "公司: 某科技公司" in result

    def test_job_block_contains_new_fields(self):
        """New fields: company_scale, company_stage, company_industry."""
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert "公司规模: 500-1000人" in result
        assert "公司阶段: B轮" in result
        assert "行业: 互联网" in result

    def test_job_block_skill_tags_as_list(self):
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert "技能标签: Python, Django, Redis" in result

    def test_job_block_skill_tags_as_string(self):
        job_info = dict(self.job_info, skill_tags="Python, Django")
        result = build_match_user_prompt(job_info, self.chunks)
        assert "技能标签: Python, Django" in result

    def test_job_block_tags_list_as_list(self):
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert "其他标签: 五险一金, 弹性工作" in result

    def test_job_block_tags_list_as_string(self):
        job_info = dict(self.job_info, tags_list="五险一金")
        result = build_match_user_prompt(job_info, self.chunks)
        assert "其他标签: 五险一金" in result

    def test_job_block_contains_jd(self):
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert "负责后端系统开发与维护" in result

    def test_job_block_missing_fields_default_to_unknown(self):
        job_info = {"title": "测试职位"}
        result = build_match_user_prompt(job_info, self.chunks)
        assert "公司: 未知" in result
        assert "薪资: 未知" in result
        assert "地点: 未知" in result
        assert "公司规模: 未知" in result
        assert "公司阶段: 未知" in result
        assert "行业: 未知" in result

    def test_empty_chunks_produces_empty_evidence(self):
        result = build_match_user_prompt(self.job_info, [])
        # evidence_block will be empty string, but job_block and footer still present
        assert "【职位信息】" in result
        assert "请基于以上【简历依据】" in result

    def test_output_ends_with_instruction(self):
        result = build_match_user_prompt(self.job_info, self.chunks)
        assert "请基于以上【简历依据】评估与【职位信息】的匹配度" in result
        assert "严格按 System Prompt 的 JSON 格式输出" in result


# --- build_summary_user_prompt tests ---


class TestBuildSummaryUserPrompt:
    def setup_method(self):
        self.job_results = [
            FakeJobResult("job_1", 0.85, ["微服务经验"], ["学习Spring Cloud"]),
            FakeJobResult("job_2", 0.60, ["微服务经验", "DevOps"], ["补充CI/CD经验"]),
        ]
        self.job_infos = {
            "job_1": {
                "title": "后端工程师",
                "company": "公司A",
                "skill_tags": ["Python", "微服务"],
            },
            "job_2": {
                "title": "全栈工程师",
                "company": "公司B",
                "skill_tags": "Python, DevOps, React",
            },
        }
        self.supplement_chunks = [
            FakeChunk("supp_1", "求职者有3年管理经验", "补充", 0.7),
        ]

    def test_produces_string_output(self):
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, self.supplement_chunks
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_results_table_contains_job_titles(self):
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, self.supplement_chunks
        )
        assert "后端工程师" in result
        assert "全栈工程师" in result

    def test_results_table_contains_companies(self):
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, self.supplement_chunks
        )
        assert "公司A" in result
        assert "公司B" in result

    def test_results_table_contains_scores(self):
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, self.supplement_chunks
        )
        assert "评分: 0.85" in result
        assert "评分: 0.60" in result

    def test_results_table_contains_gaps(self):
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, self.supplement_chunks
        )
        assert "微服务经验" in result

    def test_top_gaps_section(self):
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, self.supplement_chunks
        )
        assert "【高频能力缺口】" in result
        # "微服务经验" appears in 2 results, should be in top gaps
        assert "微服务经验: 2次" in result

    def test_top_skills_section(self):
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, self.supplement_chunks
        )
        assert "【高频技能要求】" in result
        # Python appears in both job_infos
        assert "Python: 2次" in result

    def test_skill_tags_string_parsing(self):
        """job_2 has skill_tags as string "Python, DevOps, React"."""
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, self.supplement_chunks
        )
        # DevOps should be counted from the string
        assert "DevOps" in result

    def test_supplement_block(self):
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, self.supplement_chunks
        )
        assert "【补充依据】" in result
        assert "[补充材料#1]" in result
        assert "求职者有3年管理经验" in result

    def test_empty_supplement_chunks(self):
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, []
        )
        assert "（用户未提供补充材料）" in result

    def test_no_gaps_shows_none(self):
        job_results = [FakeJobResult("job_1", 0.9, [], [])]
        job_infos = {"job_1": {"title": "测试", "company": "公司", "skill_tags": []}}
        result = build_summary_user_prompt(job_results, job_infos, [])
        assert "【高频能力缺口】" in result
        assert "无" in result

    def test_no_skills_shows_none(self):
        job_results = [FakeJobResult("job_1", 0.9, [], [])]
        job_infos = {"job_1": {"title": "测试", "company": "公司", "skill_tags": []}}
        result = build_summary_user_prompt(job_results, job_infos, [])
        assert "【高频技能要求】" in result
        assert "无" in result

    def test_missing_job_info_defaults(self):
        """job_id not in job_infos should show '?' for title and company."""
        job_results = [FakeJobResult("unknown_job", 0.5, ["缺口A"], [])]
        result = build_summary_user_prompt(job_results, {}, [])
        assert "? @ ?" in result

    def test_output_ends_with_instruction(self):
        result = build_summary_user_prompt(
            self.job_results, self.job_infos, self.supplement_chunks
        )
        assert "请基于以上数据，流式输出综合分析 JSON" in result

    def test_gaps_limited_to_three_per_result(self):
        """Each result row shows at most 3 gaps."""
        job_results = [
            FakeJobResult("job_1", 0.5, ["缺口1", "缺口2", "缺口3", "缺口4"], [])
        ]
        job_infos = {"job_1": {"title": "测试", "company": "公司", "skill_tags": []}}
        result = build_summary_user_prompt(job_results, job_infos, [])
        # The results table line should only show first 3 gaps
        line = [l for l in result.split("\n") if "缺口1" in l][0]
        assert "缺口4" not in line
