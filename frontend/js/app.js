// App initialization, routing, and page rendering
(async function() {
    let currentPage = 'login';
    let currentIdentity = 'geek';
    let chromeStatus = { running: false, logged_in: false };

    // Search page state
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
    };

    // Match page state
    let matchState = {
        resume: '',
        resumeLoaded: false,
        resumeDirty: false,
        vditor: null,
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
    };

    // ---- Utility ----

    function debounce(fn, delay) {
        let timer;
        return function(...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    // ---- Page Renderers ----

    function renderLoginPage() {
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
        <div class="login-page">
            <div class="login-card">
                <h2>${identityLabel} — Chrome 登录管理</h2>
                <p class="login-desc">启动专用 Chrome 浏览器，登录 BOSS直聘后即可开始使用</p>

                <div class="chrome-status ${statusClass}">
                    <span class="status-icon">${statusIcon}</span>
                    <div class="status-info">
                        <div class="status-label">Chrome 状态</div>
                        <div class="status-value">${statusText}</div>
                    </div>
                </div>

                <div class="login-actions">
                    ${!running ? `
                    <button class="btn btn-primary" id="btn-start-chrome">
                        启动 Chrome
                    </button>` : ''}
                    ${running && !loggedIn ? `
                    <button class="btn btn-warning" id="btn-check-login">
                        检测登录状态
                    </button>
                    <p class="login-hint">请在弹出的 Chrome 窗口中登录 BOSS直聘，然后点击"检测登录状态"</p>` : ''}
                    ${running ? `
                    <button class="btn btn-danger" id="btn-stop-chrome">
                        关闭 Chrome
                    </button>` : ''}
                    ${loggedIn ? `
                    <div class="login-success">
                        <p>已成功登录，可以开始使用搜索和匹配功能</p>
                        <button class="btn btn-primary" id="btn-go-search">
                            开始搜索
                        </button>
                    </div>` : ''}
                </div>

                <div class="login-info">
                    <div>当前身份: <strong>${identityLabel}</strong></div>
                    <div>登录地址: <a href="${loginUrl}" class="link">${loginUrl}</a></div>
                </div>
            </div>
        </div>`;
    }

    function renderSearchPage() {
        const progress = searchState.scrapeProgress;
        const isScraping = progress && progress.status === 'running';
        const hasResults = searchState.results.length > 0;

        return `
        <div class="search-page">
            <div class="search-form-card">
                <h2>职位搜索</h2>
                <div class="search-form">
                    <div class="form-row">
                        <div class="form-group flex-1">
                            <label>关键词 *</label>
                            <input type="text" id="search-keyword"
                                   value="${searchState.keyword}"
                                   placeholder="如: AI Agent, Java, 产品经理">
                        </div>
                        <div class="form-group city-group">
                            <label>城市</label>
                            <input type="text" id="search-city"
                                   value="${searchState.city}"
                                   placeholder="如: 上海, 北京"
                                   autocomplete="off">
                            <div class="city-dropdown" id="city-dropdown"></div>
                        </div>
                        <div class="form-group pages-group">
                            <label>页数</label>
                            <select id="search-pages">
                                ${[1,2,3,5,10].map(n =>
                                    `<option value="${n}" ${n===searchState.maxPages?'selected':''}>${n}页</option>`
                                ).join('')}
                            </select>
                        </div>
                    </div>
                    <div class="filter-row">
                        ${renderFilterDropdowns()}
                    </div>
                    <div class="search-actions">
                        <button class="btn btn-primary" id="btn-start-search"
                                ${isScraping ? 'disabled' : ''}>
                            ${isScraping ? '抓取中...' : '开始搜索'}
                        </button>
                        ${isScraping ? `
                        <button class="btn btn-danger" id="btn-cancel-scrape">
                            取消抓取
                        </button>` : ''}
                    </div>
                </div>
            </div>

            ${isScraping ? renderProgressIndicator(progress) : ''}
            ${hasResults ? renderResultsArea(isScraping) : ''}
        </div>
        ${searchState.selectedJob ? renderDetailPopup() : ''}`;
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
        const pageText = progress.phase === 'list'
            ? `第 ${progress.current_page}/${progress.total_pages} 页`
            : '';
        const detailText = progress.phase === 'details'
            ? `已抓 ${progress.details_scraped} 条详情`
            : '';
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

    function renderResultsArea(isScraping = false) {
        const jobs = searchState.results;
        const total = searchState.resultTotal;
        const offset = searchState.resultOffset;
        const limit = searchState.resultLimit;
        const hasMore = offset + limit < total;
        const hasPrev = offset > 0;

        return `
        <div class="results-area">
            <div class="results-header">
                <span class="results-count">共 ${total} 个职位${isScraping ? ' · 实时更新中...' : ''}</span>
            </div>
            <div class="job-cards">
                ${jobs.map(job => renderJobCard(job)).join('')}
            </div>
            ${!isScraping ? `
            <div class="pagination">
                ${hasPrev ? `<button class="btn btn-secondary" id="btn-prev-page">上一页</button>` : ''}
                <span class="page-info">${Math.floor(offset/limit)+1} / ${Math.ceil(total/limit) || 1}</span>
                ${hasMore ? `<button class="btn btn-secondary" id="btn-next-page">下一页</button>` : ''}
            </div>` : ''}
        </div>`;
    }

    function renderJobCard(job) {
        const skills = job.skills || '';
        const tags = job.tags || '';
        return `
        <div class="job-card" data-job-id="${job.job_id}">
            <div class="job-card-header">
                <span class="job-title">${job.title}</span>
                <span class="job-salary">${job.salary || ''}</span>
            </div>
            <div class="job-card-body">
                <div class="job-tags">${tags}</div>
                <div class="job-location">${job.location || ''}</div>
            </div>
            <div class="job-card-footer">
                <div class="boss-info">
                    <span class="boss-name">${job.boss_name || ''}</span>
                    <span class="boss-status ${job.boss_active_status ? 'active' : ''}">${job.boss_active_status || ''}</span>
                </div>
                <div class="company-info">
                    <span>${job.company_scale || ''}</span>
                    <span>${job.company_stage || ''}</span>
                    <span>${job.company_industry || ''}</span>
                </div>
            </div>
            ${skills ? `<div class="job-card-skills">${skills}</div>` : ''}
        </div>`;
    }

    function renderDetailPopup() {
        const detail = searchState.detailData;
        if (!detail) return `
        <div class="detail-overlay" id="detail-overlay">
            <div class="detail-popup loading">加载中...</div>
        </div>`;

        let skillTags = [];
        try {
            skillTags = JSON.parse(detail.skill_tags || '[]');
        } catch(e) {}

        return `
        <div class="detail-overlay" id="detail-overlay">
            <div class="detail-popup">
                <div class="detail-header">
                    <h3>${detail.title}</h3>
                    <span class="detail-salary">${detail.salary || ''}</span>
                    <button class="btn-close" id="btn-close-detail">&times;</button>
                </div>
                <div class="detail-meta">
                    <span>${detail.company || ''}</span>
                    <span>${detail.location || ''}</span>
                    <span class="boss-status ${detail.boss_active_status ? 'active' : ''}">${detail.boss_active_status || ''}</span>
                </div>
                <div class="detail-tags">
                    ${(detail.tags_list || '').split(' | ').filter(Boolean).map(t =>
                        `<span class="tag">${t}</span>`).join('')}
                </div>
                ${skillTags.length ? `
                <div class="detail-skills">
                    <h4>技能要求</h4>
                    ${skillTags.map(t => `<span class="skill-tag">${t}</span>`).join('')}
                </div>` : ''}
                <div class="detail-jd">
                    <h4>职位描述</h4>
                    <pre class="jd-text">${detail.jd || '暂无详情'}</pre>
                </div>
            </div>
        </div>`;
    }

    function renderMatchesPage() {
        const progress = matchState.matchProgress;
        const isMatching = progress && progress.status === 'running';
        const hasResults = matchState.results.length > 0;
        const resumeStatus = !matchState.resume.trim()
            ? '<span class="resume-status empty">请先填写简历</span>'
            : matchState.resumeDirty
            ? '<span class="resume-status dirty">未保存更改</span>'
            : matchState.resumeLoaded
            ? '<span class="resume-status saved">已保存</span>'
            : '';
        const selectedCount = matchState.selectedJobIds.size;
        const canMatch = matchState.resumeLoaded && !matchState.resumeDirty && selectedCount > 0 && !isMatching;

        return `
        <div class="match-page">
            <div class="resume-section">
                <h3>我的简历</h3>
                <div class="resume-upload-area">
                    <button class="btn btn-secondary btn-sm" id="btn-upload-resume">上传简历文件</button>
                    <span class="upload-hint">支持 PDF、DOCX、TXT、MD 格式</span>
                    <input type="file" id="resume-file-input" accept=".pdf,.docx,.doc,.txt,.md" style="display:none">
                    <span class="upload-status" id="upload-status"></span>
                </div>
                <div id="vditor" class="resume-editor"></div>
                <div class="resume-actions">
                    <button class="btn btn-primary btn-sm" id="btn-save-resume"
                            ${!matchState.resumeDirty && matchState.resumeLoaded ? 'disabled' : ''}>
                        ${matchState.resumeLoaded && !matchState.resumeDirty ? '已保存' : '保存简历'}
                    </button>
                    ${resumeStatus}
                </div>
            </div>

            <div class="job-select-section">
                <h3>选择职位 <span class="select-count">已选 ${selectedCount} 个</span></h3>
                <div class="job-select-actions">
                    <button class="btn btn-secondary btn-sm" id="btn-load-jobs">
                        ${matchState.availableJobsLoaded ? '刷新职位' : '加载已抓取职位'}
                    </button>
                    ${matchState.availableJobsLoaded ? `
                    <button class="btn btn-secondary btn-sm" id="btn-select-all">全选</button>
                    <button class="btn btn-secondary btn-sm" id="btn-deselect-all">取消全选</button>` : ''}
                </div>
                ${matchState.availableJobsLoaded ? `
                <div class="job-select-grid">
                    ${matchState.availableJobs.map(job => {
                        const checked = matchState.selectedJobIds.has(job.job_id);
                        return `
                        <div class="job-select-item ${checked ? 'selected' : ''}" data-job-id="${job.job_id}">
                            <input type="checkbox" class="job-select-checkbox" data-job-id="${job.job_id}" ${checked ? 'checked' : ''}>
                            <div class="job-select-info">
                                <span class="job-select-title">${job.title}</span>
                                <span class="job-select-salary">${job.salary || ''}</span>
                                <span class="job-select-company">${job.company_scale || ''} ${job.company_industry || ''}</span>
                            </div>
                        </div>`;
                    }).join('')}
                </div>` : `
                <div class="coming-soon-box">
                    <p>点击"加载已抓取职位"选择要匹配的职位</p>
                </div>`}
            </div>

            <div class="match-actions">
                <button class="btn btn-primary" id="btn-start-match"
                        ${!canMatch ? 'disabled' : ''}>
                    ${isMatching ? '匹配中...' : `开始匹配 (${selectedCount}个职位)`}
                </button>
                ${isMatching ? `
                <button class="btn btn-danger" id="btn-cancel-match">取消匹配</button>` : ''}
            </div>

            <div id="match-dynamic-area">
                ${isMatching ? renderMatchProgress(progress) : ''}
                ${hasResults ? renderMatchResults() : ''}
            </div>
        </div>`;
    }

    function renderMatchProgress(progress) {
        const completed = progress.completed || 0;
        const total = progress.total_jobs || 0;
        const skipped = progress.skipped || 0;
        const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
        const currentTitle = progress.current_job_title || '';

        return `
        <div class="progress-card">
            <div class="progress-header">
                <span class="progress-phase">AI 匹配</span>
                <span class="progress-stats">${completed}/${total} 已匹配${skipped ? ` · ${skipped}个缓存跳过` : ''}${currentTitle ? ` · 当前: ${currentTitle}` : ''}</span>
            </div>
            <div class="progress-bar-track">
                <div class="progress-bar-fill" style="width: ${percent}%"></div>
            </div>
        </div>`;
    }

    function renderMatchResults() {
        const results = matchState.results;
        const total = matchState.resultTotal;

        return `
        <div class="match-results-area">
            <div class="results-header">
                <span class="results-count">匹配结果: ${total} 个职位</span>
            </div>
            <div class="match-result-cards">
                ${results.map(r => renderMatchResultCard(r)).join('')}
            </div>
        </div>`;
    }

    function renderMatchResultCard(r) {
        const scorePercent = Math.round(r.score * 100);
        const scoreClass = r.score >= 0.7 ? 'high' : r.score >= 0.4 ? 'medium' : 'low';
        const isExpanded = matchState.expandedJobId === r.target_job_id;
        const suggestions = Array.isArray(r.suggestions) ? r.suggestions : [];

        return `
        <div class="match-result-card" data-job-id="${r.target_job_id}">
            <div class="match-result-header" data-toggle-detail="${r.target_job_id}">
                <div class="match-result-info">
                    <span class="match-result-title">${r.title || '未知职位'}</span>
                    <span class="match-result-salary">${r.salary || ''}</span>
                    <span class="match-result-company">${r.company || ''}</span>
                    <span class="match-result-location">${r.location || ''}</span>
                </div>
                <span class="score-badge ${scoreClass}">${scorePercent}%</span>
            </div>
            ${isExpanded ? `
            <div class="match-result-detail">
                <div class="match-reasoning">
                    <h4>匹配分析</h4>
                    <p>${r.reasoning || '暂无分析'}</p>
                </div>
                ${suggestions.length ? `
                <div class="match-suggestions">
                    <h4>改进建议</h4>
                    <ul>${suggestions.map(s => `<li>${s}</li>`).join('')}</ul>
                </div>` : ''}
                ${r.jd ? `
                <div class="match-jd">
                    <h4>职位描述</h4>
                    <pre class="jd-text">${r.jd}</pre>
                </div>` : ''}
            </div>` : ''}
        </div>`;
    }

    function renderSummaryPage() {
        return `
        <div class="page">
            <h2>市场摘要</h2>
            <p class="page-desc">职位市场数据概览</p>
            <div class="coming-soon-box">
                <p>摘要功能将在后续阶段实现</p>
            </div>
        </div>`;
    }

    function renderSettingsPage() {
        return `
        <div class="page">
            <h2>设置</h2>
            <p class="page-desc">配置 AI API 和应用参数</p>
            <div class="settings-form" id="settings-form">
                <div class="form-group">
                    <label>API Base URL</label>
                    <input type="text" id="setting-api-url" placeholder="https://api.openai.com/v1">
                </div>
                <div class="form-group">
                    <label>API Key</label>
                    <input type="password" id="setting-api-key" placeholder="sk-...">
                </div>
                <div class="form-group">
                    <label>Model</label>
                    <input type="text" id="setting-api-model" placeholder="gpt-4o">
                </div>
                <button class="btn btn-primary" id="btn-save-settings">保存设置</button>
            </div>
        </div>`;
    }

    const pageRenderers = {
        login: renderLoginPage,
        search: renderSearchPage,
        matches: renderMatchesPage,
        summary: renderSummaryPage,
        settings: renderSettingsPage,
    };

    // ---- Rendering ----

    function renderPage() {
        const content = document.getElementById('content');
        const scrollTop = content.scrollTop;

        if (matchState.vditor) {
            try { matchState.vditor.destroy(); } catch(e) {}
            matchState.vditor = null;
        }

        const renderer = pageRenderers[currentPage];
        if (renderer) {
            content.innerHTML = renderer();
            bindPageEvents();
        }

        if (currentPage === 'matches') {
            initVditor();
        }

        content.scrollTop = scrollTop;
    }

    async function refreshChromeStatus() {
        // Use auto_detect_login to leverage cache and auto-probe
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
        if (currentPage === 'login') {
            renderPage();
        }
    }

    // ---- Search Page Helpers ----

    function startProgressPolling() {
        stopProgressPolling();
        searchState.pollTimer = setInterval(async () => {
            const result = await api.getScrapeProgress();
            if (result?.ok && result.data.status !== 'idle') {
                searchState.scrapeProgress = result.data;
                // Progressive display: refresh results from DB during scraping
                if (result.data.status === 'running') {
                    await loadResults();
                }
                renderPage();
                if (result.data.status !== 'running') {
                    stopProgressPolling();
                    if (result.data.status === 'completed') {
                        await loadResults();
                    }
                }
            }
        }, 2000);
    }

    function stopProgressPolling() {
        if (searchState.pollTimer) {
            clearInterval(searchState.pollTimer);
            searchState.pollTimer = null;
        }
    }

    async function loadResults() {
        const result = await api.getScrapedJobs(
            searchState.keyword, searchState.city,
            searchState.resultOffset, searchState.resultLimit,
        );
        if (result?.ok) {
            searchState.results = result.data.jobs;
            searchState.resultTotal = result.data.total;
            renderPage();
        }
    }

    async function initSearchPage() {
        if (!searchState.filterOptions) {
            const result = await api.getFilterOptions();
            if (result?.ok) {
                searchState.filterOptions = result.data;
            }
        }
        const progress = await api.getScrapeProgress();
        if (progress?.ok && progress.data.status === 'running') {
            searchState.scrapeProgress = progress.data;
            startProgressPolling();
        }
        renderPage();
    }

    // ---- Match Page Helpers ----

    function startMatchPolling() {
        stopMatchPolling();
        matchState.pollTimer = setInterval(async () => {
            const result = await api.getMatchProgress();
            if (result?.ok && result.data.status !== 'idle') {
                matchState.matchProgress = result.data;
                if (result.data.status === 'running') {
                    await loadMatchResults();
                }
                updateMatchDynamicArea();
                if (result.data.status !== 'running') {
                    stopMatchPolling();
                    if (result.data.status === 'completed') {
                        await loadMatchResults();
                        updateMatchDynamicArea();
                    }
                }
            }
        }, 2000);
    }

    function stopMatchPolling() {
        if (matchState.pollTimer) {
            clearInterval(matchState.pollTimer);
            matchState.pollTimer = null;
        }
    }

    async function loadMatchResults() {
        const result = await api.getMatchResults(1, matchState.resultLimit, matchState.resultOffset);
        if (result?.ok) {
            matchState.results = result.data.results;
            matchState.resultTotal = result.data.total;
        }
    }

    async function initMatchesPage() {
        const resumeResult = await api.getResume();
        if (resumeResult?.ok) {
            matchState.resume = resumeResult.data.content || '';
            matchState.resumeLoaded = !!resumeResult.data.content?.trim();
            matchState.resumeDirty = false;
        }
        const progress = await api.getMatchProgress();
        if (progress?.ok && progress.data.status === 'running') {
            matchState.matchProgress = progress.data;
            startMatchPolling();
        }
        await loadMatchResults();
        renderPage();
    }

    function initVditor() {
        if (matchState.vditor) {
            try { matchState.vditor.destroy(); } catch(e) {}
            matchState.vditor = null;
        }
        const el = document.getElementById('vditor');
        if (!el) return;
        matchState.vditor = new Vditor('vditor', {
            theme: 'dark',
            mode: 'ir',
            lang: 'zh_CN',
            height: 300,
            placeholder: '粘贴简历内容，或上传 PDF/DOCX 文件...',
            preview: {
                theme: { current: 'dark' },
            },
            cache: { enable: false },
            toolbar: [
                'headings', 'bold', 'italic', 'strike', '|',
                'list', 'ordered-list', 'check', '|',
                'quote', 'code', 'inline-code', '|',
                'link', 'table', '|',
                'undo', 'redo', '|',
                'fullscreen', 'preview',
            ],
            after: () => {
                if (matchState.resume) {
                    matchState.vditor.setValue(matchState.resume);
                }
            },
            input: () => {
                matchState.resume = matchState.vditor.getValue();
                matchState.resumeDirty = true;
                const saveBtn = document.getElementById('btn-save-resume');
                if (saveBtn) {
                    saveBtn.disabled = false;
                    saveBtn.textContent = '保存简历';
                }
                const statusEl = document.querySelector('.resume-status');
                if (statusEl) {
                    statusEl.className = 'resume-status dirty';
                    statusEl.textContent = '未保存更改';
                }
            },
        });
    }

    function updateMatchDynamicArea() {
        const area = document.getElementById('match-dynamic-area');
        if (!area) return;
        const progress = matchState.matchProgress;
        const isMatching = progress && progress.status === 'running';
        const hasResults = matchState.results.length > 0;
        area.innerHTML = `
            ${isMatching ? renderMatchProgress(progress) : ''}
            ${hasResults ? renderMatchResults() : ''}
        `;
        bindMatchResultEvents();
    }

    function bindMatchResultEvents() {
        document.querySelectorAll('[data-toggle-detail]').forEach(el => {
            el.addEventListener('click', () => {
                const jobId = el.dataset.toggleDetail;
                matchState.expandedJobId = matchState.expandedJobId === jobId ? null : jobId;
                updateMatchDynamicArea();
            });
        });
    }

    // ---- Event Binding ----

    function bindPageEvents() {
        // Login page buttons
        document.getElementById('btn-start-chrome')?.addEventListener('click', async () => {
            const btn = document.getElementById('btn-start-chrome');
            btn.disabled = true;
            btn.textContent = '启动中...';
            const result = await api.setupChrome(currentIdentity);
            if (result && result.ok) {
                await refreshChromeStatus();
                renderPage();
            } else {
                btn.disabled = false;
                btn.textContent = '启动 Chrome';
                alert('启动失败: ' + (result?.error || '未知错误'));
            }
        });

        document.getElementById('btn-check-login')?.addEventListener('click', async () => {
            const btn = document.getElementById('btn-check-login');
            btn.disabled = true;
            btn.textContent = '检测中...';
            const result = await api.checkLogin(currentIdentity);
            if (result && result.ok && result.data.logged_in) {
                await refreshChromeStatus();
                renderPage();
            } else {
                btn.disabled = false;
                btn.textContent = '检测登录状态';
                const msg = result?.data?.message || '未检测到登录态';
                alert('登录状态: ' + msg);
            }
        });

        document.getElementById('btn-stop-chrome')?.addEventListener('click', async () => {
            if (!confirm('确定要关闭 Chrome 吗？')) return;
            const result = await api.stopChrome(currentIdentity);
            if (result && result.ok) {
                await refreshChromeStatus();
                renderPage();
            }
        });

        document.getElementById('btn-go-search')?.addEventListener('click', () => {
            navigateTo('search');
        });

        // Search page buttons
        document.getElementById('btn-start-search')?.addEventListener('click', async () => {
            const keyword = document.getElementById('search-keyword')?.value?.trim();
            if (!keyword) { alert('请输入搜索关键词'); return; }
            const city = document.getElementById('search-city')?.value?.trim() || '上海';
            const maxPages = parseInt(document.getElementById('search-pages')?.value || '3');

            const filters = {};
            document.querySelectorAll('[data-filter]').forEach(sel => {
                const key = sel.dataset.filter;
                const value = sel.value;
                if (value) filters[key] = value;
            });

            searchState.keyword = keyword;
            searchState.city = city;
            searchState.maxPages = maxPages;
            searchState.filters = filters;
            searchState.resultOffset = 0;

            const result = await api.searchJobs(keyword, city, maxPages, filters);
            if (result?.ok) {
                startProgressPolling();
                renderPage();
            } else {
                alert('搜索失败: ' + (result?.error || '未知错误'));
            }
        });

        document.getElementById('btn-cancel-scrape')?.addEventListener('click', async () => {
            await api.cancelScrape();
            stopProgressPolling();
        });

        // City autocomplete
        const cityInput = document.getElementById('search-city');
        if (cityInput) {
            cityInput.addEventListener('input', debounce(async (e) => {
                const keyword = e.target.value.trim();
                if (keyword.length < 1) {
                    document.getElementById('city-dropdown').style.display = 'none';
                    return;
                }
                const result = await api.listCities(keyword);
                if (result?.ok) {
                    const dropdown = document.getElementById('city-dropdown');
                    dropdown.innerHTML = result.data.cities.slice(0, 10).map(c =>
                        `<div class="city-option" data-name="${c.name}" data-code="${c.code}">${c.name}</div>`
                    ).join('');
                    dropdown.style.display = result.data.cities.length ? 'block' : 'none';
                }
            }, 300));
        }

        document.getElementById('city-dropdown')?.addEventListener('click', (e) => {
            const option = e.target.closest('.city-option');
            if (option) {
                document.getElementById('search-city').value = option.dataset.name;
                document.getElementById('city-dropdown').style.display = 'none';
            }
        });

        // Job card click -> show detail
        document.querySelectorAll('.job-card').forEach(card => {
            card.addEventListener('click', async () => {
                const jobId = card.dataset.jobId;
                searchState.selectedJob = jobId;
                const result = await api.getScrapedDetails([jobId]);
                if (result?.ok && result.data.details.length) {
                    searchState.detailData = result.data.details[0];
                } else {
                    searchState.detailData = null;
                }
                renderPage();
            });
        });

        // Close detail popup
        document.getElementById('btn-close-detail')?.addEventListener('click', () => {
            searchState.selectedJob = null;
            searchState.detailData = null;
            renderPage();
        });
        document.getElementById('detail-overlay')?.addEventListener('click', (e) => {
            if (e.target.id === 'detail-overlay') {
                searchState.selectedJob = null;
                searchState.detailData = null;
                renderPage();
            }
        });

        // Pagination
        document.getElementById('btn-prev-page')?.addEventListener('click', async () => {
            searchState.resultOffset = Math.max(0, searchState.resultOffset - searchState.resultLimit);
            await loadResults();
        });
        document.getElementById('btn-next-page')?.addEventListener('click', async () => {
            searchState.resultOffset += searchState.resultLimit;
            await loadResults();
        });

        // Settings page
        document.getElementById('btn-save-settings')?.addEventListener('click', async () => {
            const settings = {
                identity: currentIdentity,
                api_base_url: document.getElementById('setting-api-url')?.value || '',
                api_key: document.getElementById('setting-api-key')?.value || '',
                api_model: document.getElementById('setting-api-model')?.value || 'gpt-4o',
            };
            const result = await api.saveSettings(settings);
            if (result && result.ok) {
                alert('设置已保存');
            } else {
                alert('保存失败: ' + (result?.error || '未知错误'));
            }
        });

        // Match page - Resume save
        document.getElementById('btn-save-resume')?.addEventListener('click', async () => {
            const content = matchState.vditor
                ? matchState.vditor.getValue().trim()
                : matchState.resume.trim();
            if (!content) { alert('简历内容为空，请输入或上传简历'); return; }
            const result = await api.saveResume(content);
            if (result?.ok) {
                matchState.resume = content;
                matchState.resumeLoaded = true;
                matchState.resumeDirty = false;
                const saveBtn = document.getElementById('btn-save-resume');
                if (saveBtn) {
                    saveBtn.disabled = true;
                    saveBtn.textContent = '已保存';
                }
                const statusEl = document.querySelector('.resume-status');
                if (statusEl) {
                    statusEl.className = 'resume-status saved';
                    statusEl.textContent = '已保存';
                }
            } else {
                alert('保存失败: ' + (result?.error || '未知错误'));
            }
        });

        // Match page - File upload
        document.getElementById('btn-upload-resume')?.addEventListener('click', () => {
            document.getElementById('resume-file-input')?.click();
        });

        document.getElementById('resume-file-input')?.addEventListener('change', async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            const statusEl = document.getElementById('upload-status');
            if (statusEl) {
                statusEl.textContent = `正在解析 ${file.name}...`;
                statusEl.className = 'upload-status parsing';
            }
            const reader = new FileReader();
            reader.onload = async (event) => {
                const base64 = event.target.result.split(',')[1];
                const result = await api.uploadResume(file.name, base64);
                if (result?.ok) {
                    const content = result.data.content;
                    matchState.resume = content;
                    matchState.resumeDirty = true;
                    if (matchState.vditor) {
                        matchState.vditor.setValue(content);
                    }
                    if (statusEl) {
                        statusEl.textContent = `${file.name} 解析成功`;
                        statusEl.className = 'upload-status success';
                    }
                    const saveBtn = document.getElementById('btn-save-resume');
                    if (saveBtn) {
                        saveBtn.disabled = false;
                        saveBtn.textContent = '保存简历';
                    }
                    const resumeStatusEl = document.querySelector('.resume-status');
                    if (resumeStatusEl) {
                        resumeStatusEl.className = 'resume-status dirty';
                        resumeStatusEl.textContent = '未保存更改';
                    }
                } else {
                    if (statusEl) {
                        statusEl.textContent = result?.error || '解析失败';
                        statusEl.className = 'upload-status error';
                    }
                    alert('文件解析失败: ' + (result?.error || '未知错误'));
                }
                e.target.value = '';
            };
            reader.readAsDataURL(file);
        });

        // Match page - Load jobs
        document.getElementById('btn-load-jobs')?.addEventListener('click', async () => {
            const result = await api.getScrapedJobs('', '', 0, 200);
            if (result?.ok) {
                matchState.availableJobs = result.data.jobs;
                matchState.availableJobsLoaded = true;
                renderPage();
            } else {
                alert('加载失败: ' + (result?.error || '未知错误'));
            }
        });

        // Match page - Select/deselect all
        document.getElementById('btn-select-all')?.addEventListener('click', () => {
            matchState.availableJobs.forEach(j => matchState.selectedJobIds.add(j.job_id));
            document.querySelectorAll('.job-select-checkbox').forEach(cb => {
                cb.checked = true;
                cb.closest('.job-select-item')?.classList.add('selected');
            });
            const countEl = document.querySelector('.select-count');
            if (countEl) countEl.textContent = `已选 ${matchState.selectedJobIds.size} 个`;
            const matchBtn = document.getElementById('btn-start-match');
            if (matchBtn) {
                const canMatch = matchState.resumeLoaded && !matchState.resumeDirty && matchState.selectedJobIds.size > 0;
                matchBtn.disabled = !canMatch;
                matchBtn.textContent = `开始匹配 (${matchState.selectedJobIds.size}个职位)`;
            }
        });

        document.getElementById('btn-deselect-all')?.addEventListener('click', () => {
            matchState.selectedJobIds.clear();
            document.querySelectorAll('.job-select-checkbox').forEach(cb => {
                cb.checked = false;
                cb.closest('.job-select-item')?.classList.remove('selected');
            });
            const countEl = document.querySelector('.select-count');
            if (countEl) countEl.textContent = `已选 0 个`;
            const matchBtn = document.getElementById('btn-start-match');
            if (matchBtn) {
                matchBtn.disabled = true;
                matchBtn.textContent = '开始匹配 (0个职位)';
            }
        });

        // Match page - Job selection checkboxes
        document.querySelectorAll('.job-select-checkbox').forEach(cb => {
            cb.addEventListener('change', (e) => {
                const jobId = e.target.dataset.jobId;
                if (e.target.checked) {
                    matchState.selectedJobIds.add(jobId);
                } else {
                    matchState.selectedJobIds.delete(jobId);
                }
                const countEl = document.querySelector('.select-count');
                if (countEl) {
                    countEl.textContent = `已选 ${matchState.selectedJobIds.size} 个`;
                }
                const matchBtn = document.getElementById('btn-start-match');
                if (matchBtn) {
                    const canMatch = matchState.resumeLoaded && !matchState.resumeDirty && matchState.selectedJobIds.size > 0;
                    matchBtn.disabled = !canMatch;
                    matchBtn.textContent = `开始匹配 (${matchState.selectedJobIds.size}个职位)`;
                }
                const item = e.target.closest('.job-select-item');
                if (item) {
                    item.classList.toggle('selected', e.target.checked);
                }
            });
        });

        // Match page - Start match
        document.getElementById('btn-start-match')?.addEventListener('click', async () => {
            const jobIds = [...matchState.selectedJobIds];
            if (!jobIds.length) { alert('请选择至少一个职位'); return; }
            if (!matchState.resumeLoaded || matchState.resumeDirty) {
                alert('请先保存简历');
                return;
            }
            const result = await api.startMatch(jobIds);
            if (result?.ok) {
                startMatchPolling();
                renderPage();
            } else {
                alert('匹配启动失败: ' + (result?.error || '未知错误'));
            }
        });

        // Match page - Cancel match
        document.getElementById('btn-cancel-match')?.addEventListener('click', async () => {
            await api.cancelMatch();
            stopMatchPolling();
        });

        // Match page - Expand/collapse result detail
        bindMatchResultEvents();
    }

    function navigateTo(page) {
        if (currentPage === 'matches' && matchState.vditor) {
            try { matchState.vditor.destroy(); } catch(e) {}
            matchState.vditor = null;
        }
        currentPage = page;
        document.getElementById('content').scrollTop = 0;
        document.querySelectorAll('.nav-item').forEach(n => {
            n.classList.toggle('active', n.dataset.page === page);
        });
        if (page === 'search') {
            initSearchPage();
        } else if (page === 'matches') {
            initMatchesPage();
        } else {
            renderPage();
        }
    }

    // ---- Frontend progress callback (called by backend via evaluate_js) ----

    window.__onScrapeProgress = function(payload) {
        if (payload && payload.status === 'running') {
            searchState.scrapeProgress = payload;
            if (currentPage === 'search') {
                // Progressive display: load results from DB on each progress push
                loadResults().then(() => renderPage());
            }
        } else if (payload && payload.status !== 'running') {
            searchState.scrapeProgress = payload;
            stopProgressPolling();
            if (payload.status === 'completed') {
                loadResults().then(() => renderPage());
            } else {
                renderPage();
            }
        }
    };

    window.__onMatchProgress = function(payload) {
        if (payload && payload.type === 'match') {
            matchState.matchProgress = payload;
            if (currentPage === 'matches') {
                if (payload.status === 'running') {
                    loadMatchResults().then(() => updateMatchDynamicArea());
                } else {
                    stopMatchPolling();
                    if (payload.status === 'completed') {
                        loadMatchResults().then(() => updateMatchDynamicArea());
                    } else {
                        updateMatchDynamicArea();
                    }
                }
            }
        }
    };

    // ---- Init ----

    // Identity switch
    document.querySelectorAll('.identity-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const mode = btn.dataset.mode;
            const result = await api.switchIdentity(mode);
            if (result && result.ok) {
                currentIdentity = mode;
                await refreshChromeStatus();
                renderPage();
                document.querySelectorAll('.identity-btn').forEach(b => {
                    b.classList.toggle('active', b.dataset.mode === mode);
                });
            }
        });
    });

    // Nav clicks
    document.querySelectorAll('.nav-item:not(.disabled)').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const page = item.dataset.page;
            if (page) navigateTo(page);
        });
    });

    // Wait for pywebview ready, then init
    await api.waitForPyWebview();
    await refreshState();
    renderPage();

    console.log('BossMatch UI initialized');
})();
