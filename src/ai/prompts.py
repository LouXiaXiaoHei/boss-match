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


SEARCH_ORCHESTRATOR_SYSTEM_PROMPT = """你是一名求职搜索策略师。根据用户简历内容，推断最适合的 BOSS直聘搜索条件。

【铁律】
1. 只基于简历中实际提及的技能、经验和偏好推断
2. 筛选值必须严格使用下方枚举中的中文标签，不得使用其他值
3. 如果简历信息不足以推断某项，该项设为 null

【可用筛选值枚举】
薪资: 不限, 3K以下, 3-5K, 5-10K, 10-20K, 20-50K, 50K以上
经验: 不限, 在校生, 应届生, 经验不限, 1年以内, 1-3年, 3-5年, 5-10年, 10年以上
学历: 不限, 初中及以下, 中专/中技, 高中, 大专, 本科, 硕士, 博士
规模: 0-20人, 20-99人, 100-499人, 500-999人, 1000-9999人, 10000人以上
融资: 未融资, 天使轮, A轮, B轮, C轮, D轮及以上, 已上市, 不需要融资

【输出格式】
严格输出以下 JSON，不得包含任何额外文字：
{
  "keywords": ["关键词1", "关键词2"],
  "city": "城市名或null",
  "salary": "薪资标签或null",
  "experience": "经验标签或null",
  "degree": "学历标签或null",
  "scale": "规模标签或null",
  "stage": "融资标签或null",
  "reasoning": "推断理由，100字以内"
}
"""


def build_search_orchestrator_user_prompt(resume_text: str) -> str:
    return f"""以下是用户的简历内容：

---
{resume_text[:3000]}
---

请根据以上简历内容，推断最适合的 BOSS直聘搜索条件。严格按 System Prompt 的 JSON 格式输出。"""


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


OPTIMIZE_SYSTEM_PROMPT = """你是一名专业的简历优化顾问。基于【职位信息】和【匹配差距分析】，逐段落优化求职者的简历。

【铁律】
1. 只基于简历中实际提及的经历和技能进行改写，不得编造虚假经历
2. 可以重新组织语言、调整表述重点、突出与岗位匹配的内容
3. 不得改变事实性信息（公司名、时间、学历等硬性信息）
4. 每段改写必须说明改写原因
5. 对于与岗位高度匹配的段落，保持原文不变（action=keep）
6. 对于可以优化表述的段落，改写后更突出与岗位的相关性（action=rewrite）
7. 对于简历中缺失但岗位要求的技能，可建议补充说明（action=add），但必须基于已有经历推导

【输出格式】
严格输出以下 JSON，不得包含任何额外文字：
{
  "sections": [
    {
      "section": "段落/章节名称",
      "action": "keep" | "rewrite" | "add",
      "original": "原始段落文本（keep/rewrite 时填写）",
      "revised": "改写后文本（rewrite/add 时填写）",
      "reason": "改写原因"
    }
  ],
  "overall_tips": ["整体优化建议1", "整体优化建议2"]
}
"""


def build_optimize_user_prompt(resume_text: str, job_info: dict,
                                gaps: list, suggestions: list,
                                evidence: list, retrieved_chunks: list) -> str:
    """Build user prompt for resume optimization.

    Args:
        resume_text: Full resume text.
        job_info: Dict with title, company, salary, location, skill_tags, jd.
        gaps: List of gap strings from match result.
        suggestions: List of suggestion strings from match result.
        evidence: List of evidence dicts from match result.
        retrieved_chunks: List of retrieved chunk dicts from match result.
    """
    resume_block = f"""【简历原文】
{resume_text[:4000]}"""

    job_block = f"""【职位信息】
标题: {job_info.get('title', '未知')}
公司: {job_info.get('company', '未知')}
薪资: {job_info.get('salary', '未知')}
地点: {job_info.get('location', '未知')}
技能标签: {_normalize_tags(job_info.get('skill_tags'))}
其他标签: {_normalize_tags(job_info.get('tags_list'))}

【职位描述全文】
{job_info.get('jd', '暂无')}"""

    gaps_text = "\n".join([f"- {g}" for g in (gaps or [])]) or "无"
    suggestions_text = "\n".join([f"- {s}" for s in (suggestions or [])]) or "无"
    evidence_text = "\n".join([
        f"- {e.get('claim', str(e))} (来源: {e.get('source', '未知')})"
        for e in (evidence or [])
    ]) or "无"

    chunks_text = ""
    if retrieved_chunks:
        chunks_text = "\n\n【检索到的简历相关片段】\n" + "\n\n".join([
            f"[片段#{i+1}] {c if isinstance(c, str) else c.get('text', str(c))}"
            for i, c in enumerate(retrieved_chunks[:5])
        ])

    return f"""{resume_block}

---

{job_block}

---

【匹配差距分析】
{gaps_text}

【改进建议】
{suggestions_text}

【匹配依据】
{evidence_text}
{chunks_text}

---

请基于以上信息，逐段落优化简历。严格按 System Prompt 的 JSON 格式输出。"""


INTERVIEW_PREP_SYSTEM_PROMPT = """你是一名专业的面试准备教练。基于【职位信息】和【匹配差距分析】，为求职者生成针对性的面试准备方案。

【铁律】
1. 只基于简历中实际提及的经历和技能推断面试问题，不得编造
2. 面试题必须与目标岗位高度相关，覆盖技术深度、项目经验、行为面试等维度
3. 答题框架要给出结构化的回答思路，不是完整答案，而是回答的骨架和要点
4. 知识点补充要针对简历中缺失但岗位要求的技能，给出学习方向和关键概念
5. 每道题必须标注难度和类型

【输出格式】
严格输出以下 JSON，不得包含任何额外文字：
{
  "questions": [
    {
      "id": 1,
      "category": "技术深度" | "项目经验" | "行为面试" | "系统设计" | "基础知识" | "行业认知",
      "difficulty": "基础" | "中等" | "困难",
      "question": "面试问题",
      "context": "为什么这个问题会被问到（基于JD或差距分析）",
      "answer_framework": {
        "structure": "STAR" | "分点论述" | "对比分析" | "递进推导",
        "key_points": ["要点1", "要点2", "要点3"],
        "sample_opening": "回答的开场白示例"
      },
      "knowledge_supplements": [
        {
          "topic": "补充知识点名称",
          "key_concepts": ["核心概念1", "核心概念2"],
          "study_direction": "学习方向建议"
        }
      ]
    }
  ],
  "overall_strategy": {
    "strengths_to_highlight": ["面试中应重点突出的优势1", "优势2"],
    "weaknesses_to_prepare": ["需要提前准备的薄弱环节1", "薄弱环节2"],
    "interview_tips": ["面试策略建议1", "面试策略建议2"]
  }
}
"""


def build_interview_prep_user_prompt(resume_text: str, job_info: dict,
                                      gaps: list, suggestions: list,
                                      evidence: list, skill_tags: list) -> str:
    """Build user prompt for interview preparation.

    Args:
        resume_text: Full resume text.
        job_info: Dict with title, company, salary, location, skill_tags, jd.
        gaps: List of gap strings from match result.
        suggestions: List of suggestion strings from match result.
        evidence: List of evidence dicts from match result.
        skill_tags: List of skill tag strings from job detail.
    """
    resume_block = f"""【简历原文】
{resume_text[:4000]}"""

    job_block = f"""【职位信息】
标题: {job_info.get('title', '未知')}
公司: {job_info.get('company', '未知')}
薪资: {job_info.get('salary', '未知')}
地点: {job_info.get('location', '未知')}
技能标签: {_normalize_tags(skill_tags)}
其他标签: {_normalize_tags(job_info.get('tags_list'))}

【职位描述全文】
{job_info.get('jd', '暂无')}"""

    gaps_text = "\n".join([f"- {g}" for g in (gaps or [])]) or "无"
    suggestions_text = "\n".join([f"- {s}" for s in (suggestions or [])]) or "无"
    evidence_text = "\n".join([
        f"- {e.get('claim', str(e))} (来源: {e.get('source', '未知')})"
        for e in (evidence or [])
    ]) or "无"

    return f"""{resume_block}

---

{job_block}

---

【匹配差距分析】
{gaps_text}

【改进建议】
{suggestions_text}

【匹配依据】
{evidence_text}

---

请基于以上信息，生成针对性的面试准备方案。严格按 System Prompt 的 JSON 格式输出。"""


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
