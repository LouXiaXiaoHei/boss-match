"""Prompt templates for AI matching."""

from collections import Counter

MATCH_SYSTEM_PROMPT = """你是一名严谨的职业匹配分析师。你的任务是基于【提供的依据】评估求职者与职位的匹配度。

【铁律】
1. 你只能基于【检索到的简历依据】和【职位信息】进行评判
2. 不得使用依据之外的知识，不得编造简历中未提及的经验或技能
3. 如果依据不足以判断某项，在 reasoning 中明确说明"依据不足"，该项不得加分
4. 评分必须基于依据中实际存在的内容，而非推测

【输出格式】
严格输出以下 JSON，不得包含任何额外文字：
{
  "score": 0.0~1.0,
  "evidence": [
    {"claim": "依据描述", "source": "resume_chunk_3", "relevance": "为何相关"}
  ],
  "reasoning": "基于证据的分析，200字以内",
  "gaps": ["能力缺口1", "能力缺口2"],
  "suggestions": ["改进建议1", "改进建议2"]
}

【评分标准】
- 0.8~1.0: 依据显示求职者在核心技能、经验、行业上高度匹配
- 0.5~0.8: 部分核心技能有依据支持，但有明显缺口
- 0.0~0.5: 依据显示核心技能不匹配或严重缺口
"""


def _normalize_tags(value):
    """Render skill_tags/tags_list to a comma-joined string. Handles list, str, None."""
    if isinstance(value, list):
        return ', '.join(value)
    if value is None:
        return ''
    return str(value)


def build_match_user_prompt(job_info: dict, resume_chunks: list) -> str:
    """Build user prompt from job info dict and retrieved resume chunks.

    Args:
        job_info: Dict with title, company, salary, location, company_scale,
                  company_stage, company_industry, skill_tags, tags_list, jd.
        resume_chunks: List of objects with chunk_id, text, section, score attributes.
    """
    evidence_block = "\n\n".join([
        f"[简历片段#{i+1}] (来源: {c.section}, 相关度: {c.score:.2f})\n{c.text}"
        for i, c in enumerate(resume_chunks)
    ])

    job_block = f"""【职位信息】
标题: {job_info.get('title', '未知')}
公司: {job_info.get('company', '未知')}
薪资: {job_info.get('salary', '未知')}
地点: {job_info.get('location', '未知')}
公司规模: {job_info.get('company_scale', '未知')}
公司阶段: {job_info.get('company_stage', '未知')}
行业: {job_info.get('company_industry', '未知')}
技能标签: {_normalize_tags(job_info.get('skill_tags'))}
其他标签: {_normalize_tags(job_info.get('tags_list'))}

【职位描述全文】
{job_info.get('jd', '暂无')}
"""

    return f"""{evidence_block}

---

{job_block}

---

请基于以上【简历依据】评估与【职位信息】的匹配度，严格按 System Prompt 的 JSON 格式输出。"""


SUMMARY_SYSTEM_PROMPT = """你是一名职业规划顾问。基于【匹配结果数据】和【检索到的补充依据】，为求职者提供整体职业规划建议。

【铁律】
1. 只基于【匹配结果数据】和【检索到的补充依据】提供建议
2. 不得编造求职者未提及的经历或市场数据
3. 所有建议必须指向具体的依据来源

【输出格式】
流式输出，最终汇聚为以下 JSON：
{
  "skill_analysis": {
    "common_requirements": ["高频技能1", "高频技能2"],
    "matching_skills": ["已具备的技能"],
    "missing_skills": ["普遍缺失的技能"]
  },
  "company_analysis": {
    "tier_distribution": {"高匹配": 0, "中匹配": 0, "低匹配": 0},
    "industry_insights": ["行业洞察1"]
  },
  "interview_prep": {
    "likely_questions": ["可能问题1", "可能问题2"],
    "focus_areas": ["重点准备方向1"]
  },
  "action_plan": [
    {"priority": "高", "action": "具体行动", "timeline": "1周内"}
  ],
  "overall_strategy": "整体策略建议，300字以内"
}
"""


def build_summary_user_prompt(job_results, job_infos, supplement_chunks):
    """Build user prompt for summary analysis.

    Args:
        job_results: List of dict-like objects with job_id, score, gaps, suggestions.
        job_infos: Dict mapping job_id to job info dict (with title, company, skill_tags).
        supplement_chunks: List of objects with text attribute.
    """
    results_table = "\n".join([
        f"- {job_infos.get(r.job_id, {}).get('title', '?')} @ {job_infos.get(r.job_id, {}).get('company', '?')}"
        f" | 评分: {r.score:.2f} | 缺口: {', '.join(r.gaps[:3]) if r.gaps else '无'}"
        for r in job_results
    ])

    all_gaps = Counter(gap for r in job_results for gap in (r.gaps or []))
    all_skills = Counter()
    for r in job_results:
        info = job_infos.get(r.job_id, {})
        tags = info.get('skill_tags') or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',') if t.strip()]
        for skill in tags:
            all_skills[skill] += 1

    supplement_block = "\n\n".join([
        f"[补充材料#{i+1}] {c.text}"
        for i, c in enumerate(supplement_chunks)
    ]) or "（用户未提供补充材料）"

    top_gaps = "\n".join([f"  {gap}: {cnt}次" for gap, cnt in all_gaps.most_common(5)])
    top_skills = "\n".join([f"  {skill}: {cnt}次" for skill, cnt in all_skills.most_common(10)])

    return f"""【匹配结果数据】
{results_table}

【高频能力缺口】
{top_gaps if top_gaps else '  无'}

【高频技能要求】
{top_skills if top_skills else '  无'}

【补充依据】
{supplement_block}

请基于以上数据，流式输出综合分析 JSON。"""
