// API wrapper for pywebview bridge
const api = {
    _ready: false,

    async waitForPyWebview() {
        if (this._ready) return;
        while (typeof pywebview === 'undefined' || !pywebview.api || Object.keys(pywebview.api).length === 0) {
            await new Promise(r => setTimeout(r, 50));
        }
        this._ready = true;
    },

    async call(method, ...args) {
        await this.waitForPyWebview();
        try {
            return await pywebview.api[method](...args);
        } catch (e) {
            console.error(`API call failed: ${method}`, e);
            return { ok: false, error: String(e) };
        }
    },

    // Identity
    getIdentity() { return this.call('get_identity'); },
    switchIdentity(mode) { return this.call('switch_identity', mode); },
    getAppState() { return this.call('get_app_state'); },

    // Embedder
    initEmbedder() { return this.call('init_embedder'); },
    getEmbedderStatus() { return this.call('get_embedder_status'); },

    // Chrome
    setupChrome(identity) { return this.call('setup_chrome', identity); },
    stopChrome(identity) { return this.call('stop_chrome', identity); },
    checkLogin(identity) { return this.call('check_login', identity); },
    getChromeStatus(identity) { return this.call('get_chrome_status', identity); },
    autoDetectLogin(identity) { return this.call('auto_detect_login', identity); },

    // Settings
    getSettings() { return this.call('get_settings'); },
    saveSettings(s) { return this.call('save_settings', JSON.stringify(s)); },

    // Geek Search
    searchJobs(keyword, city, maxPages, filters) {
        return this.call('search_jobs', keyword, city || '', String(maxPages || 3), JSON.stringify(filters || {}));
    },
    getScrapeProgress() { return this.call('get_scrape_progress'); },
    getScrapedJobs(keyword, city, offset, limit) {
        return this.call('get_scraped_jobs', keyword || '', city || '', String(limit || 50), String(offset || 0));
    },
    getScrapedDetails(jobIds) {
        return this.call('get_scraped_details', JSON.stringify(jobIds || []));
    },
    listCities(keyword) { return this.call('list_cities', keyword || ''); },
    getFilterOptions() { return this.call('get_filter_options'); },
    cancelScrape() { return this.call('cancel_scrape'); },

    // AI Search Orchestration
    inferSearchConditions(resumeId) { return this.call('infer_search_conditions', resumeId ? String(resumeId) : 'null'); },

    // Resume
    saveResume(content) { return this.call('save_resume', content); },
    getResume() { return this.call('get_resume'); },
    uploadResume(filename, base64Content) { return this.call('upload_resume', filename, base64Content); },
    listResumes() { return this.call('list_resumes'); },
    getResumeById(id) { return this.call('get_resume_by_id', String(id)); },
    deleteResume(id) { return this.call('delete_resume', String(id)); },
    setActiveResume(id) { return this.call('set_active_resume', String(id)); },

    // Match
    startMatch(jobIds, supplements) {
        return this.call('start_match', JSON.stringify(jobIds), JSON.stringify(supplements || []));
    },
    getMatchProgress() { return this.call('get_match_progress'); },
    cancelMatch() { return this.call('cancel_match'); },
    getMatchResults(sourceId, limit, offset) {
        return this.call('get_match_results', String(sourceId || 1), String(limit || 50), String(offset || 0));
    },
    getMatchSummary() { return this.call('get_match_summary'); },
    uploadSupplement(filename, base64Content) {
        return this.call('upload_supplement', filename, base64Content);
    },

    // Pipeline
    startPipeline(resumeId, keyword, city, maxPages, filters, minScore, supplements) {
        return this.call('start_pipeline',
            resumeId ? String(resumeId) : 'null',
            keyword || '', city || '',
            String(maxPages || 3),
            JSON.stringify(filters || {}),
            String(minScore || 0),
            JSON.stringify(supplements || []));
    },
    getPipelineProgress() { return this.call('get_pipeline_progress'); },
    cancelPipeline() { return this.call('cancel_pipeline'); },
};
