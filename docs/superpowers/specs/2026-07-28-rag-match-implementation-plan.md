# Phase 3 RAG 架构重构 — 实施计划

**对应 Spec**: `docs/superpowers/specs/2026-07-28-rag-match-architecture-design.md`
**目标**: 将 AI 匹配从纯 LLM 判断重构为 RAG 架构，事件驱动 + 渐进式输出 + 结构化 JSON

---

## Phase A：基础设施（纯后端，无 UI 改动）

### A1. 添加依赖

**文件**: `pyproject.toml`

```toml
dependencies = [
    # ... 现有 ...
    "chromadb>=0.5.0",
    "sentence-transformers>=3.0.0",
]
```

> torch 由 sentence-transformers 自动拉取。不显式声明 torch，让 sentence-transformers 自行解析兼容版本。

**验证**: `uv sync` 成功，`python -c "import chromadb; import sentence_transformers"` 无报错

---

### A2. 数据库 Schema 扩展

**文件**: `src/db/database.py`

**改动 1**: `SCHEMA_SQL` 末尾新增 `match_summary` 表：

```sql
CREATE TABLE IF NOT EXISTS match_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL DEFAULT 'geek',
    source_id INTEGER NOT NULL DEFAULT 1,
    structured TEXT,
    raw_text TEXT,
    model_name TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(identity, source_id)
);
```

**改动 2**: `_migrate()` 方法新增 `match_result` 表的字段扩展：

```python
def _migrate(self, conn):
    # 现有 chrome_state 迁移...
    
    # 新增 match_result 字段扩展
    match_cols = {r[1] for r in conn.execute("PRAGMA table_info(match_result)").fetchall()}
    if "evidence" not in match_cols:
        conn.execute("ALTER TABLE match_result ADD COLUMN evidence TEXT")
    if "gaps" not in match_cols:
        conn.execute("ALTER TABLE match_result ADD COLUMN gaps TEXT")
    if "retrieved_chunks" not in match_cols:
        conn.execute("ALTER TABLE match_result ADD COLUMN retrieved_chunks TEXT")
    conn.commit()
```

**验证**: 应用启动无报错；老数据库升级后 `PRAGMA table_info(match_result)` 包含新字段；`match_summary` 表存在

---

### A3. Repository 扩展

**文件**: `src/db/repository.py`

**改动 1**: `save_match_result()` 方法签名扩展，新增 `evidence`、`gaps`、`retrieved_chunks` 参数（均可选，向后兼容）：

```python
def save_match_result(self, identity, source_id, target_job_id,
                      score, reasoning, suggestions, model_name,
                      evidence=None, gaps=None, retrieved_chunks=None):
    # JSON 序列化 evidence/gaps/retrieved_chunks
    # INSERT ... ON CONFLICT 更新所有字段
```

**改动 2**: `get_match_results_with_jobs()` 和 `get_match_results()` 反序列化新字段：

```python
# 在现有 suggestions 反序列化逻辑旁添加
if isinstance(d.get("evidence"), str):
    try: d["evidence"] = json.loads(d["evidence"])
    except: d["evidence"] = []
if isinstance(d.get("gaps"), str):
    try: d["gaps"] = json.loads(d["gaps"])
    except: d["gaps"] = []
if isinstance(d.get("retrieved_chunks"), str):
    try: d["retrieved_chunks"] = json.loads(d["retrieved_chunks"])
    except: d["retrieved_chunks"] = []
```

**改动 3**: 新增 `save_match_summary()` 和 `get_match_summary()`：

```python
def save_match_summary(self, identity, source_id, structured, raw_text, model_name=""):
    # ON CONFLICT(identity, source_id) 更新

def get_match_summary(self, identity, source_id=1):
    # 返回最新一条 match_summary 记录
```

**验证**: 单元测试调用 `save_match_result` 带新字段 → `get_match_results_with_jobs` 能读回；`save_match_summary` + `get_match_summary` 读写一致

---

### A4. Chunker — 语义切分

**新建文件**: `src/ai/chunker.py`

**实现**:
- `Chunk` dataclass: `id, text, source, job_id, section, metadata`
- `Chunker` 类:
  - `split_resume(text, source="resume") -> list[Chunk]`：按 markdown 标题/空行分段，超长段按句号二次切分，每段 200~400 tokens
  - `split_jd(job_id, title, jd_text, skill_tags) -> list[Chunk]`：技能标签独立 chunk（`section: "skills"`），JD 正文按段落切分
- chunk_id 生成规则：`{source}_{index}` 或 `{source}_{job_id}_{index}`

**验证**: 
- 简历切分：3000 字简历 → 5~8 个 chunks，每 chunk 长度在范围内
- JD 切分：含技能标签的 JD → 至少 2 个 chunks（skills + 正文段落）
- 边界：空文本 → 返回空列表

---

### A5. Embedder — 本地 Embedding

**新建文件**: `src/ai/embedder.py`

**实现**:
- `Embedder` 类:
  - `DEFAULT_MODEL = "BAAI/bge-small-zh-v1.1"`
  - `__init__(model_name=None, cache_dir="~/.boss-match/models")`
  - `ensure_model(progress_callback=None)`：延迟加载 SentenceTransformer，首次下载通过 `progress_callback` 推送进度
  - `embed(texts: list[str]) -> list[list[float]]`：批量 embedding，`normalize_embeddings=True`
  - `embed_one(text) -> list[float]`

**进度回调实现**：
- SentenceTransformer 构造时无原生下载进度回调
- 使用 `sentence_transformers.util.http_get` 包装或独立线程监控 `cache_dir` 文件大小变化
- 简化方案：推送 "downloading" / "ready" 两阶段事件，不精确到百分比（如需精确进度可后续优化）

**验证**:
- 首次调用 `ensure_model()` → 触发下载，`~/.boss-match/models/` 出现模型文件
- `embed(["你好", "世界"])` → 返回 2 个向量，维度 512，L2 范数 ≈ 1.0
- 第二次调用 `ensure_model()` → 立即返回，无下载

---

### A6. VectorStore — ChromaDB 封装

**新建文件**: `src/ai/vector_store.py`

**实现**:
- `VectorStore` 类:
  - `__init__(persist_dir="~/.boss-match/chromadb")`：创建 PersistentClient，初始化 3 个 collection（resume / supplement / jobs），均用 cosine 距离
  - `upsert_resume_chunks(chunks, embeddings)` / `upsert_supplement_chunks(...)` / `upsert_job_chunks(...)`
  - `query(query_embedding, collection, top_k=5) -> list[RetrievalResult]`：支持指定 collection 检索
  - `query_multi(query_embedding, collections, top_k=5) -> list[RetrievalResult]`：多 collection 合并检索，按相似度全局排序取 top_k
  - `get_job_chunks(job_id) -> list[Chunk]`：按 metadata.job_id 过滤
  - `clear_jobs()` / `clear_supplement()` / `clear_all()`

**RetrievalResult dataclass**: `chunk_id, text, source, section, score, metadata`

**验证**:
- 写入 3 个 resume chunks + 5 个 job chunks → `query` 能检索到结果
- `clear_jobs()` 后 jobs collection 为空，resume 不受影响
- `query_multi(["resume", "supplement"], top_k=5)` 合并两个 collection 结果

---

### A7. Retriever — RAG 检索

**新建文件**: `src/ai/retriever.py`

**实现**:
- `Retriever` 类:
  - `__init__(vector_store: VectorStore, embedder: Embedder)`
  - `retrieve_for_job(query_embedding, top_k=5) -> list[RetrievalResult]`：调用 `vector_store.query_multi(["resume", "supplement"], ...)` 检索与职位最相关的简历+补充片段
  - `retrieve_supplements(top_k=3) -> list[RetrievalResult]`：为综合分析阶段检索关键补充材料（从 supplement collection 取前 N 条）

**验证**:
- 简历含"Python 后端开发 5 年" → 查询 "Python 工程师" → 检索到该片段，score > 0.5
- 无补充材料时 `retrieve_supplements()` 返回空列表

---

## Phase B：核心逻辑（替换旧匹配流程）

### B1. EventBus — 事件总线

**新建文件**: `src/ai/event_bus.py`

按 spec 模块 3 实现 `MatchEvent` + `EventBus`。

**验证**:
- 多线程并发 `emit` 100 个事件 → 单线程串行派发，顺序正确
- `stop()` 后消费者线程正常退出

---

### B2. Prompts 重构

**文件**: `src/ai/prompts.py`

**改动 1**: 替换 `MATCH_SYSTEM_PROMPT` 为严格限制版（含 evidence/gaps/suggestions 字段，铁律 4 条）

**改动 2**: 替换 `build_match_user_prompt(resume, job_detail)` 为 `build_match_user_prompt(job_info, resume_chunks)`：
- 参数从 `resume: str` 改为 `resume_chunks: list[RetrievalResult]`
- job_info 字段新增 `company_scale/stage/industry`
- 输出格式：【简历依据】+【职位信息】+【评分要求】

**改动 3**: 新增 `SUMMARY_SYSTEM_PROMPT` 和 `build_summary_user_prompt(job_results, job_infos, supplement_chunks)`

**验证**: Prompt 文本检查，字段对齐 spec；`build_match_user_prompt` 接受新参数签名

---

### B3. AIClient 重构

**文件**: `src/ai/client.py`

**改动 1**: `MatchResult` 扩展为 `JobScoreResult`，新增 `job_id, evidence, gaps, retrieved_chunks` 字段

**改动 2**: 新增 `match_with_evidence(user_prompt, retrieved_chunks) -> JobScoreResult`：
- 调 LLM（temperature=0.2, json_object mode）
- 解析 JSON 提取 score/evidence/reasoning/gaps/suggestions
- `retrieved_chunks` 从参数传入（chunk_id 列表）

**改动 3**: 保留 `match_job_seeker()` 方法但标记为 deprecated（或直接删除，spec 表明完全替换）

**改动 4**: 新增 `stream_chat(messages, temperature=0.4) -> iterator`：
- 返回 OpenAI stream 迭代器，供 `SummaryStreamer` 消费

**改动 5**: 错误处理保持现有逻辑（AuthenticationError/RateLimitError/APIConnectionError）

**验证**:
- `match_with_evidence` 返回的 JobScoreResult 字段完整
- `stream_chat` 返回的迭代器能逐 chunk 产出 delta.content

---

### B4. SummaryStreamer — 流式综合分析

**新建文件**: `src/ai/summarizer.py`

按 spec 模块 2 实现 `SummaryStreamer` + `SummaryResult`。

**实现要点**:
- `stream(user_prompt) -> SummaryResult`
- 每个 delta 推送 `MatchEvent("summary_chunk", text=delta)`
- 流结束后两阶段 JSON 解析 → `structured` 字段
- 推送 `MatchEvent("summary_done", structured=..., raw=...)`
- `cancel_event` 检查 → 关闭 stream + 抛 CancelledError

**验证**:
- Mock LLM stream 返回完整 JSON → `structured` 字段解析成功
- Mock 返回非 JSON 文本 → `structured=None`, `raw` 保留原文
- 中途 cancel → 抛 CancelledError，已产出 chunk 已推送

---

### B5. Matcher 重构为三阶段编排器

**文件**: `src/ai/matcher.py`

**完全重写**，按 spec 模块 3 实现：

- `MatchTask` 扩展：新增 `phase`、`index_progress`、`elapsed_ms` 字段
- `Matcher.__init__`: 新增 `_bus`, `_cancel_event`, `_auth_failed`
- `start_match(resume, job_ids, supplements=None, concurrency=3)`：启动 EventBus + 后台编排线程
- `_orchestrate()`：三阶段顺序调用 + 错误处理 + finally stop bus
- `_init_embedder()`：阶段 0，推送 `model_download_progress`
- `_build_index(resume, job_ids, supplements)`：阶段 1，批量 embedding + ChromaDB 写入，推送 `phase_progress`
- `_score_jobs(job_ids, concurrency)`：阶段 2，ThreadPoolExecutor 并发，每完成一个推送 `job_scored`
- `_score_one_job(job_id)`：单职位评分（检索 + LLM 调用）
- `_generate_summary(results)`：阶段 3，调 `SummaryStreamer`
- `_check_cancel()`：各阶段入口检查
- `cancel()`：设置 `_cancel_event`

**自定义异常**: `CancelledError`, `AuthFailedError`

**验证**:
- 启动匹配 → 事件流按 `phase_start` → `phase_progress` × N → `phase_done` → `job_scored` × N → `summary_chunk` × N → `summary_done` → `match_completed` 顺序产出
- `cancel()` 后各阶段停止，推送 `cancelled` 事件
- API 认证失败 → 推送 `error` 事件（fatal=True），任务终止

---

## Phase C：API + 前端（连通端到端）

### C1. bridge.py 扩展

**文件**: `src/api/bridge.py`

**改动 1**: `start_match` 方法签名扩展支持 `supplements_json`：

```python
def start_match(self, job_ids_json, supplements_json="[]"):
    job_ids = json.loads(job_ids_json) if isinstance(job_ids_json, str) else job_ids_json
    supplements = json.loads(supplements_json) if isinstance(supplements_json, str) else supplements_json
    resume = self.repo.get_resume()
    if not resume:
        return {"ok": False, "error": "请先保存简历"}
    return self._get_geek_api().start_match(resume, job_ids, supplements)
```

**改动 2**: 新增 `upload_supplement(filename, base64_content)`：复用 `parse_resume_file` 解析

**改动 3**: 新增 `get_match_summary()`：返回最新综合分析结果

**改动 4**: `_notify_frontend` 适配新事件类型（原 `type: "match"` 改为透传 EventBus 的 `type` 字段）

> ⚠️ `_notify_frontend` 当前根据 `type` 字段分发到 `__onScrapeProgress` / `__onMatchProgress`。RAG 事件系统的 `type` 值（`phase_start`/`job_scored`/`summary_chunk` 等）需要全部路由到 `__onMatchProgress`。需修改路由逻辑：match 相关事件一律走 `__onMatchProgress`。

**验证**: 前端能调 `api.uploadSupplement()` / `api.getMatchSummary()`；`api.startMatch(jobIds, supplements)` 参数传递正确

---

### C2. geek_api.py 适配

**文件**: `src/api/geek_api.py`

**改动 1**: `start_match(job_ids_json)` 改为 `start_match(resume, job_ids, supplements)`，直接委托给 `self._matcher.start_match(resume, job_ids, supplements)`

**改动 2**: 新增 `get_match_summary()`：委托 `self.repo.get_match_summary("geek", source_id=1)`

**改动 3**: `get_match_results` 返回数据新增 `evidence/gaps/retrieved_chunks` 字段（由 repository 层反序列化）

**验证**: bridge → geek_api → matcher 调用链畅通；`get_match_summary` 能返回 DB 中的最新记录

---

### C3. 前端 api.js 扩展

**文件**: `frontend/js/api.js`

新增方法：
```javascript
uploadSupplement(filename, base64Content) {
    return this.call('upload_supplement', filename, base64Content);
},
getMatchSummary() { return this.call('get_match_summary'); },
// startMatch 改为支持 supplements 参数
startMatch(jobIds, supplements) {
    return this.call('start_match', JSON.stringify(jobIds), JSON.stringify(supplements || []));
},
```

**验证**: 浏览器控制台 `api.uploadSupplement` / `api.getMatchSummary` 函数存在

---

### C4. 前端 CSS — 综合分析 + Loading 样式

**文件**: `frontend/css/style.css`

新增样式：
- `.match-progress-card` / `.progress-phase` / `.progress-stats` / `.progress-bar-track` / `.progress-bar-fill`（复用现有）
- `.summary-stream` — 打字机流式输出容器，monospace 字体，自动滚动
- `.summary-structured` — 结构化综合分析容器
- `.summary-section` — 各分区（技能分析/面试准备/行动计划/整体策略）
- `.skill-grid` / `.skill-col` — 技能两列布局
- `.skill-tag.matched` / `.skill-tag.gap` — 已具备/缺失技能标签颜色区分
- `.interview-questions` / `.focus-areas` / `.action-plan` / `.priority` — 各子元素样式
- `.model-download-card` — 模型下载进度卡片
- `.job-evidence` — 职位卡片中"引用依据"展开区

**验证**: 样式与现有暗色主题一致（`#0f1115` 背景、`#4a9eff` 主色、`#16191f` 卡片）

---

### C5. 前端 index.html — 补充材料上传区域

**文件**: `frontend/index.html`

匹配页新增补充材料上传入口（在简历区域下方）：
```html
<!-- 由 app.js 动态渲染，结构： -->
<div class="supplement-section">
    <h3>补充材料（可选）</h3>
    <div class="supplement-upload-area">
        <input type="file" id="supplement-file" accept=".pdf,.docx,.txt,.md">
        <span class="upload-hint">上传面试经验、目标公司资料等，增强匹配依据</span>
    </div>
    <div id="supplement-list"><!-- 已上传的补充材料列表 --></div>
</div>
```

**验证**: 匹配页出现补充材料上传区域，文件选择器接受指定格式

---

### C6. 前端 app.js 重构 — 事件驱动渲染

**文件**: `frontend/js/app.js`

**改动 1**: `matchState` 扩展：
```javascript
const matchState = {
    phase: 'idle',
    jobs: [],
    summaryBuffer: '',
    summaryStructured: null,
    supplements: [],       // 新增：补充材料列表
    vditor: null,
};
```

**改动 2**: `window.__onMatchProgress` 重写为事件分发器 `handleMatchEvent(evt)`，按 `evt.type` 路由到各 handler

**改动 3**: 实现 11 个事件 handler：
- `handlePhaseStart` / `handlePhaseProgress` / `handlePhaseDone`
- `handleModelDownload`
- `handleJobScored` — 增量 `appendJobCard`，不调 `renderPage`
- `handleJobFailed`
- `handleSummaryChunk` — append 到 `#summary-stream`，自动滚到底
- `handleSummaryDone` — 结构化 JSON 渲染
- `handleMatchCompleted`
- `handleMatchError` / `handleCancelled`

**改动 4**: `renderMatchesPage()` 新增：
- 补充材料上传区域
- `#match-dynamic-area` 容器（进度 + 结果 + 综合分析）
- 保留 Vditor 简历编辑器

**改动 5**: 新增函数：
- `appendJobCard(evt)` — DOM append + 按分数排序插入
- `renderStructuredSummary(s)` — 渲染技能网格/面试问题/行动计划/整体策略
- `renderRawSummary(text)` — 降级渲染
- `handleSupplementUpload(file)` — FileReader → base64 → `api.uploadSupplement` → 加入 `matchState.supplements`
- `startMatch()` — 从 Vditor 取简历 + 收集 supplements + 调 `api.startMatch(jobIds, supplements)`

**改动 6**: `renderPage()` / `navigateTo()` 保持现有 Vditor 销毁/重建逻辑，`#match-dynamic-area` 局部更新不触发全量重建

**验证**（手动测试）:
1. 启动匹配 → 显示"下载嵌入模型..." → "索引中 X/Y" → 结果卡片逐张出现 → 综合分析打字机输出 → 最终结构化卡片
2. 匹配过程中页面滚动位置保持
3. 点击取消 → 立即停止，显示"已取消"
4. 上传 PDF 补充材料 → 提取文本 → 保存 → 参与匹配
5. 关闭重开应用 → 匹配结果和综合分析从 DB 恢复

---

## 实施顺序总结

```
Phase A (后端基础设施):
  A1 pyproject.toml → A2 database.py → A3 repository.py
  → A4 chunker.py → A5 embedder.py → A6 vector_store.py → A7 retriever.py

Phase B (核心逻辑):
  B1 event_bus.py → B2 prompts.py → B3 client.py
  → B4 summarizer.py → B5 matcher.py

Phase C (端到端连通):
  C1 bridge.py → C2 geek_api.py → C3 api.js
  → C4 style.css → C5 index.html → C6 app.js
```

**每个步骤完成后立即提交**，提交信息格式：`feat(rag): A1 添加 chromadb/sentence-transformers 依赖`。

**Phase 间验证点**:
- Phase A 完成：`python -c "from src.ai.retriever import Retriever"` 无报错
- Phase B 完成：`python -c "from src.ai.matcher import Matcher"` 无报错，Mock 调用能跑通三阶段
- Phase C 完成：启动应用，完整跑一次匹配流程

---

## 回滚策略

- 所有改动在 `master` 分支提交，每个步骤独立 commit
- 若某步骤引入问题，`git revert` 单个 commit 即可
- 数据库迁移用 `ALTER TABLE ADD COLUMN`（新增字段默认 NULL），不破坏老数据
- ChromaDB 数据存 `~/.boss-match/chromadb/`，删除目录即可清空向量库
- embedding 模型存 `~/.boss-match/models/`，删除目录即可重新下载

---

## 不在本次实施范围

- Boss 端匹配重构（本次仅 geek 端）
- embedding 模型微调
- 向量库增量更新（每次匹配前 `clear_jobs` 全量重建）
- 多用户/多简历支持（source_id 仍硬编码为 1）
- 综合分析结果导出为 PDF/DOCX
- 模型下载精确百分比进度（本次仅 "downloading" / "ready" 两阶段）
