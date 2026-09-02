// App initialization, routing, and page rendering
(async function() {
    let currentPage = 'dashboard';
    let currentIdentity = 'geek';
    let chromeStatus = { running: false, logged_in: false };

    // Embedder model state
    let embedderState = {
        status: 'idle',
        progress: 0,
        speed: 0,
        error: '',
    };

    // Dashboard page state
    let dashboardState = {
        activeResume: null,
        totalJobs: 0,
        totalMatches: 0,
        avgScore: 0,
        recentMatches: [],
        recentOptimizations: [],
        loading: true,
    };

    // Search state (used within job center)
    let searchState = {
        keyword: '',
        city: '',
        maxPages: 3,
        filters: {},
        filterOptions: null,
        citySuggestions: [],
        scrapeProgress: null,
        pollTimer: null,
        results: [],
        resultTotal: 0,
        resultOffset: 0,
        resultLimit: 50,
        selectedJob: null,
        detailData: null,
        aiSuggestion: null,
        aiSuggestionLoading: false,
        pipelineRunning: false,
        pipelineProgress: null,
        pipelinePollTimer: null,
        pipelineMinScore: 0.4,
    };

    // Match state (used within job center)
    let matchState = {
        selectedJobIds: new Set(),
        matchProgress: null,
        pollTimer: null,
        results: [],
        resultTotal: 0,
        resultOffset: 0,
        resultLimit: 50,
        expandedJobId: null,
        availableJobs: [],
        availableJobsLoaded: false,
        supplements: [],
        phase: 'idle',
        phaseProgress: null,
        modelDownloadStatus: null,
        streamedJobs: [],
        summaryBuffer: '',
        summaryStructured: null,
    };

    // Resume management state (used within resume center)
    let resumePageState = {
        resumes: [],
        editingId: null,
        vditor: null,
        content: '',
        contentDirty: false,
        _silenceInput: false,
        supplements: [],
    };

    // Optimization state (used within resume center)
    let optimizationState = {
        optimizations: [],
        activeOptimization: null,
        generating: false,
        progress: null,
        pollTimer: null,
    };

    // Interview prep state (used within job center)
    let interviewPrepState = {
        preps: [],
        activePrep: null,
        generating: false,
        progress: null,
        pollTimer: null,
        expandedQuestionId: null,
    };

    // Summary state (used within job center)
    let summaryState = {
        summary: null,
        loading: false,
    };

    // ---- Utility ----

    function debounce(fn, delay) {
        let timer;
        return function(...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    // ---- Shared Components ----

    function renderEmbedderCard() {
        const es = embedderState;
        if (es.status === 'idle') {
            return `<div class="embedder-card embedder-idle">
                <div class="embedder-label">嵌入模型</div>
                <div class="embedder-detail">等待下载...</div>
            </div>`;
        }
        if (es.status === 'downloading') {
            const pct = Math.round(es.progress * 100);
            const speedText = es.speed > 0 ? ` · <span class="download-speed">${es.speed.toFixed(1)} MB/s</span>` : '';
            return `<div class="embedder-card embedder-downloading">
                <div class="embedder-label">下载嵌入模型</div>
                <div class="embedder-detail">${pct}%${speedText}</div>
                <div class="progress-bar-track"><div class="progress-bar-fill" style="width: ${pct}%"></div></div>
            </div>`;
        }
        if (es.status === 'ready') {
            return `<div class="embedder-card embedder-ready">
                <div class="embedder-label">嵌入模型</div>
                <div class="embedder-detail">已就绪</div>
            </div>`;
        }
        return `<div class="embedder-card embedder-failed">
            <div class="embedder-label">嵌入模型</div>
            <div class="embedder-detail">下载失败: ${es.error || '未知错误'}</div>
            <button class="btn btn-warning btn-sm" id="btn-retry-embedder">重试</button>
        </div>`;
    }

    function renderChromeStatusCard() {
        const identity = currentIdentity;
        const identityLabel = identity === 'geek' ? '求职者' : '招聘者';
        const loginUrl = identity === 'geek'
            ? 'https://www.zhipin.com/web/user/'
            : 'https://www.zhipin.com/web/boss/';
        const running = chromeStatus.running;
        const loggedIn = chromeStatus.logged_in;

        let statusClass = 'chrome-stopped';
        let statusText = '未启动';
        let statusIcon = '⏹';
        if (running && loggedIn) {
            statusClass = 'chrome-logged-in';
            statusText = '已登录';
            statusIcon = '✅';
        } else if (running) {
            statusClass = 'chrome-running';
            statusText = '未登录';
            statusIcon = '🔄';
        }

        return `
        <div class="chrome-status ${statusClass}">
            <span class="status-icon">${statusIcon}</span>
            <div class="status-info">
                <div class="status-label">Chrome 状态</div>
                <div class="status-value">${statusText}</div>
            </div>
        </div>
        <div class="chrome-actions">
            ${!running ? `<button class="btn btn-primary btn-sm" id="btn-start-chrome">启动 Chrome</button>` : ''}
            ${running && !loggedIn ? `<button class="btn btn-warning btn-sm" id="btn-check-login">检测登录状态</button>` : ''}
            ${running ? `<button class="btn btn-danger btn-sm" id="btn-stop-chrome">关闭 Chrome</button>` : ''}
        </div>`;
    }

    function renderFilterDropdowns() {
        const opts = searchState.filterOptions;
        if (!opts) return '<div class="loading-filters">加载筛选选项...</div>';

        const filters = [
            { key: 'salary', label: '薪资', options: opts.salary },
            { key: 'experience', label: '经验', options: opts.experience },
            { key: 'degree', label: '学历', options: opts.degree },
            { key: 'scale', label: '规模', options: opts.scale },
            { key: 'stage', label: '融资', options: opts.stage },
        ];

        return filters.map(f => `
            <div class="form-group filter-group">
                <label>${f.label}</label>
                <select id="filter-${f.key}" data-filter="${f.key}">
                    <option value="">不限</option>
                    ${Object.entries(f.options).map(([label, code]) =>
                        `<option value="${label}" ${searchState.filters[f.key]===label?'selected':''}>${label}</option>`
                    ).join('')}
                </select>
            </div>
        `).join('');
    }

    function renderProgressIndicator(progress) {
        const phase = progress.phase === 'list' ? '列表抓取' :
                      progress.phase === 'details' ? '详情抓取' :
                      progress.phase === 'saving' ? '保存数据' : '准备中';
        const pageText = progress.phase === 'list' ? `第 ${progress.current_page}/${progress.total_pages} 页` : '';
        const detailText = progress.phase === 'details' ? `已抓 ${progress.details_scraped} 条详情` : '';
        const percent = progress.phase === 'list'
            ? Math.round((progress.current_page / Math.max(progress.total_pages, 1)) * 60)
            : progress.phase === 'details'
            ? 60 + Math.round((progress.details_scraped / Math.max(progress.jobs_found, 1)) * 35)
            : progress.phase === 'saving' ? 95 : 5;

        return `
        <div class="progress-card">
            <div class="progress-header">
                <span class="progress-phase">${phase}</span>
                <span class="progress-stats">${pageText}${detailText} · ${progress.jobs_found} 个职位</span>
            </div>
            <div class="progress-bar-track">
                <div class="progress-bar-fill" style="width: ${percent}%"></div>
            </div>
        </div>`;
    }

    function renderPipelineProgress() {
        const p = searchState.pipelineProgress;
        if (!p) {
            return `<div class="pipeline-progress-card">
                <div class="pipeline-phase-label">一体化流水线</div>
                <div class="pipeline-phase-detail">启动中...</div>
            </div>`;
        }

        const phase = p.phase === 'init' ? '初始化' :
                      p.phase === 'scraping' ? '爬取中' :
                      p.phase === 'scoring' ? '评分中' :
                      p.phase === 'summary' ? '生成摘要' : p.phase;

        const scrapePct = p.total_pages > 0 ? Math.round((p.current_page / p.total_pages) * 100) : 0;
        const scorePct = p.jobs_found > 0 ? Math.round((p.jobs_scored / p.jobs_found) * 100) : 0;

        return `
        <div class="pipeline-progress-card">
            <div class="pipeline-header">
                <span class="pipeline-phase-label">一体化流水线 — ${phase}</span>
                <span class="pipeline-stats">
                    发现 ${p.jobs_found} 个职位 · 已评分 ${p.jobs_scored}
                    ${p.details_skipped ? ` · 跳过 ${p.details_skipped} 条缓存` : ''}
                    ${p.jobs_skipped ? ` · 跳过 ${p.jobs_skipped} 条已评` : ''}
                </span>
            </div>
            <div class="pipeline-bars">
                <div class="pipeline-bar-row">
                    <span class="pipeline-bar-label">爬取</span>
                    <div class="progress-bar-track flex-1"><div class="progress-bar-fill" style="width: ${scrapePct}%"></div></div>
                    <span class="pipeline-bar-pct">${p.current_page}/${p.total_pages}页</span>
                </div>
                <div class="pipeline-bar-row">
                    <span class="pipeline-bar-label">评分</span>
                    <div class="progress-bar-track flex-1"><div class="progress-bar-fill scoring" style="width: ${scorePct}%"></div></div>
                    <span class="pipeline-bar-pct">${p.jobs_scored}/${p.jobs_found}</span>
                </div>
            </div>
        </div>`;
    }

    function renderAiSuggestionCard() {
        const ss = searchState;
        if (ss.aiSuggestionLoading) {
            return `<div class="ai-suggestion-card loading">
                <div class="ai-suggestion-header"><span class="ai-suggestion-icon">AI</span><span class="ai-suggestion-title">智能推荐搜索条件</span></div>
                <div class="ai-suggestion-body"><div class="ai-loading-dots">正在分析简历...</div></div>
            </div>`;
        }
        if (ss.aiSuggestion && ss.aiSuggestion.ok) {
            const s = ss.aiSuggestion;
            const tags = [];
            if (s.keywords) tags.push({ label: '关键词', value: s.keywords });
            if (s.city) tags.push({ label: '城市', value: s.city });
            const fo = ss.filterOptions;
            if (s.salary && fo) { const label = Object.entries(fo.salary || {}).find(([_, v]) => v === s.salary)?.[0]; if (label) tags.push({ label: '薪资', value: label }); }
            if (s.experience && fo) { const label = Object.entries(fo.experience || {}).find(([_, v]) => v === s.experience)?.[0]; if (label) tags.push({ label: '经验', value: label }); }
            if (s.degree && fo) { const label = Object.entries(fo.degree || {}).find(([_, v]) => v === s.degree)?.[0]; if (label) tags.push({ label: '学历', value: label }); }
            if (s.scale && fo) { const label = Object.entries(fo.scale || {}).find(([_, v]) => v === s.scale)?.[0]; if (label) tags.push({ label: '规模', value: label }); }
            if (s.stage && fo) { const label = Object.entries(fo.stage || {}).find(([_, v]) => v === s.stage)?.[0]; if (label) tags.push({ label: '融资', value: label }); }
            return `<div class="ai-suggestion-card">
                <div class="ai-suggestion-header"><span class="ai-suggestion-icon">AI</span><span class="ai-suggestion-title">智能推荐搜索条件</span></div>
                <div class="ai-suggestion-body">
                    <div class="ai-suggestion-tags">${tags.map(t => `<span class="ai-suggestion-item"><span class="ai-suggestion-label">${t.label}</span><span class="ai-suggestion-value">${t.value}</span></span>`).join('')}</div>
                    ${s.reasoning ? `<div class="ai-suggestion-reasoning">${s.reasoning}</div>` : ''}
                </div>
                <div class="ai-suggestion-actions">
                    <button class="btn btn-secondary btn-sm" id="btn-apply-ai-suggestion">应用推荐</button>
                    <button class="btn btn-primary btn-sm" id="btn-search-ai-suggestion">使用推荐搜索</button>
                </div>
            </div>`;
        }
        if (ss.aiSuggestion && !ss.aiSuggestion.ok) {
            return `<div class="ai-suggestion-card error">
                <div class="ai-suggestion-header"><span class="ai-suggestion-icon">AI</span><span class="ai-suggestion-title">智能推荐</span></div>
                <div class="ai-suggestion-body"><div class="ai-suggestion-error">${ss.aiSuggestion.error || '推断失败'}</div></div>
            </div>`;
        }
        return `<div class="ai-suggestion-card empty">
            <div class="ai-suggestion-header"><span class="ai-suggestion-icon">AI</span><span class="ai-suggestion-title">智能推荐搜索条件</span></div>
            <div class="ai-suggestion-body">
                <p class="ai-suggestion-hint">基于当前简历内容，AI 自动推断最适合的搜索条件</p>
                <button class="btn btn-secondary btn-sm" id="btn-ai-suggest">获取 AI 推荐</button>
            </div>
        </div>`;
    }

    function renderJobCard(job) {
        const skills = job.skills || '';
        const tags = job.tags || '';
        return `<div class="job-card" data-job-id="${job.job_id}">
            <div class="job-card-header"><span class="job-title">${job.title}</span><span class="job-salary">${job.salary || ''}</span></div>
            <div class="job-card-body"><div class="job-tags">${tags}</div><div class="job-location">${job.location || ''}</div></div>
            <div class="job-card-footer">
                <div class="boss-info"><span class="boss-name">${job.boss_name || ''}</span><span class="boss-status ${job.boss_active_status ? 'active' : ''}">${job.boss_active_status || ''}</span></div>
                <div class="company-info"><span>${job.company_scale || ''}</span><span>${job.company_stage || ''}</span><span>${job.company_industry || ''}</span></div>
            </div>
            ${skills ? `<div class="job-card-skills">${skills}</div>` : ''}
        </div>`;
    }

    function renderDetailPopup() {
        const detail = searchState.detailData;
        if (!detail) return `<div class="detail-overlay" id="detail-overlay"><div class="detail-popup loading">加载中...</div></div>`;

        let skillTags = [];
        try { skillTags = JSON.parse(detail.skill_tags || '[]'); } catch(e) {}

        return `<div class="detail-overlay" id="detail-overlay">
            <div class="detail-popup">
                <div class="detail-header"><h3>${detail.title}</h3><span class="detail-salary">${detail.salary || ''}</span><button class="btn-close" id="btn-close-detail">&times;</button></div>
                <div class="detail-meta"><span>${detail.company || ''}</span><span>${detail.location || ''}</span><span class="boss-status ${detail.boss_active_status ? 'active' : ''}">${detail.boss_active_status || ''}</span></div>
                <div class="detail-tags">${(detail.tags_list || '').split(' | ').filter(Boolean).map(t => `<span class="tag">${t}</span>`).join('')}</div>
                ${skillTags.length ? `<div class="detail-skills"><h4>技能要求</h4>${skillTags.map(t => `<span class="skill-tag">${t}</span>`).join('')}</div>` : ''}
                <div class="detail-jd"><h4>职位描述</h4><pre class="jd-text">${detail.jd || '暂无详情'}</pre></div>
            </div>
        </div>`;
    }

    function renderMatchResultCard(r) {
        const scorePercent = Math.round(r.score * 100);
        const scoreClass = r.score >= 0.7 ? 'high' : r.score >= 0.4 ? 'medium' : 'low';
        const isExpanded = matchState.expandedJobId === r.target_job_id;
        const suggestions = Array.isArray(r.suggestions) ? r.suggestions : [];

        return `<div class="match-result-card" data-job-id="${r.target_job_id}">
            <div class="match-result-header" data-toggle-detail="${r.target_job_id}">
                <div class="match-result-info">
                    <span class="match-result-title">${r.title || '未知职位'}</span>
                    <span class="match-result-salary">${r.salary || ''}</span>
                    <span class="match-result-company">${r.company || ''}</span>
                    <span class="match-result-location">${r.location || ''}</span>
                </div>
                <span class="score-badge ${scoreClass}">${scorePercent}%</span>
            </div>
            ${isExpanded ? `<div class="match-result-detail">
                <div class="match-reasoning"><h4>匹配分析</h4><p>${r.reasoning || '暂无分析'}</p></div>
                ${suggestions.length ? `<div class="match-suggestions"><h4>改进建议</h4><ul>${suggestions.map(s => `<li>${s}</li>`).join('')}</ul></div>` : ''}
                ${r.jd ? `<div class="match-jd"><h4>职位描述</h4><pre class="jd-text">${r.jd}</pre></div>` : ''}
                <div class="match-result-actions">
                    <button class="btn btn-primary btn-sm" data-optimize-job="${r.target_job_id}">优化简历</button>
                    <button class="btn btn-accent btn-sm" data-interview-prep="${r.target_job_id}">面试准备</button>
                </div>
            </div>` : ''}
        </div>`;
    }

    function renderStructuredSummaryEl(s) {
        const el = document.createElement('div');
        el.className = 'summary-structured';
        el.id = 'summary-stream';
        let html = '';

        if (s.skill_analysis) {
            const sa = s.skill_analysis;
            html += `<div class="summary-section"><h4>技能分析</h4><div class="skill-grid">`;
            if (sa.matching_skills?.length) html += `<div class="skill-col"><h5>已具备</h5>${sa.matching_skills.map(sk => `<span class="skill-tag matched">${sk}</span>`).join('')}</div>`;
            if (sa.missing_skills?.length) html += `<div class="skill-col"><h5>需提升</h5>${sa.missing_skills.map(sk => `<span class="skill-tag gap">${sk}</span>`).join('')}</div>`;
            html += `</div></div>`;
        }
        if (s.company_analysis) {
            const ca = s.company_analysis;
            html += `<div class="summary-section"><h4>公司分析</h4>`;
            if (ca.tier_distribution) {
                html += `<div class="tier-distribution">`;
                const td = ca.tier_distribution;
                if (td['高匹配'] !== undefined) html += `<div class="tier-item"><div class="tier-count high">${td['高匹配']}</div><div class="tier-label">高匹配</div></div>`;
                if (td['中匹配'] !== undefined) html += `<div class="tier-item"><div class="tier-count medium">${td['中匹配']}</div><div class="tier-label">中匹配</div></div>`;
                if (td['低匹配'] !== undefined) html += `<div class="tier-item"><div class="tier-count low">${td['低匹配']}</div><div class="tier-label">低匹配</div></div>`;
                html += `</div>`;
            }
            if (ca.industry_insights?.length) html += `<ul class="industry-insights">${ca.industry_insights.map(i => `<li>${i}</li>`).join('')}</ul>`;
            html += `</div>`;
        }
        if (s.interview_prep) {
            const ip = s.interview_prep;
            html += `<div class="summary-section"><h4>面试准备</h4>`;
            if (ip.likely_questions?.length) html += `<ol class="interview-questions">${ip.likely_questions.map(q => `<li>${q}</li>`).join('')}</ol>`;
            if (ip.focus_areas?.length) html += `<h5 style="font-size:12px;color:#8b92a0;margin:8px 0 4px">重点方向</h5><ul class="focus-areas">${ip.focus_areas.map(f => `<li>${f}</li>`).join('')}</ul>`;
            html += `</div>`;
        }
        if (s.action_plan?.length) {
            html += `<div class="summary-section"><h4>行动计划</h4><div class="action-plan">`;
            s.action_plan.forEach(a => {
                const pClass = (a.priority === '高' || a.priority === 'high') ? 'high' : (a.priority === '低' || a.priority === 'low') ? 'low' : 'medium';
                html += `<div class="action-item"><span class="priority ${pClass}">${a.priority}</span><span>${a.action}</span><span class="action-timeline">${a.timeline || ''}</span></div>`;
            });
            html += `</div></div>`;
        }
        if (s.overall_strategy) html += `<div class="summary-section"><h4>整体策略</h4><div class="overall-strategy">${s.overall_strategy}</div></div>`;

        el.innerHTML = html;
        return el;
    }

    // ---- Page Renderers ----

    function renderDashboardPage() {
        const ds = dashboardState;
        if (ds.loading) {
            return `<div class="dashboard-page"><div class="dashboard-loading">加载中...</div></div>`;
        }

        const ar = ds.activeResume;
        const resumeCard = ar ? `
            <div class="dashboard-card">
                <div class="dashboard-card-header"><span class="dashboard-card-icon">📄</span><span class="dashboard-card-title">当前简历</span></div>
                <div class="dashboard-card-body">
                    <div class="dashboard-resume-name">${ar.name || '未命名'}</div>
                    <div class="dashboard-resume-info">${ar.chunk_count > 0 ? `已向量化 (${ar.chunk_count}段)` : '未向量化'}</div>
                </div>
                <button class="btn btn-secondary btn-sm" id="btn-go-resume-center">管理简历</button>
            </div>` : `
            <div class="dashboard-card">
                <div class="dashboard-card-header"><span class="dashboard-card-icon">📄</span><span class="dashboard-card-title">当前简历</span></div>
                <div class="dashboard-card-body"><div class="dashboard-resume-name">未设置简历</div></div>
                <button class="btn btn-primary btn-sm" id="btn-go-resume-center">添加简历</button>
            </div>`;

        const statsCards = `
            <div class="dashboard-stats-row">
                <div class="dashboard-stat-card">
                    <div class="dashboard-stat-number">${ds.totalJobs}</div>
                    <div class="dashboard-stat-label">已抓取职位</div>
                </div>
                <div class="dashboard-stat-card">
                    <div class="dashboard-stat-number">${ds.totalMatches}</div>
                    <div class="dashboard-stat-label">已匹配职位</div>
                </div>
                <div class="dashboard-stat-card">
                    <div class="dashboard-stat-number">${ds.avgScore}</div>
                    <div class="dashboard-stat-label">平均匹配分</div>
                </div>
            </div>`;

        const topMatches = ds.recentMatches.length ? `
            <div class="dashboard-card">
                <div class="dashboard-card-header"><span class="dashboard-card-icon">🏆</span><span class="dashboard-card-title">最佳匹配 TOP 5</span></div>
                <div class="dashboard-card-body">
                    ${ds.recentMatches.map(m => `
                        <div class="dashboard-match-item">
                            <span class="dashboard-match-title">${m.title || '未知'}</span>
                            <span class="dashboard-match-company">${m.company_industry || ''}</span>
                            <span class="score-badge ${m.score >= 0.7 ? 'high' : m.score >= 0.4 ? 'medium' : 'low'}">${Math.round(m.score * 100)}%</span>
                        </div>
                    `).join('')}
                </div>
            </div>` : '';

        const recentOpts = ds.recentOptimizations.length ? `
            <div class="dashboard-card">
                <div class="dashboard-card-header"><span class="dashboard-card-icon">✨</span><span class="dashboard-card-title">最近优化</span></div>
                <div class="dashboard-card-body">
                    ${ds.recentOptimizations.map(o => `
                        <div class="dashboard-opt-item">
                            <span class="dashboard-opt-title">${o.job_title || o.job_id}</span>
                            <span class="dashboard-opt-company">${o.company_name || ''}</span>
                            <span class="optimization-status-badge ${o.status}">${o.status === 'completed' ? '已完成' : o.status === 'generating' ? '生成中' : o.status === 'failed' ? '失败' : '待处理'}</span>
                        </div>
                    `).join('')}
                </div>
            </div>` : '';

        const chromeCard = `
            <div class="dashboard-card">
                <div class="dashboard-card-header"><span class="dashboard-card-icon">🌐</span><span class="dashboard-card-title">Chrome 状态</span></div>
                <div class="dashboard-card-body">
                    ${renderChromeStatusCard()}
                </div>
            </div>`;

        const embedderCard = `
            <div class="dashboard-card">
                ${renderEmbedderCard()}
            </div>`;

        const quickActions = `
            <div class="dashboard-card">
                <div class="dashboard-card-header"><span class="dashboard-card-icon">⚡</span><span class="dashboard-card-title">快速入口</span></div>
                <div class="dashboard-quick-actions">
                    <button class="btn btn-accent" id="btn-quick-pipeline">一体化搜索+匹配</button>
                    <button class="btn btn-primary" id="btn-quick-search">搜索职位</button>
                    <button class="btn btn-secondary" id="btn-quick-matches">查看匹配结果</button>
                </div>
            </div>`;

        return `<div class="dashboard-page">
            <h2>工作台</h2>
            <p class="page-desc">全局概览与快速入口</p>
            ${statsCards}
            <div class="dashboard-row">
                ${resumeCard}
                ${chromeCard}
                ${embedderCard}
            </div>
            <div class="dashboard-row">
                ${quickActions}
            </div>
            <div class="dashboard-row">
                ${topMatches}
                ${recentOpts}
            </div>
        </div>`;
    }

    function renderResumeCenterPage() {
        const rps = resumePageState;
        const isEditing = rps.editingId !== null;
        const resumeCards = rps.resumes.map(r => {
            const isActive = r.is_active ? 'active' : '';
            const chunkInfo = r.chunk_count > 0 ? `已向量化 (${r.chunk_count}段)` : '未向量化';
            const summary = r.summary ? (r.summary.length > 80 ? r.summary.slice(0, 80) + '...' : r.summary) : '空简历';
            return `
            <div class="resume-card ${isActive}" data-resume-id="${r.id}">
                <div class="resume-card-header"><span class="resume-card-name">${r.name || '未命名'}</span>${r.is_active ? '<span class="resume-active-badge">当前</span>' : ''}</div>
                <div class="resume-card-summary">${summary}</div>
                <div class="resume-card-meta"><span class="resume-chunk-status">${chunkInfo}</span><span class="resume-updated">${r.updated_at || ''}</span></div>
                <div class="resume-card-actions">
                    <button class="btn btn-secondary btn-xs" data-edit-resume="${r.id}">编辑</button>
                    ${!r.is_active ? `<button class="btn btn-secondary btn-xs" data-set-active="${r.id}">设为当前</button>` : ''}
                    <button class="btn btn-danger btn-xs" data-delete-resume="${r.id}">删除</button>
                </div>
            </div>`;
        }).join('');

        // Optimization section
        const activeResume = rps.resumes.find(r => r.is_active);
        const opts = optimizationState.optimizations;
        const optList = opts.length ? `
            <div class="optimization-list">
                ${opts.map(o => `
                    <div class="optimization-item" data-optimization-id="${o.id}">
                        <div class="optimization-item-info">
                            <span class="optimization-item-title">${o.job_title || o.job_id}</span>
                            <span class="optimization-item-company">${o.company_name || ''}</span>
                            <span class="optimization-status-badge ${o.status}">${o.status === 'completed' ? '已完成' : o.status === 'generating' ? '生成中' : o.status === 'failed' ? '失败' : '待处理'}</span>
                        </div>
                        <div class="optimization-item-meta">${o.created_at || ''}</div>
                        <div class="optimization-item-actions">
                            ${o.status === 'completed' ? `<button class="btn btn-primary btn-xs" data-view-optimization="${o.id}">查看优化</button>` : ''}
                            <button class="btn btn-danger btn-xs" data-delete-optimization="${o.id}">删除</button>
                        </div>
                    </div>
                `).join('')}
            </div>` : '<div class="optimization-empty">暂无优化记录，请先匹配职位后再优化简历</div>';

        // Active optimization detail with diff view
        let optDetailView = '';
        if (optimizationState.activeOptimization) {
            const opt = optimizationState.activeOptimization;
            const sections = opt.diff_sections || [];
            optDetailView = `
            <div class="optimization-detail">
                <div class="optimization-detail-header">
                    <h3>优化结果 — ${opt.job_title || opt.job_id}</h3>
                    <button class="btn-close" id="btn-close-optimization">&times;</button>
                </div>
                <div class="optimization-detail-meta">${opt.company_name || ''} · ${opt.model_name || ''}</div>
                <div class="diff-sections">
                    ${sections.map((sec, idx) => {
                        if (sec.action === 'keep') {
                            return `<div class="diff-section diff-keep">
                                <div class="diff-section-header"><span class="diff-section-name">${sec.section}</span><span class="diff-action-badge keep">保持</span></div>
                                <div class="diff-content">${sec.original || ''}</div>
                            </div>`;
                        } else if (sec.action === 'rewrite') {
                            return `<div class="diff-section diff-rewrite">
                                <div class="diff-section-header"><span class="diff-section-name">${sec.section}</span><span class="diff-action-badge rewrite">改写</span></div>
                                <div class="diff-original"><del>${sec.original || ''}</del></div>
                                <div class="diff-revised"><ins>${sec.revised || ''}</ins></div>
                                <div class="diff-reason">${sec.reason || ''}</div>
                                <button class="btn btn-secondary btn-xs diff-revert-btn" data-revert-section="${idx}">回退此段</button>
                            </div>`;
                        } else if (sec.action === 'add') {
                            return `<div class="diff-section diff-add">
                                <div class="diff-section-header"><span class="diff-section-name">${sec.section}</span><span class="diff-action-badge add">新增</span></div>
                                <div class="diff-revised"><ins>${sec.revised || ''}</ins></div>
                                <div class="diff-reason">${sec.reason || ''}</div>
                                <button class="btn btn-secondary btn-xs diff-revert-btn" data-revert-section="${idx}">移除此段</button>
                            </div>`;
                        } else if (sec.action === 'reverted') {
                            return `<div class="diff-section diff-reverted">
                                <div class="diff-section-header"><span class="diff-section-name">${sec.section}</span><span class="diff-action-badge reverted">已回退</span></div>
                                <div class="diff-content">${sec.original || sec.revised || ''}</div>
                            </div>`;
                        }
                        return '';
                    }).join('')}
                </div>
                ${opt.overall_tips ? `<div class="optimization-tips"><h4>整体建议</h4><ul>${(Array.isArray(opt.overall_tips) ? opt.overall_tips : []).map(t => `<li>${t}</li>`).join('')}</ul></div>` : ''}
            </div>`;
        }

        // Optimization generating progress
        let optProgress = '';
        if (optimizationState.generating) {
            optProgress = `<div class="optimization-progress-card">
                <div class="optimization-progress-label">AI 正在优化简历...</div>
                <div class="progress-bar-track"><div class="progress-bar-fill" style="width: 50%"></div></div>
            </div>`;
        }

        return `
        <div class="resume-center-page">
            <h2>简历中心</h2>
            <div class="resume-card-grid">
                ${resumeCards}
                <div class="resume-card resume-card-add" id="btn-add-resume">
                    <div class="resume-card-add-icon">+</div>
                    <div class="resume-card-add-text">上传简历</div>
                    <input type="file" id="resume-file-input" accept=".pdf,.docx,.doc,.txt,.md" style="display:none">
                </div>
            </div>
            ${isEditing ? `
            <div class="resume-editor-section">
                <h3>编辑简历</h3>
                <div id="vditor" class="resume-editor"></div>
                <div class="resume-editor-actions">
                    <button class="btn btn-primary btn-sm" id="btn-save-resume-edit" ${!rps.contentDirty ? 'disabled' : ''}>保存</button>
                    <button class="btn btn-secondary btn-sm" id="btn-cancel-resume-edit">取消</button>
                </div>
                <div class="supplement-section">
                    <h4>补充材料（可选）</h4>
                    <div class="supplement-upload-area">
                        <button class="btn btn-secondary btn-sm" id="btn-upload-supplement">上传补充材料</button>
                        <input type="file" id="supplement-file-input" accept=".pdf,.docx,.txt,.md" style="display:none">
                    </div>
                    <div class="supplement-list" id="supplement-list">
                        ${rps.supplements.map((s, i) => `<div class="supplement-item"><span>${s.filename}</span><button class="btn-remove" data-remove-supplement="${i}">&times;</button></div>`).join('')}
                    </div>
                </div>
            </div>` : ''}
            <div class="optimization-section">
                <h3>简历优化</h3>
                ${optProgress}
                ${optDetailView}
                ${!optimizationState.activeOptimization && !optimizationState.generating ? optList : ''}
            </div>
        </div>`;
    }

    function renderJobCenterPage() {
        const progress = searchState.scrapeProgress;
        const isScraping = progress && progress.status === 'running';
        const hasResults = searchState.results.length > 0;

        // Search form
        const searchForm = `
        <div class="search-form-card">
            <h2>职位搜索</h2>
            <div class="search-form">
                <div class="form-row">
                    <div class="form-group flex-1"><label>关键词 *</label><input type="text" id="search-keyword" value="${searchState.keyword}" placeholder="如: AI Agent, Java, 产品经理"></div>
                    <div class="form-group city-group"><label>城市</label><input type="text" id="search-city" value="${searchState.city}" placeholder="如: 上海, 北京" autocomplete="off"><div class="city-dropdown" id="city-dropdown"></div></div>
                    <div class="form-group pages-group"><label>页数</label><select id="search-pages">${[1,2,3,5,10].map(n => `<option value="${n}" ${n===searchState.maxPages?'selected':''}>${n}页</option>`).join('')}</select></div>
                </div>
                <div class="filter-row">${renderFilterDropdowns()}</div>
                <div class="search-actions">
                    <button class="btn btn-primary" id="btn-start-search" ${isScraping || searchState.pipelineRunning ? 'disabled' : ''}>${isScraping ? '抓取中...' : '开始搜索'}</button>
                    <button class="btn btn-accent" id="btn-start-pipeline" ${isScraping || searchState.pipelineRunning ? 'disabled' : ''}>${searchState.pipelineRunning ? '一体化进行中...' : '一体化搜索+匹配'}</button>
                    ${isScraping ? `<button class="btn btn-danger" id="btn-cancel-scrape">取消抓取</button>` : ''}
                    ${searchState.pipelineRunning ? `<button class="btn btn-danger" id="btn-cancel-pipeline">取消流水线</button>` : ''}
                </div>
                <div class="pipeline-options">
                    <div class="pipeline-option-item"><label>最低匹配分</label><div class="score-slider-wrap"><input type="range" id="pipeline-min-score" min="0" max="100" step="5" value="${Math.round(searchState.pipelineMinScore * 100)}" ${searchState.pipelineRunning ? 'disabled' : ''}><span class="score-slider-value">${Math.round(searchState.pipelineMinScore * 100)}%</span></div></div>
                </div>
            </div>
        </div>`;

        // AI suggestion
        const aiCard = renderAiSuggestionCard();

        // Pipeline progress
        const pipelineProg = searchState.pipelineRunning ? renderPipelineProgress() : '';

        // Scrape progress
        const scrapeProg = isScraping ? renderProgressIndicator(progress) : '';

        // Search results
        const searchResults = hasResults ? (() => {
            const jobs = searchState.results;
            const total = searchState.resultTotal;
            const offset = searchState.resultOffset;
            const limit = searchState.resultLimit;
            const hasMore = offset + limit < total;
            const hasPrev = offset > 0;
            return `<div class="results-area">
                <div class="results-header"><span class="results-count">共 ${total} 个职位${isScraping ? ' · 实时更新中...' : ''}</span></div>
                <div class="job-cards">${jobs.map(job => renderJobCard(job)).join('')}</div>
                ${!isScraping ? `<div class="pagination">
                    ${hasPrev ? `<button class="btn btn-secondary" id="btn-prev-page">上一页</button>` : ''}
                    <span class="page-info">${Math.floor(offset/limit)+1} / ${Math.ceil(total/limit) || 1}</span>
                    ${hasMore ? `<button class="btn btn-secondary" id="btn-next-page">下一页</button>` : ''}
                </div>` : ''}
            </div>`;
        })() : '';

        // Match results
        const matchResults = matchState.results.length > 0 ? `
        <div class="match-results-area">
            <div class="results-header"><span class="results-count">匹配结果: ${matchState.resultTotal} 个职位</span></div>
            <div class="match-result-cards" id="match-result-cards">
                ${matchState.results.map(r => renderMatchResultCard(r)).join('')}
            </div>
        </div>` : '';

        // Summary
        let summarySection = '';
        if (summaryState.summary) {
            const s = summaryState.summary;
            const structured = s.structured;
            if (structured) {
                const el = renderStructuredSummaryEl(structured);
                summarySection = `<div class="job-center-section"><h3>综合摘要</h3>${el.outerHTML}</div>`;
            } else if (s.raw_text) {
                summarySection = `<div class="job-center-section"><h3>综合摘要</h3><div class="summary-stream">${s.raw_text}</div></div>`;
            }
        }

        // Interview prep section
        let interviewPrepSection = '';
        if (interviewPrepState.generating) {
            interviewPrepSection = `<div class="job-center-section interview-prep-section">
                <h3>面试准备</h3>
                <div class="interview-prep-progress-card">
                    <div class="interview-prep-progress-label">AI 正在生成面试题...</div>
                    <div class="progress-bar-track"><div class="progress-bar-fill" style="width: 50%"></div></div>
                </div>
            </div>`;
        } else if (interviewPrepState.activePrep) {
            const prep = interviewPrepState.activePrep;
            // Parse questions: stored as {items: [...], overall_strategy: {...}} or legacy [...]
            let questions = [];
            let strategy = {};
            const rawQuestions = prep.questions;
            if (Array.isArray(rawQuestions)) {
                questions = rawQuestions;
            } else if (rawQuestions && typeof rawQuestions === 'object') {
                questions = rawQuestions.items || [];
                strategy = rawQuestions.overall_strategy || {};
            }
            const expandedId = interviewPrepState.expandedQuestionId;

            interviewPrepSection = `<div class="job-center-section interview-prep-section">
                <div class="interview-prep-header">
                    <h3>面试准备 — ${prep.job_title || prep.job_id}</h3>
                    <div class="interview-prep-meta">${prep.company_name || ''} · ${prep.model_name || ''}</div>
                    <button class="btn-close" id="btn-close-interview-prep">&times;</button>
                </div>
                ${strategy.strengths_to_highlight?.length ? `<div class="interview-prep-strategy">
                    <div class="strategy-block"><h4>突出优势</h4><ul>${strategy.strengths_to_highlight.map(s => `<li>${s}</li>`).join('')}</ul></div>
                    ${strategy.weaknesses_to_prepare?.length ? `<div class="strategy-block"><h4>薄弱环节</h4><ul>${strategy.weaknesses_to_prepare.map(s => `<li>${s}</li>`).join('')}</ul></div>` : ''}
                    ${strategy.interview_tips?.length ? `<div class="strategy-block"><h4>面试策略</h4><ul>${strategy.interview_tips.map(s => `<li>${s}</li>`).join('')}</ul></div>` : ''}
                </div>` : ''}
                <div class="interview-questions-list">
                    ${questions.map((q, idx) => {
                        const isExpanded = expandedId === idx;
                        const diffClass = q.difficulty === '困难' ? 'hard' : q.difficulty === '中等' ? 'medium' : 'easy';
                        const catIcons = {'技术深度': '🔬', '项目经验': '💼', '行为面试': '🤝', '系统设计': '🏗️', '基础知识': '📚', '行业认知': '🌐'};
                        return `<div class="interview-question-card ${isExpanded ? 'expanded' : ''}" data-question-idx="${idx}">
                            <div class="interview-question-header" data-toggle-question="${idx}">
                                <div class="interview-question-left">
                                    <span class="question-number">Q${idx + 1}</span>
                                    <span class="question-category-icon">${catIcons[q.category] || '❓'}</span>
                                    <span class="question-category">${q.category || ''}</span>
                                    <span class="question-difficulty ${diffClass}">${q.difficulty || ''}</span>
                                </div>
                                <span class="question-expand-icon">${isExpanded ? '▼' : '▶'}</span>
                            </div>
                            <div class="interview-question-text">${q.question || ''}</div>
                            ${q.context ? `<div class="interview-question-context">💡 ${q.context}</div>` : ''}
                            ${isExpanded ? `<div class="interview-question-detail">
                                ${q.answer_framework ? `<div class="answer-framework">
                                    <h5>答题框架 <span class="framework-structure">(${q.answer_framework.structure || ''})</span></h5>
                                    <div class="sample-opening">"${q.answer_framework.sample_opening || ''}"</div>
                                    <ul class="key-points">${(q.answer_framework.key_points || []).map(p => `<li>${p}</li>`).join('')}</ul>
                                </div>` : ''}
                                ${q.knowledge_supplements?.length ? `<div class="knowledge-supplements">
                                    <h5>知识点补充</h5>
                                    ${q.knowledge_supplements.map(ks => `<div class="knowledge-supplement-item">
                                        <div class="ks-topic">${ks.topic || ''}</div>
                                        <div class="ks-concepts">${(ks.key_concepts || []).map(c => `<span class="ks-concept-tag">${c}</span>`).join('')}</div>
                                        <div class="ks-direction">📖 ${ks.study_direction || ''}</div>
                                    </div>`).join('')}
                                </div>` : ''}
                            </div>` : ''}
                        </div>`;
                    }).join('')}
                </div>
            </div>`;
        }

        return `<div class="job-center-page">
            <h2>职位中心</h2>
            <p class="page-desc">搜索、匹配、分析一体化</p>
            ${searchForm}
            ${aiCard}
            ${pipelineProg}
            ${scrapeProg}
            ${searchResults}
            <div class="job-center-section"><h3>匹配结果</h3>${matchResults}</div>
            ${interviewPrepSection}
            ${summarySection}
        </div>
        ${searchState.selectedJob ? renderDetailPopup() : ''}`;
    }

    function renderSettingsPage() {
        return `
        <div class="page">
            <h2>设置</h2>
            <p class="page-desc">配置应用参数</p>

            <div class="settings-section">
                <h3>Chrome 管理</h3>
                ${renderEmbedderCard()}
                ${renderChromeStatusCard()}
            </div>

            <div class="settings-section">
                <h3>API 配置</h3>
                <div class="settings-form" id="settings-form">
                    <div class="form-group"><label>API Base URL</label><input type="text" id="setting-api-url" placeholder="https://api.openai.com/v1"></div>
                    <div class="form-group"><label>API Key</label><input type="password" id="setting-api-key" placeholder="sk-..."></div>
                    <div class="form-group"><label>Model</label><input type="text" id="setting-api-model" placeholder="gpt-4o"></div>
                    <button class="btn btn-primary" id="btn-save-settings">保存设置</button>
                </div>
            </div>
        </div>`;
    }

    // ---- Page Renderers Mapping ----

    const pageRenderers = {
        dashboard: renderDashboardPage,
        'resume-center': renderResumeCenterPage,
        'job-center': renderJobCenterPage,
        settings: renderSettingsPage,
    };

    // ---- Rendering ----

    function renderPage() {
        const content = document.getElementById('content');
        const scrollTop = content.scrollTop;

        if (resumePageState.vditor) {
            try { resumePageState.vditor.destroy(); } catch(e) {}
            resumePageState.vditor = null;
        }

        const renderer = pageRenderers[currentPage];
        if (renderer) {
            content.innerHTML = renderer();
            bindPageEvents();
        }

        if (currentPage === 'resume-center' && resumePageState.editingId !== null) {
            initResumeVditor();
        }

        content.scrollTop = scrollTop;
    }

    async function refreshChromeStatus() {
        const result = await api.autoDetectLogin(currentIdentity);
        if (result && result.ok) {
            chromeStatus = result.data;
        }
    }

    async function refreshState() {
        const state = await api.getAppState();
        if (state && state.identity) {
            currentIdentity = state.identity;
            document.querySelectorAll('.identity-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.mode === state.identity);
            });
        }
        await refreshChromeStatus();
        if (currentPage === 'dashboard') {
            renderPage();
        }
    }

    // ---- Page Init Functions ----

    async function initDashboardPage() {
        dashboardState.loading = true;
        renderPage();
        const result = await api.getDashboardData();
        if (result?.ok) {
            const d = result.data;
            dashboardState.activeResume = d.active_resume;
            dashboardState.totalJobs = d.total_jobs || 0;
            dashboardState.totalMatches = d.total_matches || 0;
            dashboardState.avgScore = d.avg_score || 0;
            dashboardState.recentMatches = d.top_matches || [];
            dashboardState.recentOptimizations = d.recent_optimizations || [];
        }
        dashboardState.loading = false;
        renderPage();
    }

    async function initResumeCenterPage() {
        const result = await api.listResumes();
        if (result?.ok) {
            resumePageState.resumes = result.data.resumes || [];
        }
        // Load optimizations for active resume
        const active = resumePageState.resumes.find(r => r.is_active);
        if (active) {
            const optResult = await api.listOptimizations(active.id);
            if (optResult?.ok) {
                optimizationState.optimizations = optResult.data.optimizations || [];
            }
        }
        renderPage();
    }

    async function initJobCenterPage() {
        if (!searchState.filterOptions) {
            const result = await api.getFilterOptions();
            if (result?.ok) searchState.filterOptions = result.data;
        }
        const progress = await api.getScrapeProgress();
        if (progress?.ok && progress.data.status === 'running') {
            searchState.scrapeProgress = progress.data;
            startProgressPolling();
        }
        const matchProgress = await api.getMatchProgress();
        if (matchProgress?.ok && matchProgress.data.status === 'running') {
            matchState.matchProgress = matchProgress.data;
            startMatchPolling();
        }
        await loadMatchResults();
        // Load summary
        const summaryResult = await api.getMatchSummary();
        if (summaryResult?.ok && summaryResult.data?.summary) {
            summaryState.summary = summaryResult.data.summary;
        }
        renderPage();
    }

    async function initSettingsPage() {
        const result = await api.getSettings();
        if (result?.ok) {
            const d = result.data;
            const urlInput = document.getElementById('setting-api-url');
            const keyInput = document.getElementById('setting-api-key');
            const modelInput = document.getElementById('setting-api-model');
            if (urlInput) urlInput.value = d.api_base_url || '';
            if (keyInput) keyInput.value = d.api_key || '';
            if (modelInput) modelInput.value = d.api_model || '';
        }
    }

    // ---- Search Helpers ----

    function startProgressPolling() {
        stopProgressPolling();
        searchState.pollTimer = setInterval(async () => {
            const result = await api.getScrapeProgress();
            if (result?.ok && result.data.status !== 'idle') {
                searchState.scrapeProgress = result.data;
                if (result.data.status === 'running') await loadResults();
                renderPage();
                if (result.data.status !== 'running') {
                    stopProgressPolling();
                    if (result.data.status === 'completed') await loadResults();
                }
            }
        }, 2000);
    }

    function stopProgressPolling() {
        if (searchState.pollTimer) { clearInterval(searchState.pollTimer); searchState.pollTimer = null; }
    }

    async function loadResults() {
        const result = await api.getScrapedJobs(searchState.keyword, searchState.city, searchState.resultOffset, searchState.resultLimit);
        if (result?.ok) { searchState.results = result.data.jobs; searchState.resultTotal = result.data.total; renderPage(); }
    }

    function applyAiSuggestion() {
        const s = searchState.aiSuggestion;
        if (!s || !s.ok) return;
        const fo = searchState.filterOptions;
        if (s.keywords) { const el = document.getElementById('search-keyword'); if (el) el.value = s.keywords; searchState.keyword = s.keywords; }
        if (s.city) { const el = document.getElementById('search-city'); if (el) el.value = s.city; searchState.city = s.city; }
        if (s.salary && fo) { const label = Object.entries(fo.salary || {}).find(([_, v]) => v === s.salary)?.[0]; const sel = document.getElementById('filter-salary'); if (sel && label) { sel.value = label; searchState.filters.salary = label; } }
        if (s.experience && fo) { const label = Object.entries(fo.experience || {}).find(([_, v]) => v === s.experience)?.[0]; const sel = document.getElementById('filter-experience'); if (sel && label) { sel.value = label; searchState.filters.experience = label; } }
        if (s.degree && fo) { const label = Object.entries(fo.degree || {}).find(([_, v]) => v === s.degree)?.[0]; const sel = document.getElementById('filter-degree'); if (sel && label) { sel.value = label; searchState.filters.degree = label; } }
        if (s.scale && fo) { const label = Object.entries(fo.scale || {}).find(([_, v]) => v === s.scale)?.[0]; const sel = document.getElementById('filter-scale'); if (sel && label) { sel.value = label; searchState.filters.scale = label; } }
        if (s.stage && fo) { const label = Object.entries(fo.stage || {}).find(([_, v]) => v === s.stage)?.[0]; const sel = document.getElementById('filter-stage'); if (sel && label) { sel.value = label; searchState.filters.stage = label; } }
    }

    // ---- Match Helpers ----

    function startMatchPolling() {
        stopMatchPolling();
        matchState.pollTimer = setInterval(async () => {
            const result = await api.getMatchProgress();
            if (result?.ok && result.data.status !== 'idle') {
                matchState.matchProgress = result.data;
                if (result.data.status === 'running') {
                    if (result.data.phase) {
                        matchState.phase = result.data.phase;
                        matchState.phaseProgress = { current: result.data.completed || 0, total: result.data.total_jobs || 0 };
                    }
                } else {
                    stopMatchPolling();
                    if (result.data.status === 'completed') { matchState.phase = 'completed'; await loadMatchResults(); }
                    else if (result.data.status === 'cancelled') matchState.phase = 'cancelled';
                    else if (result.data.status === 'failed') matchState.phase = 'failed';
                    renderPage();
                }
            }
        }, 3000);
    }

    function stopMatchPolling() {
        if (matchState.pollTimer) { clearInterval(matchState.pollTimer); matchState.pollTimer = null; }
    }

    async function loadMatchResults() {
        const result = await api.getMatchResults(1, matchState.resultLimit, matchState.resultOffset);
        if (result?.ok) { matchState.results = result.data.results; matchState.resultTotal = result.data.total; }
    }

    // ---- Optimization Helpers ----

    function startOptimizationPolling() {
        stopOptimizationPolling();
        optimizationState.pollTimer = setInterval(async () => {
            const result = await api.getOptimizationProgress();
            if (result?.ok && result.data.status !== 'idle') {
                optimizationState.progress = result.data;
                if (result.data.status !== 'generating') {
                    stopOptimizationPolling();
                    optimizationState.generating = false;
                    if (result.data.status === 'completed') {
                        // Reload the optimization detail
                        const optResult = await api.getOptimization(result.data.optimization_id);
                        if (optResult?.ok) optimizationState.activeOptimization = optResult.data;
                    }
                    renderPage();
                }
            }
        }, 2000);
    }

    function stopOptimizationPolling() {
        if (optimizationState.pollTimer) { clearInterval(optimizationState.pollTimer); optimizationState.pollTimer = null; }
    }

    // ---- Interview Prep Helpers ----

    function startInterviewPrepPolling() {
        stopInterviewPrepPolling();
        interviewPrepState.pollTimer = setInterval(async () => {
            const result = await api.getInterviewPrepProgress();
            if (result?.ok && result.data.status !== 'idle') {
                interviewPrepState.progress = result.data;
                if (result.data.status !== 'generating') {
                    stopInterviewPrepPolling();
                    interviewPrepState.generating = false;
                    if (result.data.status === 'completed') {
                        const prepResult = await api.getInterviewPrep(result.data.prep_id);
                        if (prepResult?.ok) {
                            interviewPrepState.activePrep = prepResult.data;
                        }
                    }
                    renderPage();
                }
            }
        }, 2000);
    }

    function stopInterviewPrepPolling() {
        if (interviewPrepState.pollTimer) { clearInterval(interviewPrepState.pollTimer); interviewPrepState.pollTimer = null; }
    }

    // ---- Vditor Init ----

    function initResumeVditor() {
        if (resumePageState.vditor) { try { resumePageState.vditor.destroy(); } catch(e) {} resumePageState.vditor = null; }
        const el = document.getElementById('vditor');
        if (!el) return;
        resumePageState.vditor = new Vditor('vditor', {
            theme: 'dark', mode: 'ir', lang: 'zh_CN', height: 300,
            placeholder: '粘贴简历内容，或上传 PDF/DOCX 文件...',
            preview: { theme: { current: 'dark' } },
            cache: { enable: false },
            toolbar: ['headings','bold','italic','strike','|','list','ordered-list','check','|','quote','code','inline-code','|','link','table','|','undo','redo','|','fullscreen','preview'],
            after: () => {
                if (resumePageState.content) {
                    resumePageState._silenceInput = true;
                    resumePageState.vditor.setValue(resumePageState.content);
                    resumePageState._silenceInput = false;
                    resumePageState.contentDirty = false;
                }
            },
            input: () => {
                if (resumePageState._silenceInput) return;
                resumePageState.content = resumePageState.vditor.getValue();
                resumePageState.contentDirty = true;
                const saveBtn = document.getElementById('btn-save-resume-edit');
                if (saveBtn) saveBtn.disabled = false;
            },
        });
    }

    // ---- Pipeline Helpers ----

    function startPipelinePolling() {
        stopPipelinePolling();
        searchState.pipelinePollTimer = setInterval(async () => {
            const result = await api.getPipelineProgress();
            if (result?.ok && result.data.status !== 'idle') {
                searchState.pipelineProgress = result.data;
                if (result.data.status === 'running') { renderPage(); }
                else {
                    stopPipelinePolling();
                    searchState.pipelineRunning = false;
                    if (result.data.status === 'completed') await loadResults();
                    renderPage();
                }
            }
        }, 2000);
    }

    function stopPipelinePolling() {
        if (searchState.pipelinePollTimer) { clearInterval(searchState.pipelinePollTimer); searchState.pipelinePollTimer = null; }
    }

    // ---- Event Binding ----

    function bindPageEvents() {
        // ---- Dashboard Events ----
        document.getElementById('btn-go-resume-center')?.addEventListener('click', () => navigateTo('resume-center'));
        document.getElementById('btn-quick-pipeline')?.addEventListener('click', () => navigateTo('job-center'));
        document.getElementById('btn-quick-search')?.addEventListener('click', () => navigateTo('job-center'));
        document.getElementById('btn-quick-matches')?.addEventListener('click', () => navigateTo('job-center'));

        // Chrome events (dashboard + settings)
        document.getElementById('btn-start-chrome')?.addEventListener('click', async () => {
            const btn = document.getElementById('btn-start-chrome');
            btn.disabled = true; btn.textContent = '启动中...';
            const result = await api.setupChrome(currentIdentity);
            if (result?.ok) { await refreshChromeStatus(); renderPage(); }
            else { btn.disabled = false; btn.textContent = '启动 Chrome'; alert('启动失败: ' + (result?.error || '未知错误')); }
        });

        document.getElementById('btn-check-login')?.addEventListener('click', async () => {
            const btn = document.getElementById('btn-check-login');
            btn.disabled = true; btn.textContent = '检测中...';
            const result = await api.checkLogin(currentIdentity);
            if (result?.ok && result.data.logged_in) { await refreshChromeStatus(); renderPage(); }
            else { btn.disabled = false; btn.textContent = '检测登录状态'; alert('登录状态: ' + (result?.data?.message || '未检测到登录态')); }
        });

        document.getElementById('btn-stop-chrome')?.addEventListener('click', async () => {
            if (!confirm('确定要关闭 Chrome 吗？')) return;
            const result = await api.stopChrome(currentIdentity);
            if (result?.ok) { await refreshChromeStatus(); renderPage(); }
        });

        document.getElementById('btn-retry-embedder')?.addEventListener('click', () => {
            embedderState.status = 'idle'; embedderState.error = '';
            api.initEmbedder(); embedderState.status = 'downloading'; renderPage();
        });

        // ---- Job Center Events ----
        document.getElementById('btn-start-search')?.addEventListener('click', async () => {
            const keyword = document.getElementById('search-keyword')?.value?.trim();
            if (!keyword) { alert('请输入搜索关键词'); return; }
            const city = document.getElementById('search-city')?.value?.trim() || '上海';
            const maxPages = parseInt(document.getElementById('search-pages')?.value || '3');
            const filters = {};
            document.querySelectorAll('[data-filter]').forEach(sel => { const key = sel.dataset.filter; const value = sel.value; if (value) filters[key] = value; });
            searchState.keyword = keyword; searchState.city = city; searchState.maxPages = maxPages; searchState.filters = filters; searchState.resultOffset = 0;
            const result = await api.searchJobs(keyword, city, maxPages, filters);
            if (result?.ok) { startProgressPolling(); renderPage(); }
            else alert('搜索失败: ' + (result?.error || '未知错误'));
        });

        document.getElementById('btn-cancel-scrape')?.addEventListener('click', async () => { await api.cancelScrape(); stopProgressPolling(); });

        document.getElementById('btn-start-pipeline')?.addEventListener('click', async () => {
            const keyword = document.getElementById('search-keyword')?.value?.trim();
            if (!keyword) { alert('请输入搜索关键词'); return; }
            const city = document.getElementById('search-city')?.value?.trim() || '上海';
            const maxPages = parseInt(document.getElementById('search-pages')?.value || '3');
            const minScore = searchState.pipelineMinScore;
            const filters = {};
            document.querySelectorAll('[data-filter]').forEach(sel => { const key = sel.dataset.filter; const value = sel.value; if (value) filters[key] = value; });
            searchState.keyword = keyword; searchState.city = city; searchState.maxPages = maxPages; searchState.filters = filters; searchState.resultOffset = 0;
            searchState.pipelineRunning = true;
            searchState.pipelineProgress = { phase: 'init', jobs_found: 0, jobs_scored: 0, current_page: 0, total_pages: maxPages, details_scraped: 0, details_skipped: 0, jobs_skipped: 0 };
            const supplements = resumePageState.supplements.map(s => s.content);
            const result = await api.startPipeline(null, keyword, city, maxPages, filters, minScore, supplements);
            if (result?.ok) { startPipelinePolling(); renderPage(); }
            else { searchState.pipelineRunning = false; searchState.pipelineProgress = null; alert('流水线启动失败: ' + (result?.error || '未知错误')); renderPage(); }
        });

        document.getElementById('btn-cancel-pipeline')?.addEventListener('click', async () => {
            await api.cancelPipeline(); stopPipelinePolling(); searchState.pipelineRunning = false; searchState.pipelineProgress = null; renderPage();
        });

        const scoreSlider = document.getElementById('pipeline-min-score');
        if (scoreSlider) {
            scoreSlider.addEventListener('input', (e) => {
                searchState.pipelineMinScore = parseInt(e.target.value) / 100;
                const label = e.target.parentElement.querySelector('.score-slider-value');
                if (label) label.textContent = e.target.value + '%';
            });
        }

        // AI suggestion
        document.getElementById('btn-ai-suggest')?.addEventListener('click', async () => {
            searchState.aiSuggestionLoading = true; searchState.aiSuggestion = null; renderPage();
            searchState.aiSuggestionLoading = false; searchState.aiSuggestion = await api.inferSearchConditions(); renderPage();
        });
        document.getElementById('btn-apply-ai-suggestion')?.addEventListener('click', () => applyAiSuggestion());
        document.getElementById('btn-search-ai-suggestion')?.addEventListener('click', () => { applyAiSuggestion(); setTimeout(() => document.getElementById('btn-start-search')?.click(), 100); });

        // City autocomplete
        const cityInput = document.getElementById('search-city');
        if (cityInput) {
            cityInput.addEventListener('input', debounce(async (e) => {
                const keyword = e.target.value.trim();
                if (keyword.length < 1) { document.getElementById('city-dropdown').style.display = 'none'; return; }
                const result = await api.listCities(keyword);
                if (result?.ok) {
                    const dropdown = document.getElementById('city-dropdown');
                    dropdown.innerHTML = result.data.cities.slice(0, 10).map(c => `<div class="city-option" data-name="${c.name}" data-code="${c.code}">${c.name}</div>`).join('');
                    dropdown.style.display = result.data.cities.length ? 'block' : 'none';
                }
            }, 300));
        }
        document.getElementById('city-dropdown')?.addEventListener('click', (e) => {
            const option = e.target.closest('.city-option');
            if (option) { document.getElementById('search-city').value = option.dataset.name; document.getElementById('city-dropdown').style.display = 'none'; }
        });

        // Job card click
        document.querySelectorAll('.job-card').forEach(card => {
            card.addEventListener('click', async () => {
                const jobId = card.dataset.jobId;
                searchState.selectedJob = jobId;
                const result = await api.getScrapedDetails([jobId]);
                if (result?.ok && result.data.details.length) searchState.detailData = result.data.details[0];
                else searchState.detailData = null;
                renderPage();
            });
        });

        // Close detail popup
        document.getElementById('btn-close-detail')?.addEventListener('click', () => { searchState.selectedJob = null; searchState.detailData = null; renderPage(); });
        document.getElementById('detail-overlay')?.addEventListener('click', (e) => { if (e.target.id === 'detail-overlay') { searchState.selectedJob = null; searchState.detailData = null; renderPage(); } });

        // Pagination
        document.getElementById('btn-prev-page')?.addEventListener('click', async () => { searchState.resultOffset = Math.max(0, searchState.resultOffset - searchState.resultLimit); await loadResults(); });
        document.getElementById('btn-next-page')?.addEventListener('click', async () => { searchState.resultOffset += searchState.resultLimit; await loadResults(); });

        // Match result expand/collapse
        document.querySelectorAll('[data-toggle-detail]').forEach(el => {
            el.addEventListener('click', () => {
                const jobId = el.dataset.toggleDetail;
                matchState.expandedJobId = matchState.expandedJobId === jobId ? null : jobId;
                renderPage();
            });
        });

        // Optimize resume from match result
        document.querySelectorAll('[data-optimize-job]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const jobId = btn.dataset.optimizeJob;
                const active = resumePageState.resumes.find(r => r.is_active);
                if (!active) { alert('请先设置当前简历'); return; }
                navigateTo('resume-center');
                // Trigger optimization after page loads
                setTimeout(async () => {
                    optimizationState.generating = true;
                    renderPage();
                    const result = await api.optimizeResume(active.id, jobId);
                    if (result?.ok) { startOptimizationPolling(); }
                    else { optimizationState.generating = false; alert('优化启动失败: ' + (result?.error || '未知错误')); renderPage(); }
                }, 300);
            });
        });

        // Interview prep from match result
        document.querySelectorAll('[data-interview-prep]').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const jobId = btn.dataset.interviewPrep;
                const active = resumePageState.resumes.find(r => r.is_active);
                if (!active) { alert('请先设置当前简历'); return; }
                interviewPrepState.generating = true;
                interviewPrepState.activePrep = null;
                renderPage();
                const result = await api.startInterviewPrep(active.id, jobId);
                if (result?.ok) { startInterviewPrepPolling(); }
                else { interviewPrepState.generating = false; alert('面试准备启动失败: ' + (result?.error || '未知错误')); renderPage(); }
            });
        });

        // Close interview prep detail
        document.getElementById('btn-close-interview-prep')?.addEventListener('click', () => {
            interviewPrepState.activePrep = null;
            interviewPrepState.expandedQuestionId = null;
            renderPage();
        });

        // Toggle interview question expand
        document.querySelectorAll('[data-toggle-question]').forEach(el => {
            el.addEventListener('click', () => {
                const idx = parseInt(el.dataset.toggleQuestion);
                interviewPrepState.expandedQuestionId = interviewPrepState.expandedQuestionId === idx ? null : idx;
                renderPage();
            });
        });

        // ---- Resume Center Events ----
        document.getElementById('btn-add-resume')?.addEventListener('click', () => { document.getElementById('resume-file-input')?.click(); });

        document.getElementById('resume-file-input')?.addEventListener('change', async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = async (event) => {
                const base64 = event.target.result.split(',')[1];
                const result = await api.uploadResume(file.name, base64);
                if (result?.ok) await initResumeCenterPage();
                else alert('文件解析失败: ' + (result?.error || '未知错误'));
                e.target.value = '';
            };
            reader.readAsDataURL(file);
        });

        document.querySelectorAll('[data-edit-resume]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = parseInt(btn.dataset.editResume);
                const result = await api.getResumeById(id);
                if (result?.ok) { resumePageState.editingId = id; resumePageState.content = result.data.content || ''; resumePageState.contentDirty = false; renderPage(); }
            });
        });

        document.getElementById('btn-save-resume-edit')?.addEventListener('click', async () => {
            const content = resumePageState.vditor ? resumePageState.vditor.getValue().trim() : resumePageState.content.trim();
            if (!content) { alert('简历内容为空'); return; }
            const result = await api.saveResume(content);
            if (result?.ok) { resumePageState.contentDirty = false; resumePageState.editingId = null; await initResumeCenterPage(); }
            else alert('保存失败: ' + (result?.error || '未知错误'));
        });

        document.getElementById('btn-cancel-resume-edit')?.addEventListener('click', () => { resumePageState.editingId = null; resumePageState.contentDirty = false; renderPage(); });

        document.querySelectorAll('[data-set-active]').forEach(btn => {
            btn.addEventListener('click', async () => { const id = parseInt(btn.dataset.setActive); await api.setActiveResume(id); await initResumeCenterPage(); });
        });

        document.querySelectorAll('[data-delete-resume]').forEach(btn => {
            btn.addEventListener('click', async () => { const id = parseInt(btn.dataset.deleteResume); if (!confirm('确定删除此简历？')) return; await api.deleteResume(id); await initResumeCenterPage(); });
        });

        // Supplement upload
        document.getElementById('btn-upload-supplement')?.addEventListener('click', () => { document.getElementById('supplement-file-input')?.click(); });
        document.getElementById('supplement-file-input')?.addEventListener('change', async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = async (event) => {
                const base64 = event.target.result.split(',')[1];
                const result = await api.uploadSupplement(file.name, base64);
                if (result?.ok) { resumePageState.supplements.push({ filename: file.name, content: result.data.content }); renderPage(); }
                e.target.value = '';
            };
            reader.readAsDataURL(file);
        });
        document.querySelectorAll('[data-remove-supplement]').forEach(btn => {
            btn.addEventListener('click', () => { const idx = parseInt(btn.dataset.removeSupplement); resumePageState.supplements.splice(idx, 1); renderPage(); });
        });

        // View optimization detail
        document.querySelectorAll('[data-view-optimization]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = parseInt(btn.dataset.viewOptimization);
                const result = await api.getOptimization(id);
                if (result?.ok) { optimizationState.activeOptimization = result.data; renderPage(); }
            });
        });

        // Close optimization detail
        document.getElementById('btn-close-optimization')?.addEventListener('click', () => { optimizationState.activeOptimization = null; renderPage(); });

        // Delete optimization
        document.querySelectorAll('[data-delete-optimization]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = parseInt(btn.dataset.deleteOptimization);
                if (!confirm('确定删除此优化记录？')) return;
                await api.deleteOptimization(id);
                await initResumeCenterPage();
            });
        });

        // Revert optimization section
        document.querySelectorAll('[data-revert-section]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const idx = parseInt(btn.dataset.revertSection);
                const optId = optimizationState.activeOptimization?.id;
                if (!optId) return;
                const result = await api.revertOptimizationSection(optId, idx);
                if (result?.ok) {
                    optimizationState.activeOptimization.diff_sections = result.data.sections;
                    optimizationState.activeOptimization.optimized_content = result.data.optimized_content;
                    renderPage();
                }
            });
        });

        // ---- Settings Events ----
        document.getElementById('btn-save-settings')?.addEventListener('click', async () => {
            const settings = {
                identity: currentIdentity,
                api_base_url: document.getElementById('setting-api-url')?.value || '',
                api_key: document.getElementById('setting-api-key')?.value || '',
                api_model: document.getElementById('setting-api-model')?.value || 'gpt-4o',
            };
            const result = await api.saveSettings(settings);
            if (result?.ok) alert('设置已保存');
            else alert('保存失败: ' + (result?.error || '未知错误'));
        });
    }

    // ---- Navigation ----

    function navigateTo(page) {
        if (resumePageState.vditor) { try { resumePageState.vditor.destroy(); } catch(e) {} resumePageState.vditor = null; }
        currentPage = page;
        document.getElementById('content').scrollTop = 0;
        document.querySelectorAll('.nav-item').forEach(n => { n.classList.toggle('active', n.dataset.page === page); });
        if (page === 'dashboard') initDashboardPage();
        else if (page === 'resume-center') initResumeCenterPage();
        else if (page === 'job-center') initJobCenterPage();
        else if (page === 'settings') { renderPage(); initSettingsPage(); }
        else renderPage();
    }

    // ---- RAG Match Event Handlers ----

    function handlePhaseStart(evt) {
        matchState.phase = evt.phase;
        matchState.phaseProgress = { current: 0, total: evt.total || 0 };
        matchState.modelDownloadStatus = null;
        if (evt.phase === 'pipeline_scraping' || evt.phase === 'pipeline_scoring') {
            searchState.pipelineRunning = true;
            if (currentPage === 'job-center') renderPage();
        }
    }

    function handlePhaseProgress(evt) {
        matchState.phaseProgress = { current: evt.current || 0, total: evt.total || 0 };
        if (searchState.pipelineRunning && currentPage === 'job-center') {
            if (searchState.pipelineProgress) {
                if (evt.current !== undefined) { searchState.pipelineProgress.current_page = evt.current; searchState.pipelineProgress.total_pages = evt.total || searchState.pipelineProgress.total_pages; }
                if (evt.jobs_found !== undefined) searchState.pipelineProgress.jobs_found = evt.jobs_found;
                if (evt.jobs_scored !== undefined) searchState.pipelineProgress.jobs_scored = evt.jobs_scored;
                searchState.pipelineProgress.phase = evt.phase === 'pipeline_scraping' ? 'scraping' : 'scoring';
            }
            renderPage();
        }
    }

    function handlePhaseDone(evt) {}

    function handleEmbedderInit(evt) {
        embedderState.status = evt.status || 'idle';
        if (evt.status === 'downloading') { embedderState.progress = evt.progress || 0; embedderState.speed = evt.speed || 0; }
        else if (evt.status === 'ready') { embedderState.progress = 1.0; embedderState.speed = 0; }
        else if (evt.status === 'failed') embedderState.error = evt.error || '';
        renderPage();
    }

    function handleModelDownload(evt) {
        matchState.modelDownloadStatus = { progress: evt.progress, status: evt.status, speed: evt.speed || 0 };
    }

    function handleJobScored(evt) {
        matchState.streamedJobs.push({ target_job_id: evt.job_id, title: evt.title, score: evt.score, evidence: evt.evidence, reasoning: evt.reasoning, gaps: evt.gaps, suggestions: evt.suggestions, retrieved_chunks: evt.retrieved_chunks });
        if (searchState.pipelineRunning && searchState.pipelineProgress) {
            searchState.pipelineProgress.jobs_scored = (searchState.pipelineProgress.jobs_scored || 0) + 1;
            if (currentPage === 'job-center') renderPage();
        }
    }

    function handleJobFailed(evt) {}

    function handleSummaryChunk(evt) {
        matchState.summaryBuffer += (evt.content || '');
    }

    function handleSummaryDone(evt) {
        matchState.summaryStructured = evt.structured;
        if (evt.structured) summaryState.summary = { structured: evt.structured, raw_text: evt.raw || matchState.summaryBuffer };
        else if (evt.raw) summaryState.summary = { raw_text: evt.raw };
        if (currentPage === 'job-center') renderPage();
    }

    function handleMatchCompleted(evt) {
        matchState.phase = 'completed';
        stopMatchPolling();
        loadMatchResults().then(() => { if (currentPage === 'job-center') renderPage(); });
    }

    function handleCancelled(evt) {
        matchState.phase = 'cancelled';
        stopMatchPolling();
        if (searchState.pipelineRunning) { searchState.pipelineRunning = false; searchState.pipelineProgress = null; stopPipelinePolling(); }
        if (currentPage === 'job-center') renderPage();
    }

    function handleMatchError(evt) {
        matchState.phase = 'failed';
        stopMatchPolling();
        if (searchState.pipelineRunning) { searchState.pipelineRunning = false; searchState.pipelineProgress = null; stopPipelinePolling(); }
        if (currentPage === 'job-center') renderPage();
    }

    function handlePipelineCompleted(evt) {
        searchState.pipelineRunning = false;
        searchState.pipelineProgress = null;
        stopPipelinePolling();
        if (currentPage === 'job-center') loadResults().then(() => renderPage());
    }

    function handleOptimizationCompleted(evt) {
        optimizationState.generating = false;
        stopOptimizationPolling();
        if (evt.optimization_id) {
            api.getOptimization(evt.optimization_id).then(result => {
                if (result?.ok) { optimizationState.activeOptimization = result.data; if (currentPage === 'resume-center') renderPage(); }
            });
        }
        // Also refresh optimization list
        if (currentPage === 'resume-center') initResumeCenterPage();
    }

    function handleOptimizationProgress(evt) {
        optimizationState.progress = evt;
    }

    function handleOptimizationFailed(evt) {
        optimizationState.generating = false;
        stopOptimizationPolling();
        if (currentPage === 'resume-center') renderPage();
        alert('简历优化失败: ' + (evt.error_message || '未知错误'));
    }

    function handleInterviewPrepCompleted(evt) {
        interviewPrepState.generating = false;
        stopInterviewPrepPolling();
        if (evt.prep_id) {
            api.getInterviewPrep(evt.prep_id).then(result => {
                if (result?.ok) {
                    interviewPrepState.activePrep = result.data;
                    if (currentPage === 'job-center') renderPage();
                }
            });
        }
    }

    function handleInterviewPrepProgress(evt) {
        interviewPrepState.progress = evt;
    }

    function handleInterviewPrepFailed(evt) {
        interviewPrepState.generating = false;
        stopInterviewPrepPolling();
        if (currentPage === 'job-center') renderPage();
        alert('面试准备失败: ' + (evt.error_message || '未知错误'));
    }

    // ---- Frontend progress callbacks ----

    window.__onScrapeProgress = function(payload) {
        if (payload && payload.status === 'running') {
            searchState.scrapeProgress = payload;
            if (currentPage === 'job-center') loadResults().then(() => renderPage());
        } else if (payload && payload.status !== 'running') {
            searchState.scrapeProgress = payload;
            stopProgressPolling();
            if (payload.status === 'completed') loadResults().then(() => renderPage());
            else if (currentPage === 'job-center') renderPage();
        }
    };

    window.__onMatchProgress = function(evt) {
        if (!evt) return;
        switch (evt.type) {
            case 'embedder_init': handleEmbedderInit(evt); break;
            case 'phase_start': handlePhaseStart(evt); break;
            case 'phase_progress': handlePhaseProgress(evt); break;
            case 'phase_done': handlePhaseDone(evt); break;
            case 'model_download_progress': handleModelDownload(evt); break;
            case 'job_scored': handleJobScored(evt); break;
            case 'job_failed': handleJobFailed(evt); break;
            case 'summary_chunk': handleSummaryChunk(evt); break;
            case 'summary_done': handleSummaryDone(evt); break;
            case 'match_completed': handleMatchCompleted(evt); break;
            case 'pipeline_completed': handlePipelineCompleted(evt); break;
            case 'cancelled': handleCancelled(evt); break;
            case 'error': handleMatchError(evt); break;
            case 'optimization_progress': handleOptimizationProgress(evt); break;
            case 'optimization_completed': handleOptimizationCompleted(evt); break;
            case 'optimization_failed': handleOptimizationFailed(evt); break;
            case 'interview_prep_progress': handleInterviewPrepProgress(evt); break;
            case 'interview_prep_completed': handleInterviewPrepCompleted(evt); break;
            case 'interview_prep_failed': handleInterviewPrepFailed(evt); break;
            default:
                if (evt.type === 'match' || evt.status) { matchState.matchProgress = evt; }
                break;
        }
    };

    // ---- Init ----

    document.querySelectorAll('.identity-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const mode = btn.dataset.mode;
            const result = await api.switchIdentity(mode);
            if (result?.ok) {
                currentIdentity = mode;
                await refreshChromeStatus();
                renderPage();
                document.querySelectorAll('.identity-btn').forEach(b => { b.classList.toggle('active', b.dataset.mode === mode); });
            }
        });
    });

    document.querySelectorAll('.nav-item:not(.disabled)').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const page = item.dataset.page;
            if (page) navigateTo(page);
        });
    });

    await api.waitForPyWebview();
    await refreshState();

    const embedderRes = await api.getEmbedderStatus();
    if (embedderRes?.ok?.data) {
        embedderState.status = embedderRes.data.status || 'idle';
        embedderState.error = embedderRes.data.error || '';
        if (embedderState.status === 'idle') { api.initEmbedder(); embedderState.status = 'downloading'; }
    }

    navigateTo('dashboard');
    console.log('BossMatch UI initialized');
})();
