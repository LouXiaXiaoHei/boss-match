# Phase 3 RAG 架构重构设计

**日期**: 2026-07-28
**范围**: Phase 3 — AI 匹配模块从纯 LLM 判断重构为 RAG 架构
**目标**: 让 LLM 从"凭记忆打分"变成"有依据地评判"，并支持跨职位综合分析

---

## 背景与问题

### 当前架构

当前 AI 匹配流程（`src/ai/matcher.py` + `src/ai/client.py`）：

1. 用户选择职位后，从 `geek_resume` 读取简历全文
2. `ThreadPoolExecutor` 并发 3 线程，每个线程对单个职位调 LLM
3. Prompt = system prompt（要求输出 score/reasoning/suggestions JSON）+ user prompt（简历全文 + 职位 JD）
4. `temperature=0.3`，`response_format={"type": "json_object"}`
5. 两阶段 JSON 解析，结果存入 `match_result` 表

### 核心问题

1. **凭记忆打分**：LLM 只能依赖上下文窗口内的信息，职位多、JD 长时容易遗忘关键技能点
2. **无跨职位分析**：每个职位独立评分，无法回答"这些公司共同要求什么"、"普遍缺口是什么"、"面试怎么准备"
3. **Prompt 信息缺失**：user prompt 未包含 `company_scale/stage/industry/welfare` 等字段
4. **无依据可追溯**：LLM 给出的 reasoning 无法定位到简历具体片段，无法验证是否编造
5. **阻塞式输出**：所有职位评分完成后才返回，用户等待时间长，无进度反馈

### 用户需求

- 使用 RAG 架构，让 LLM 从"凭记忆打分"变成"有依据地评判"
- 知识库来源：用户选择的职位 + 用户可选上传的补充材料
- Prompt 严格限制 LLM 只基于检索依据，不允许自由发挥
- 事件驱动 + 生产消费者架构，展示 loading 但不阻塞用户
- 渐进式输出，不需要等所有内容返回才输出
- 匹配结果使用 JSON 格式，方便结构化 UI 展示
- 最后给用户整体建议：面试问题、技能要求、面试准备

---

## 总体架构

### 核心组件

```
┌─────────────────────────────────────────────────────────┐
│  前端 (app.js)                                           │
│  - 订阅事件流 (evaluate_js)                              │
│  - 渐进式渲染结果卡片                                     │
│  - Loading 状态随事件更新                                 │
└──────────────┬──────────────────────────────────────────┘
               │ window.__onMatchProgress(payload)
               ▼
┌─────────────────────────────────────────────────────────┐
│  事件总线 (EventBus)                                     │
│  - 单线程消费，按顺序派发                                 │
│  - 事件类型:                                              │
│    · phase_start / phase_progress / phase_done           │
│    · model_download_progress / model_ready               │
│    · job_scored / job_failed                             │
│    · summary_chunk / summary_done                        │
│    · error / cancelled / match_completed                 │
└──────────────┬──────────────────────────────────────────┘
               │
       ┌───────┴────────┐
       ▼                ▼
┌──────────────┐  ┌──────────────┐
│ 生产者       │  │ 消费者       │
│ - Embedder   │  │ - LLM Scorer │
│ - Retriever  │  │ - Summarizer │
└──────────────┘  └──────────────┘
```

### 三阶段生产-消费流程

1. **知识库构建阶段**（生产者：Embedder → 消费者：ChromaDB Writer）
   - 简历 + 补充材料 → chunks → embeddings → 写入向量库
   - N 个职位 JD → chunks → embeddings → 写入向量库
   - 每完成一批 chunk 推送 `phase_progress` 事件

2. **逐岗位匹配阶段**（生产者：Retriever → 消费者：LLM Scorer）
   - 对每个职位：检索简历相关 chunk → 组装 prompt → 调 LLM 评分
   - 每完成一个职位推送 `job_scored` 事件（带 score/evidence/gaps/reasoning）
   - 前端立即渲染该职位的结果卡片，不等其他职位

3. **跨职位综合阶段**（生产者：结果聚合器 → 消费者：LLM Summarizer）
   - 收集所有逐岗位结果 → 调 LLM 生成综合分析
   - 使用流式输出（stream=true），每收到一段推送 `summary_chunk` 事件
   - 前端逐步拼接显示，类似打字机效果

### 线程模型

```
主线程 (UI/PyWebView)
    ↑
    │ evaluate_js (事件推送)
    │
事件派发线程 (单线程, 串行)
    ↑
    │ Queue.put (事件入队)
    │
工作线程池 (ThreadPoolExecutor)
    - Embedding 线程 (CPU 密集, 1-2 线程)
    - LLM 调用线程 (IO 密集, 3 并发)
```

**关键设计**：
- 所有事件通过 `queue.Queue` 串行入队，单线程派发到前端，避免 evaluate_js 并发问题
- Embedding 和 LLM 调用分离到不同线程池，互不阻塞
- `cancel_event` 在各阶段检查，支持随时取消

### 取消机制

每个阶段都检查 `cancel_event.is_set()`：
- 模型下载中取消：终止下载线程
- 知识库构建中取消：停止后续 chunk embedding
- 逐岗位评分中取消：`executor.shutdown(cancel_futures=True)`
- 综合分析中取消：关闭 LLM stream 连接

---

## 模块 1：知识库构建

### 数据流

```
用户简历 (geek_resume)          用户选择的职位 JD (scraped_detail)
       │                                    │
       ▼                                    ▼
  Chunker.split()                    Chunker.split()
       │                                    │
       ▼                                    ▼
  [chunk_1, chunk_2, ...]           [chunk_1, chunk_2, ...]
       │                                    │
       ▼                                    ▼
  Embedder.embed() × N              Embedder.embed() × N
       │                                    │
       ▼                                    ▼
  ChromaDB.upsert()                 ChromaDB.upsert()
  collection: "resume"              collection: "jobs"
```

### Chunker — 语义切分

按结构边界切分而非固定 token，每 chunk 200~400 tokens。

**Chunk 结构**：
```python
@dataclass
class Chunk:
    id: str           # 唯一标识 (如 "resume_0", "job_abc_2")
    text: str         # chunk 文本
    source: str       # "resume" | "job" | "supplement"
    job_id: str       # 仅 job chunk 有
    section: str      # 语义段落标签
    metadata: dict    # 额外元数据
```

**切分策略**：
- **简历**：按 `##`/`###` markdown 标题或空行分段，每段 200~400 tokens，超长段再按句号二次切分
- **JD**：技能标签提取为独立 chunk（`section: "skills"`），JD 正文按段落切分
- **补充材料**：与简历同样策略切分，`source: "supplement"`

```python
class Chunker:
    def split_resume(self, text: str, source: str = "resume") -> list[Chunk]: ...
    def split_jd(self, job_id: str, title: str, jd_text: str, skill_tags: list) -> list[Chunk]: ...
```

### Embedder — 本地 Embedding 推理

```python
class Embedder:
    DEFAULT_MODEL = "BAAI/bge-small-zh-v1.1"
    # 中文小模型，33M 参数，~100MB，普通家用 CPU 即可运行
    
    def __init__(self, model_name=None, cache_dir=None):
        self.model_name = model_name or self.DEFAULT_MODEL
        self.cache_dir = cache_dir or "~/.boss-match/models"
        self._model = None  # 延迟加载
    
    def ensure_model(self, progress_callback=None):
        """确保模型已下载，首次调用触发下载"""
        # SentenceTransformer 构造时自动下载到 cache_dir
        # progress_callback 推送下载进度事件
    
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量 embedding，返回向量列表"""
        return self._model.encode(texts, normalize_embeddings=True)
    
    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
```

**模型选择 `BAAI/bge-small-zh-v1.1`**：
- 中文专用，33M 参数，模型文件 ~100MB
- MTEB 中文榜单排名靠前
- CPU 推理速度：~50 chunks/s（普通家用电脑）
- 首次下载约 30 秒，后续启动 <2 秒

### VectorStore — ChromaDB 封装

```python
class VectorStore:
    def __init__(self, persist_dir="~/.boss-match/chromadb"):
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._resume_col = self._client.get_or_create_collection(
            "resume", metadata={"hnsw:space": "cosine"})
        self._supplement_col = self._client.get_or_create_collection(
            "supplement", metadata={"hnsw:space": "cosine"})
        self._jobs_col = self._client.get_or_create_collection(
            "jobs", metadata={"hnsw:space": "cosine"})
    
    def upsert_resume_chunks(self, chunks, embeddings): ...
    def upsert_supplement_chunks(self, chunks, embeddings): ...
    def upsert_job_chunks(self, chunks, embeddings): ...
    def query(self, query_embedding, collection, top_k=5) -> list[RetrievalResult]: ...
    def get_job_chunks(self, job_id) -> list[Chunk]: ...
    def clear_jobs(self): ...       # 每次新匹配前清空旧职位索引
    def clear_all(self): ...
```

**数据新鲜度策略**：每次匹配前 `clear_jobs()`，避免旧职位 chunks 污染检索。简历和补充材料 chunks 持久保留（用户主动更新时覆盖）。

### Retriever — RAG 检索逻辑

```python
class Retriever:
    def retrieve_for_job(self, query_embedding: list[float], top_k: int = 5) -> list[RetrievalResult]:
        """检索与该职位最相关的简历 + 补充材料片段"""
        # 同时查询 resume 和 supplement collection，合并结果取 top_k
    
    def retrieve_supplements(self, top_k: int = 3) -> list[RetrievalResult]:
        """为综合分析阶段检索关键补充材料"""
```

### 用户补充材料处理

用户可在匹配页上传补充知识（面试经验、目标公司资料等）：
1. 前端：文件选择 → FileReader → base64 → `api.uploadSupplement(filename, base64Content)`
2. 后端：解析文本（复用 `resume_parser.py`）→ 返回提取文本给前端
3. 用户可在 Vditor 编辑器中查看/编辑后保存
4. 启动匹配时，补充材料文本作为参数传入，切分 chunk → embedding → 写入 ChromaDB `supplement` collection
5. 匹配时检索范围：`resume` + `supplement` 两个 collection

---

## 模块 2：两阶段匹配与 Prompt 设计

### 阶段一：逐岗位评分

#### 流程

```
对每个职位 job (并发 3):
    1. 取该职位所有 chunks (title + skills + jd段落)
    2. 用 query embedding 检索简历 top_k=5 相关片段
    3. 组装 Prompt:
       - System: 严格限定「只基于提供的依据打分，不得编造」
       - User: 
         · 【检索到的简历依据】(5个片段，标注来源)
         · 【职位信息】(title/company/salary/skills/jd全文 + company_scale/stage/industry)
         · 【评分要求】(输出固定JSON)
    4. 调 LLM (temperature=0.2, json_object mode)
    5. 解析 JSON → JobScoreResult
    6. 推送 job_scored 事件
```

#### System Prompt（严格限制版）

```python
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
```

**关键设计**：
- `evidence` 字段强制 LLM 列出引用的依据 chunk_id，可追溯
- `gaps` 字段显式列出能力缺口，供综合分析阶段使用
- `source` 字段对应检索返回的 chunk_id，前端可高亮"引用了简历哪一段"

#### User Prompt 构建

```python
def build_match_user_prompt(job_info: dict, resume_chunks: list[RetrievalResult]) -> str:
    """job_info: 包含 title/company/salary/location/company_scale/stage/industry/skill_tags/tags_list/jd
       resume_chunks: 检索到的简历相关片段 [{chunk_id, text, section, score}]
    """
    # 1. 检索到的简历依据（带标注）
    evidence_block = "\n\n".join([
        f"[简历片段#{i+1}] (来源: {c.section}, 相关度: {c.score:.2f})\n{c.text}"
        for i, c in enumerate(resume_chunks)
    ])
    
    # 2. 职位完整信息（包含之前缺失的字段）
    job_block = f"""【职位信息】
标题: {job_info['title']}
公司: {job_info.get('company', '未知')}
薪资: {job_info.get('salary', '未知')}
地点: {job_info.get('location', '未知')}
公司规模: {job_info.get('company_scale', '未知')}
公司阶段: {job_info.get('company_stage', '未知')}
行业: {job_info.get('company_industry', '未知')}
技能标签: {', '.join(job_info.get('skill_tags', []))}
其他标签: {', '.join(job_info.get('tags_list', []))}

【职位描述全文】
{job_info.get('jd', '暂无')}
"""
    
    return f"""{evidence_block}

---

{job_block}

---

请基于以上【简历依据】评估与【职位信息】的匹配度，严格按 System Prompt 的 JSON 格式输出。"""
```

#### JobScoreResult 数据结构

```python
@dataclass
class JobScoreResult:
    job_id: str
    score: float                # 0.0~1.0
    evidence: list[dict]        # [{claim, source, relevance}]
    reasoning: str              # 基于证据的分析
    gaps: list[str]             # 能力缺口
    suggestions: list[str]      # 改进建议
    model_name: str
    retrieved_chunks: list[str] # 引用的 chunk_id 列表，用于前端高亮
```

---

### 阶段二：跨职位综合分析

#### 流程

```
1. 收集所有 JobScoreResult (按 score DESC 排序)
2. 提取关键信号:
   - 高匹配职位共现的技能 (从 retrieved_chunks 聚合)
   - 普遍的能力缺口 (从 gaps 字段聚合)
   - 薪资分布与简历期望的对比
3. 检索补充材料 (用户上传的面试经验等)
4. 流式调 LLM (stream=True) 生成综合分析
5. 每收到一段 chunk 推送 summary_chunk 事件
6. 完成后推送 summary_done (带完整 JSON)
```

#### 综合分析 System Prompt

```python
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
    "tier_distribution": {"高匹配": N, "中匹配": N, "低匹配": N},
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
```

#### 综合分析 User Prompt

```python
def build_summary_user_prompt(
    job_results: list[JobScoreResult],
    job_infos: dict[str, dict],
    supplement_chunks: list[RetrievalResult]
) -> str:
    # 1. 匹配结果摘要表
    results_table = "\n".join([
        f"- {job_infos[r.job_id]['title']} @ {job_infos[r.job_id].get('company','?')}"
        f" | 评分: {r.score:.2f} | 缺口: {', '.join(r.gaps[:3])}"
        for r in job_results
    ])
    
    # 2. 聚合信号
    all_gaps = Counter(gap for r in job_results for gap in r.gaps)
    all_skills = Counter(skill for r in job_results 
                         for skill in job_infos[r.job_id].get('skill_tags', []))
    
    # 3. 补充依据
    supplement_block = "\n\n".join([
        f"[补充材料#{i+1}] {c.text}"
        for i, c in enumerate(supplement_chunks)
    ]) or "（用户未提供补充材料）"
    
    return f"""【匹配结果数据】
{results_table}

【高频能力缺口】
{format_counter(all_gaps, top_n=5)}

【高频技能要求】
{format_counter(all_skills, top_n=10)}

【补充依据】
{supplement_block}

请基于以上数据，流式输出综合分析 JSON。"""
```

#### 流式输出处理

```python
class SummaryStreamer:
    """流式生成综合分析，渐进式推送 chunk 事件"""
    
    def stream(self, user_prompt: str) -> SummaryResult:
        buffer = ""
        
        stream = self.client.stream_chat(
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.4,
            stream=True
        )
        
        for chunk in stream:
            if self.cancel.is_set():
                stream.close()
                raise CancelledError("用户取消综合分析")
            
            delta = chunk.choices[0].delta.content or ""
            buffer += delta
            
            if delta:
                self.bus.emit(MatchEvent("summary_chunk", text=delta))
        
        structured = self._try_parse_json(buffer)
        
        self.bus.emit(MatchEvent("summary_done",
                                  structured=structured, raw=buffer))
        
        return SummaryResult(structured=structured, raw=buffer)
    
    def _try_parse_json(self, text: str):
        """两阶段解析：直接解析 → 正则提取 code block"""
        try:
            return json.loads(text)
        except:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except:
                    pass
        return None
```

---

## 模块 3：事件驱动架构实现

### EventBus — 事件总线

```python
# src/ai/event_bus.py
import queue
import threading
import json
import logging

log = logging.getLogger(__name__)


class MatchEvent:
    """匹配事件，序列化为 JSON 推送到前端"""
    
    __slots__ = ("type", "phase", "data")
    
    def __init__(self, type: str, phase: str = "", **data):
        self.type = type
        self.phase = phase
        self.data = data
    
    def to_json(self) -> str:
        payload = {"type": self.type, "phase": self.phase}
        payload.update(self.data)
        return json.dumps(payload, ensure_ascii=False)


class EventBus:
    """事件总线：单线程串行派发，避免 evaluate_js 并发问题"""
    
    def __init__(self, notify_callback):
        self._queue = queue.Queue()
        self._notify = notify_callback
        self._consumer_thread = None
        self._stop = threading.Event()
    
    def start(self):
        self._stop.clear()
        self._consumer_thread = threading.Thread(
            target=self._consume, daemon=True, name="event-bus"
        )
        self._consumer_thread.start()
    
    def stop(self):
        self._stop.set()
        self._queue.put(None)
    
    def emit(self, event: MatchEvent):
        """生产者调用：事件入队，立即返回"""
        self._queue.put(event)
    
    def _consume(self):
        """消费者：单线程串行派发到前端"""
        while not self._stop.is_set():
            event = self._queue.get()
            if event is None:
                break
            try:
                self._notify(event.to_json())
            except Exception as e:
                log.debug(f"事件派发失败: {e}")
```

### Matcher — 三阶段编排器

```python
# src/ai/matcher.py
class Matcher:
    """RAG 匹配编排器：协调三阶段生产-消费"""
    
    def __init__(self, repo, notify_callback):
        self.repo = repo
        self._notify = notify_callback
        self._task = None
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._auth_failed = threading.Event()
    
    def start_match(self, resume: str, job_ids: list[str], 
                    supplements: list[str] = None, concurrency: int = 3):
        with self._lock:
            if self._task and self._task.status == "running":
                return {"ok": False, "error": "已有匹配任务在运行"}
            
            self._cancel_event.clear()
            self._auth_failed.clear()
            
            self._task = MatchTask(total_jobs=len(job_ids))
            self._task.status = "running"
            
            self._bus = EventBus(self._notify)
            self._bus.start()
            
            threading.Thread(
                target=self._orchestrate,
                args=(resume, job_ids, supplements or [], concurrency),
                daemon=True,
                name="match-orchestrator"
            ).start()
            
            return {"ok": True, "task_id": self._task.task_id}
    
    def _orchestrate(self, resume, job_ids, supplements, concurrency):
        """主编排逻辑：三阶段顺序执行"""
        try:
            self._init_embedder()
            self._build_index(resume, job_ids, supplements)
            results = self._score_jobs(job_ids, concurrency)
            self._generate_summary(results)
            
            self._task.status = "completed"
            self._bus.emit(MatchEvent("match_completed", 
                                       total_duration_ms=self._task.elapsed_ms))
        except CancelledError:
            self._task.status = "cancelled"
            self._bus.emit(MatchEvent("cancelled"))
        except AuthFailedError as e:
            self._task.status = "failed"
            self._task.error_message = str(e)
            self._bus.emit(MatchEvent("error", error=str(e), fatal=True))
        except Exception as e:
            self._task.status = "failed"
            self._task.error_message = str(e)
            self._bus.emit(MatchEvent("error", error=str(e)))
        finally:
            self._bus.stop()
    
    def _init_embedder(self):
        """阶段 0: 初始化 embedding 模型，事件推送下载进度"""
        self._bus.emit(MatchEvent("phase_start", "init_model"))
        
        def progress_callback(progress: float, status: str):
            self._check_cancel()
            self._bus.emit(MatchEvent("model_download_progress", 
                                       progress=progress, status=status))
        
        self.embedder = Embedder()
        self.embedder.ensure_model(progress_callback)
        
        self._bus.emit(MatchEvent("phase_done", "init_model"))
    
    def _build_index(self, resume, job_ids, supplements):
        """阶段 1: 知识库构建"""
        self._check_cancel()
        
        chunks_to_embed = []
        
        # 简历切分
        resume_chunks = self.chunker.split_resume(resume)
        chunks_to_embed.extend(resume_chunks)
        
        # 职位切分（从 DB 加载详情）
        job_infos = {}
        for jid in job_ids:
            self._check_cancel()
            detail = self.repo.get_scraped_detail("geek", jid)
            job_info = self._build_job_info(detail)
            job_infos[jid] = job_info
            chunks = self.chunker.split_jd(jid, job_info["title"], 
                                           job_info.get("jd", ""), 
                                           job_info.get("skill_tags", []))
            chunks_to_embed.extend(chunks)
        
        # 补充材料切分
        for supp in supplements:
            chunks = self.chunker.split_resume(supp, source="supplement")
            chunks_to_embed.extend(chunks)
        
        total = len(chunks_to_embed)
        self._bus.emit(MatchEvent("phase_start", "build_index", total=total))
        
        # 批量 embedding + 写入 ChromaDB
        BATCH = 16
        self.vector_store.clear_jobs()
        
        for i in range(0, total, BATCH):
            self._check_cancel()
            batch = chunks_to_embed[i:i+BATCH]
            
            embeddings = self.embedder.embed([c.text for c in batch])
            
            for chunk, emb in zip(batch, embeddings):
                if chunk.source == "resume":
                    self.vector_store.upsert_resume_chunk(chunk, emb)
                elif chunk.source == "supplement":
                    self.vector_store.upsert_supplement_chunk(chunk, emb)
                else:
                    self.vector_store.upsert_job_chunk(chunk, emb)
            
            done = min(i + BATCH, total)
            self._bus.emit(MatchEvent("phase_progress", "build_index",
                                       current=done, total=total,
                                       item=batch[-1].source))
            self._task.index_progress = done / total
        
        self._job_infos = job_infos
        self._bus.emit(MatchEvent("phase_done", "build_index"))
    
    def _score_jobs(self, job_ids, concurrency):
        """阶段 2: 逐岗位评分"""
        self._check_cancel()
        
        self._bus.emit(MatchEvent("phase_start", "job_scoring", 
                                   total=len(job_ids)))
        
        results = []
        completed = 0
        
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {}
            for jid in job_ids:
                self._check_cancel()
                future = executor.submit(self._score_one_job, jid)
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
                        identity="geek", source_id=1,
                        target_job_id=jid,
                        score=result.score,
                        reasoning=result.reasoning,
                        suggestions=result.suggestions,
                        model_name=result.model_name,
                        evidence=result.evidence,
                        gaps=result.gaps,
                        retrieved_chunks=result.retrieved_chunks
                    )
                    
                    self._bus.emit(MatchEvent(
                        "job_scored",
                        job_id=jid,
                        title=self._job_infos[jid]["title"],
                        score=result.score,
                        evidence=result.evidence,
                        reasoning=result.reasoning,
                        gaps=result.gaps,
                        suggestions=result.suggestions,
                        retrieved_chunks=result.retrieved_chunks
                    ))
                except Exception as e:
                    self._bus.emit(MatchEvent(
                        "job_failed", job_id=jid,
                        title=self._job_infos[jid]["title"],
                        error=str(e)
                    ))
                
                self._task.completed = completed
                self._bus.emit(MatchEvent("phase_progress", "job_scoring",
                                           current=completed, 
                                           total=len(job_ids)))
        
        self._bus.emit(MatchEvent("phase_done", "job_scoring",
                                   completed=completed))
        return results
    
    def _score_one_job(self, job_id: str) -> JobScoreResult:
        """单个职位评分（工作线程内执行）"""
        if self._auth_failed.is_set():
            raise CancelledError("认证失败，已中止")
        
        self._check_cancel()
        
        job_info = self._job_infos[job_id]
        
        # 检索简历相关片段
        query_emb = self.embedder.embed_one(
            f"{job_info['title']} {' '.join(job_info.get('skill_tags', []))}"
        )
        retrieved = self.retriever.retrieve_for_job(query_emb, top_k=5)
        
        # 调 LLM 评分
        user_prompt = build_match_user_prompt(job_info, retrieved)
        
        try:
            result = self.ai_client.match_with_evidence(user_prompt, retrieved)
            return result
        except AuthenticationError:
            self._auth_failed.set()
            raise AuthFailedError("API 认证失败")
    
    def _generate_summary(self, results: list[JobScoreResult]):
        """阶段 3: 跨职位综合分析（流式输出）"""
        if not results:
            self._bus.emit(MatchEvent("summary_done", 
                                       structured=None, raw="无匹配结果"))
            return
        
        self._check_cancel()
        self._bus.emit(MatchEvent("phase_start", "summary"))
        
        supplement_chunks = self.retriever.retrieve_supplements(top_k=3)
        
        user_prompt = build_summary_user_prompt(
            results, self._job_infos, supplement_chunks
        )
        
        streamer = SummaryStreamer(self.ai_client, self._bus, self._cancel_event)
        summary = streamer.stream(user_prompt)
        
        self.repo.save_match_summary(
            identity="geek", source_id=1,
            structured=summary.structured,
            raw_text=summary.raw
        )
        
        self._bus.emit(MatchEvent("phase_done", "summary"))
    
    def _check_cancel(self):
        if self._cancel_event.is_set():
            raise CancelledError("用户取消")
    
    def cancel(self):
        self._cancel_event.set()
        return {"ok": True}
```

### 完整事件序列示例

```json
{"type": "phase_start", "phase": "init_model"}
{"type": "model_download_progress", "progress": 0.45, "status": "downloading"}
{"type": "phase_done", "phase": "init_model"}

{"type": "phase_start", "phase": "build_index", "total": 12}
{"type": "phase_progress", "phase": "build_index", "current": 6, "total": 12, "item": "job"}
{"type": "phase_done", "phase": "build_index"}

{"type": "phase_start", "phase": "job_scoring", "total": 10}
{"type": "job_scored", "job_id": "abc", "title": "后端开发", "score": 0.82, 
 "evidence": [...], "gaps": ["K8s"], "suggestions": [...], "retrieved_chunks": ["resume_0", "resume_3"]}
{"type": "job_scored", "job_id": "def", "title": "Java工程师", "score": 0.65, ...}
{"type": "job_failed", "job_id": "xyz", "error": "API timeout"}
{"type": "phase_done", "phase": "job_scoring", "completed": 9}

{"type": "phase_start", "phase": "summary"}
{"type": "summary_chunk", "text": "基于"}
{"type": "summary_chunk", "text": "10个职位的"}
{"type": "summary_chunk", "text": "匹配结果..."}
{"type": "summary_done", "structured": {...}, "raw": "完整文本"}
{"type": "phase_done", "phase": "summary"}

{"type": "match_completed", "total_duration_ms": 15000}
```

---

## 模块 4：前端渐进式渲染

### 事件订阅

```javascript
// frontend/js/app.js

window.__onMatchProgress = (payload) => {
    const evt = typeof payload === 'string' ? JSON.parse(payload) : payload;
    handleMatchEvent(evt);
};

function handleMatchEvent(evt) {
    switch (evt.type) {
        case 'phase_start':          handlePhaseStart(evt); break;
        case 'phase_progress':       handlePhaseProgress(evt); break;
        case 'phase_done':           handlePhaseDone(evt); break;
        case 'model_download_progress': handleModelDownload(evt); break;
        case 'job_scored':           handleJobScored(evt); break;
        case 'job_failed':           handleJobFailed(evt); break;
        case 'summary_chunk':        handleSummaryChunk(evt); break;
        case 'summary_done':         handleSummaryDone(evt); break;
        case 'match_completed':      handleMatchCompleted(evt); break;
        case 'error':                handleMatchError(evt); break;
        case 'cancelled':            handleCancelled(evt); break;
    }
}
```

### 匹配状态机

```javascript
const matchState = {
    phase: 'idle',          // idle / init_model / building / scoring / summarizing / done
    jobs: [],               // 逐岗位结果（按到达顺序）
    summaryBuffer: '',      // 综合分析流式 buffer
    summaryStructured: null,
    vditor: null,
};
```

### 渐进式渲染策略

| 阶段 | 前端展示 | 事件流 |
|---|---|---|
| 模型下载 | "正在下载嵌入模型... 45%" | `model_download_progress` |
| 知识库构建 | "索引中: 简历 ✓ / 职位 3/10" | `phase_progress` |
| 逐岗位评分 | 卡片逐张出现，每张带 score | `job_scored` |
| 综合分析 | 打字机效果流式输出 | `summary_chunk` → `summary_done` |

### 关键渲染函数

```javascript
function handlePhaseProgress(evt) {
    // 只更新进度条，不重建 DOM
    const bar = document.getElementById('match-progress-bar');
    const text = document.getElementById('match-progress-text');
    if (bar && evt.total) {
        bar.style.width = `${(evt.current / evt.total) * 100}%`;
    }
    if (text) {
        text.textContent = `${evt.current}/${evt.total} - ${evt.item || ''}`;
    }
}

function handleJobScored(evt) {
    // 增量插入结果卡片，不重渲染整个列表
    matchState.jobs.push(evt);
    appendJobCard(evt);
    updateResultCount();
}

function handleSummaryChunk(evt) {
    // 打字机效果：append 到 summary 容器
    matchState.summaryBuffer += evt.text;
    const el = document.getElementById('summary-stream');
    if (el) {
        el.textContent = matchState.summaryBuffer;
        el.scrollTop = el.scrollHeight;
    }
}

function handleSummaryDone(evt) {
    matchState.summaryStructured = evt.structured;
    matchState.summaryBuffer = evt.raw;
    
    if (evt.structured) {
        renderStructuredSummary(evt.structured);
    } else {
        renderRawSummary(evt.raw);
    }
}

function appendJobCard(evt) {
    // 直接 append 新卡片到结果列表，不重建已有卡片
    const container = document.getElementById('match-results-list');
    if (!container) return;
    
    const card = createJobCardElement(evt);
    container.appendChild(card);
    sortJobCards(container);  // 按分数插入排序（而非全量重建）
}
```

### 综合分析结构化渲染

```javascript
function renderStructuredSummary(s) {
    const html = `
        <div class="summary-structured">
            <section class="summary-section">
                <h4>技能分析</h4>
                <div class="skill-grid">
                    <div class="skill-col">
                        <h5>已具备</h5>
                        ${renderSkillTags(s.skill_analysis.matching_skills)}
                    </div>
                    <div class="skill-col">
                        <h5>普遍缺失</h5>
                        ${renderSkillTags(s.skill_analysis.missing_skills, 'gap')}
                    </div>
                </div>
            </section>
            
            <section class="summary-section">
                <h4>面试准备</h4>
                <ul class="interview-questions">
                    ${s.interview_prep.likely_questions.map(q => `<li>${q}</li>`).join('')}
                </ul>
                <div class="focus-areas">
                    ${s.interview_prep.focus_areas.map(a => `<span class="tag">${a}</span>`).join('')}
                </div>
            </section>
            
            <section class="summary-section">
                <h4>行动计划</h4>
                <ol class="action-plan">
                    ${s.action_plan.map(a => `
                        <li>
                            <span class="priority ${a.priority}">${a.priority}</span>
                            <span class="action">${a.action}</span>
                            <span class="timeline">${a.timeline}</span>
                        </li>
                    `).join('')}
                </ol>
            </section>
            
            <section class="summary-section overall">
                <h4>整体策略</h4>
                <p>${s.overall_strategy}</p>
            </section>
        </div>
    `;
    document.getElementById('summary-container').innerHTML = html;
}
```

### 局部 DOM 更新（保持滚动位置）

```javascript
function updateMatchDynamicArea() {
    // 只更新 #match-dynamic-area 内部，不影响 Vditor 和职位选择区
    const area = document.getElementById('match-dynamic-area');
    if (!area) return;
    
    area.innerHTML = renderMatchDynamic();
    bindMatchResultEvents();
}
```

---

## 模块 5：数据库与依赖

### 数据库 Schema 扩展

`match_result` 表新增字段：

```sql
ALTER TABLE match_result ADD COLUMN evidence TEXT;           -- JSON: [{claim, source, relevance}]
ALTER TABLE match_result ADD COLUMN gaps TEXT;               -- JSON: ["缺口1", "缺口2"]
ALTER TABLE match_result ADD COLUMN retrieved_chunks TEXT;   -- JSON: ["resume_0", "resume_3"]
```

新增 `match_summary` 表：

```sql
CREATE TABLE IF NOT EXISTS match_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL DEFAULT 'geek',
    source_id INTEGER NOT NULL DEFAULT 1,
    structured TEXT,       -- JSON: 综合分析结构化结果
    raw_text TEXT,         -- 原始文本（兜底）
    model_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(identity, source_id)
);
```

**迁移策略**：在 `Database.__init__` 中用 `ALTER TABLE ADD COLUMN` + `CREATE TABLE IF NOT EXISTS`，兼容已有数据库（新增字段默认 NULL，不影响旧数据）。

### 新增依赖

```toml
# pyproject.toml
dependencies = [
    # ... 现有 ...
    "chromadb>=0.5.0",                # 向量存储
    "sentence-transformers>=3.0.0",   # 本地 embedding
    "torch>=2.0.0",                   # sentence-transformers 依赖（CPU only）
]
```

**注意**：
- `sentence-transformers` 会自动拉取 `torch`、`transformers`、`tokenizers` 等依赖
- 首次安装包体积较大（~2GB），但用户只需安装一次
- 可通过 `pip install torch --index-url https://download.pytorch.org/whl/cpu` 安装 CPU 版 torch 减小体积

### 文件结构

```
src/
├── ai/
│   ├── __init__.py
│   ├── prompts.py          # 重构: 新 Prompt 模板
│   ├── client.py           # 重构: 新增 match_with_evidence / stream_chat
│   ├── matcher.py          # 重构: RAG 编排器 (三阶段)
│   ├── event_bus.py        # 新增: 事件总线
│   ├── summarizer.py       # 新增: 流式综合分析
│   ├── chunker.py          # 新增: 语义切分
│   ├── embedder.py         # 新增: 本地 embedding
│   ├── retriever.py        # 新增: RAG 检索
│   └── vector_store.py     # 新增: ChromaDB 封装
├── api/
│   ├── bridge.py           # 修改: 新增 upload_supplement / get_match_summary
│   └── geek_api.py         # 修改: 适配新 Matcher 接口
├── db/
│   ├── database.py         # 修改: Schema 扩展
│   └── repository.py       # 修改: 新增 save_match_summary / 字段扩展
└── core/
    └── resume_parser.py    # 不变

frontend/
├── js/
│   ├── api.js              # 修改: 新增 uploadSupplement / getMatchSummary
│   └── app.js              # 重构: 事件驱动渲染 + 渐进式输出
├── css/
│   └── style.css           # 修改: 综合分析样式 + Loading 样式
└── index.html              # 修改: 补充材料上传区域
```

---

## bridge.py 改动

```python
# src/api/bridge.py 新增方法

def start_match(self, job_ids_json, supplements_json="[]"):
    """启动 RAG 匹配，支持补充材料"""
    try:
        job_ids = json.loads(job_ids_json) if isinstance(job_ids_json, str) else job_ids_json
        supplements = json.loads(supplements_json) if isinstance(supplements_json, str) else supplements_json
    except:
        return {"ok": False, "error": "参数解析失败"}
    
    resume = self.repo.get_resume()
    if not resume:
        return {"ok": False, "error": "请先保存简历"}
    
    return self._get_geek_api().start_match(resume, job_ids, supplements)

def upload_supplement(self, filename, base64_content):
    """上传补充材料，返回提取的文本"""
    import base64
    from src.core.resume_parser import parse_resume_file
    try:
        file_bytes = base64.b64decode(base64_content)
        content = parse_resume_file(filename, file_bytes)
        return {"ok": True, "data": {"content": content, "filename": filename}}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def get_match_summary(self):
    """获取综合分析结果"""
    return self._get_geek_api().get_match_summary()
```

---

## 实施顺序

```
Phase A: 基础设施（不涉及 UI，纯后端）
  1. pyproject.toml — 添加 chromadb / sentence-transformers 依赖
  2. src/db/database.py — Schema 扩展 (ALTER TABLE + match_summary 表)
  3. src/db/repository.py — 新增 save_match_summary / 字段扩展
  4. src/ai/chunker.py — 新建：语义切分
  5. src/ai/embedder.py — 新建：本地 embedding 模型
  6. src/ai/vector_store.py — 新建：ChromaDB 封装
  7. src/ai/retriever.py — 新建：RAG 检索

Phase B: 核心逻辑（替换旧匹配流程）
  8. src/ai/event_bus.py — 新建：事件总线
  9. src/ai/prompts.py — 重构：新 Prompt（evidence/gaps + 综合分析）
  10. src/ai/client.py — 重构：match_with_evidence + stream_chat
  11. src/ai/summarizer.py — 新建：流式综合分析
  12. src/ai/matcher.py — 重构：三阶段编排器

Phase C: API + 前端（连通端到端）
  13. src/api/bridge.py — 修改：新增方法 + 适配
  14. src/api/geek_api.py — 修改：适配新 Matcher
  15. frontend/js/api.js — 新增方法
  16. frontend/css/style.css — 综合分析 + Loading 样式
  17. frontend/index.html — 补充材料上传区域
  18. frontend/js/app.js — 重构：事件驱动 + 渐进式渲染
```

**每个 Phase 内部的步骤可以并行开发，Phase 之间有依赖关系**。Phase A 是基础，Phase B 依赖 A，Phase C 依赖 B。

---

## 验证清单

1. 首次启动匹配 → 触发 embedding 模型下载，前端显示下载进度
2. 模型下载完成 → 进入知识库构建阶段，前端显示"索引中 X/Y"
3. 知识库构建完成 → 进入逐岗位评分，结果卡片逐张出现
4. 每张结果卡片显示 score / reasoning / evidence（可展开看引用的简历片段）
5. 所有职位评分完成 → 进入综合分析，打字机效果流式输出
6. 综合分析完成 → 渲染为结构化卡片（技能分析 / 面试准备 / 行动计划 / 整体策略）
7. 上传补充材料（PDF/DOCX）→ 提取文本 → 可编辑 → 参与匹配检索
8. 任意阶段点击取消 → 立即停止，前端显示"已取消"
9. API 认证失败 → 中止所有后续匹配，前端显示错误
10. 关闭重开应用 → 已完成的匹配结果从 DB 恢复，综合分析结果持久化
11. 重新匹配同一批职位 → 覆盖旧结果（ON CONFLICT 更新）
12. 职位 JD 很长（>2000 字）→ 切分为多个 chunk，检索精度不受影响

---

## 风险与权衡

### 已知风险

1. **首次下载 embedding 模型 ~100MB**：用户首次匹配需等待 30 秒下载。缓解：启动应用时预下载（可选）、显示明确进度
2. **torch 依赖体积大（~2GB）**：增加安装成本。缓解：引导安装 CPU 版 torch
3. **ChromaDB 持久化文件增长**：长期使用会积累。缓解：提供"清理向量库"按钮
4. **LLM 输出 JSON 解析失败**：流式输出可能产生不完整 JSON。缓解：两阶段解析 + raw_text 兜底
5. **bge-small-zh 模型能力边界**：对非常专业的领域（医疗/法律）检索精度可能不足。缓解：允许用户配置其他 HuggingFace 模型

### 不在本次范围

- Boss 端匹配（本次只重构 geek 端）
- 向量库的增量更新（本次每次匹配前 clear_jobs 全量重建）
- 多用户/多简历支持（source_id 硬编码为 1）
- embedding 模型的微调
- 综合分析结果导出为 PDF/DOCX
