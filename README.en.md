# BossMatch — AI-Powered BOSS Zhipin Two-Way Matching Desktop App

> 🌐 中文文档：[README.md](./README.md)

![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)
![Version](https://img.shields.io/badge/version-0.1.0-orange.svg)

BossMatch is an AI-powered job-matching desktop app that scrapes BOSS Zhipin listings via Chrome CDP, then uses **RAG intelligent matching** to find the best positions for you. Upload your resume and a local embedding model + vector retrieval + LLM will output a match score, evidence citations, gap analysis, and optimization suggestions for each job, along with a comprehensive job-search summary.

![BossMatch Main Interface](pics/48b2d95762c87e59147aaaf904fa4e46.png)

---

## ⚠️ Disclaimer

This project is for **personal learning and job-search research only**, designed to help job seekers efficiently match positions, understand their gaps, and optimize their job-search strategy. Do **not** use it for any purpose that violates the [BOSS Zhipin Terms of Service](https://www.zhipin.com/about/protocol.html) or applicable laws and regulations, including commercial resale, malicious scraping, or any activity that imposes undue load on the target site. Users are solely responsible for the consequences of using this project; the author is not liable for any misuse.

---

## ✨ Core Features

- **Chrome CDP Scraping** — Reuse real browser login session, call search API for plaintext salary
- **RAG Intelligent Matching** — Resume & JD semantic chunking → local embedding vectorization → ChromaDB retrieval → LLM scoring + suggestions
- **Three-Stage Pipeline** — Model initialization → index building → per-job scoring, real-time progress push
- **Streaming Summary** — After all jobs are scored, LLM streams a structured job-search analysis report
- **Resume File Parsing** — Support PDF / DOCX / TXT / MD upload, automatic text extraction
- **Supplementary Material Enhancement** — Upload interview experience, target company info, etc. as extra matching context
- **Dual Identity Mode** — Job Seeker (Geek) / Recruiter (Boss) switch with independent Chrome Profile & CDP port
- **Incremental Scraping** — Each page/detail is written to SQLite immediately; no data loss on crash; existing details are skipped
- **Multi-Dimension Filtering** — Salary, experience, degree, company scale, funding stage, industry
- **Nationwide Cities** — 300+ city codes, auto-sync latest BOSS city data at runtime
- **PyWebView Desktop App** — Native window + frontend UI, lightweight alternative to Electron

<details>
<summary>🔍 Why not Selenium / Playwright?</summary>

Selenium/Playwright spins up a full instrumented browser — it's heavy, has an obvious fingerprint, and is easily flagged by BOSS Zhipin's risk-control / CAPTCHA. BossMatch connects directly to your already-logged-in Chrome (CDP), reusing the real fingerprint and session, and calls the legitimate search API within the page. The `salaryDesc` returned is already plaintext. More stable than traditional DOM scraping and harder to detect as automated traffic.

</details>

---

## 🚀 Quick Start

### Installation

```bash
git clone git@github.com:LouXiaXiaoHei/boss-match.git
cd boss-match
pip install -r requirements.txt          # Minimal dependencies (CLI mode)
pip install -e .                          # Full install (desktop app + CLI)
```

### Launch the App

```bash
python3 src/app.py
# Or use the entry command after installation
pip install -e .
bossmatch
```

### Usage

1. **Login** — Click "Launch Chrome" in the app, then log in to BOSS Zhipin in the dedicated browser
2. **Search** — Enter keywords and city, select filters, start scraping jobs
3. **Match** — Upload your resume, select scraped jobs, start AI matching
4. **Review** — View match scores, evidence citations, gap analysis, and optimization suggestions for each job

![RAG Match Results](pics/bf64d3a8c927c01b82d9524ded809eb3.png)

### CLI Mode

Prefer the command line? Scrape and analyze directly:

```bash
# Launch isolated Chrome and log in
python3 scripts/boss_cdp_raw.py --setup-chrome

# Scrape jobs
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --analysis

# Generate aggregated summary
python3 scripts/job_summary.py --top 15

# List supported cities
python3 scripts/boss_cdp_raw.py --list-cities 江
```

---

## 📸 Screenshots

### Login Management

Launch the dedicated Chrome browser and log in to BOSS Zhipin. The app auto-detects login status; the embedding model downloads in the background.

### RAG Matching

Upload resume → select jobs → three-stage matching pipeline with real-time progress. Each job outputs a score, evidence, gaps, and suggestions.

![Match Results](pics/bf64d3a8c927c01b82d9524ded809eb3.png)

### Match Summary

After all jobs are scored, the LLM streams a structured analysis report with overall match trends, skill-gap summaries, and job-search optimization directions.

![Match Summary](pics/e8f542c207e2fbb4fc9216e2a3bfa10c.png)

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────┐
│                    BossMatch App                      │
│                  (PyWebView Desktop)                  │
├──────────┬──────────┬──────────┬──────────┬──────────┤
│  Login   │  Search  │  Match   │  Summary │ Settings │
│   Page   │   Page   │   Page   │   Page   │   Page   │
└────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┘
     │          │          │          │          │
     ▼          ▼          ▼          ▼          ▼
┌──────────────────────────────────────────────────────┐
│                   AppAPI (Bridge)                     │
│              JS ↔ Python API Bridge                   │
├─────────┬──────────────────┬─────────────────────────┤
│ Chrome  │    GeekAPI       │      Matcher            │
│ Manager │  (Scrape Orch.)  │   (RAG Orchestrator)    │
├─────────┼──────────────────┼──────┬──────┬───────────┤
│  CDP    │   Scraper        │Embed │Vector│   LLM     │
│ Session │  (List+Detail)   │der   │Store │  Client   │
├─────────┼──────────────────┼──────┼──────┼───────────┤
│Profile  │  City/Constants  │Chunker│Retriever│Prompts │
│Manager  │  ResumeParser    │EventBus│Summarizer│      │
└─────────┴──────────────────┴──────┴──────┴───────────┘
                      │
                ┌─────┴─────┐
                │  SQLite   │
                │ Database  │
                └───────────┘
```

### RAG Matching Pipeline

```
Resume + JD + Supplementary Materials
        │
        ▼
   ┌─────────┐     Phase 0
   │ Chunker │ ────────────── Model Initialization
   └────┬────┘
        │ Semantic Chunking
        ▼
   ┌──────────┐    Phase 1
   │ Embedder │ ────────────── Index Building
   │(bge-small│
   │  -zh)    │
   └────┬─────┘
        │ Vectorization → ChromaDB
        ▼
   ┌──────────┐    Phase 2
   │ Retriever│ ────────────── Per-Job Scoring
   │ + LLM    │
   └────┬─────┘
        │ Score + Evidence + Suggestions
        ▼
   ┌──────────┐    Phase 3
   │Summarizer│ ────────────── Comprehensive Summary
   │ (Stream) │
   └──────────┘
```

---

## 📁 Project Structure

```
boss-match/
├── src/
│   ├── app.py                    # PyWebView app entry point
│   ├── api/
│   │   ├── bridge.py             # JS ↔ Python API bridge
│   │   └── geek_api.py           # Job seeker search/scrape orchestration
│   ├── ai/
│   │   ├── matcher.py            # RAG matching three-stage orchestrator
│   │   ├── embedder.py           # Local embedding model (bge-small-zh)
│   │   ├── chunker.py            # Resume/JD semantic chunking
│   │   ├── vector_store.py       # ChromaDB vector storage
│   │   ├── retriever.py          # Vector retrieval + context assembly
│   │   ├── client.py             # OpenAI-compatible LLM client
│   │   ├── summarizer.py         # Streaming summary generation
│   │   ├── prompts.py            # Prompt templates
│   │   └── event_bus.py          # Matching event bus
│   ├── core/
│   │   ├── cdp.py                # Chrome DevTools Protocol session
│   │   ├── chrome.py             # Chrome launch/stop + CDP connection
│   │   ├── scraper.py            # List + detail page scraping
│   │   ├── detail.py             # Detail page JD extraction
│   │   ├── city.py               # City code parsing (300+ cities)
│   │   ├── constants.py          # Filter parameter mapping + constants
│   │   ├── login.py              # Login state detection
│   │   ├── js_templates.py       # CDP-injected JS templates
│   │   └── resume_parser.py      # Resume file parsing (PDF/DOCX/TXT/MD)
│   ├── db/
│   │   ├── database.py           # SQLite schema + connection management
│   │   └── repository.py         # Data access layer
│   └── identity/
│       └── profile_manager.py    # Dual-identity Chrome Profile management
├── frontend/
│   ├── index.html                # Single-page app entry
│   ├── css/style.css             # Global styles
│   └── js/
│       ├── api.js                # Backend API call wrapper
│       └── app.js                # Frontend routing + page rendering + state management
├── scripts/
│   ├── boss_cdp_raw.py           # CLI scraping main script
│   └── job_summary.py            # Post-scrape aggregated summary
├── data/
│   └── city_codes.json           # Nationwide city codes
├── pics/                         # README screenshots
├── pyproject.toml                # Project metadata + dependencies
└── requirements.txt              # Minimal CLI dependencies
```

---

## 🔧 Tech Stack

| Layer | Technology |
|-------|------------|
| Desktop Framework | PyWebView 5.x |
| Frontend | Vanilla HTML/CSS/JS + Vditor (Markdown editor) |
| Backend | Python 3.10+ |
| Database | SQLite |
| Embedding Model | BAAI/bge-small-zh-v1.5 (sentence-transformers) |
| Vector Database | ChromaDB |
| LLM | OpenAI-compatible API (gpt-4o / any compatible model) |
| Browser Automation | Chrome DevTools Protocol (websocket-client) |
| Resume Parsing | pypdf + python-docx |

---

## 📖 Core Features

### Login Management

- Launch/close dedicated Chrome browser (independent Profile, does not affect main browser)
- Auto-detect login status (30-minute cache TTL)
- Embedding model downloads in the background with real-time progress
- Job seeker / recruiter dual identity switching

### Job Search

- Keyword + city + multi-dimension filters (salary/experience/degree/scale/funding/industry)
- City auto-complete (300+ cities, Chinese search supported)
- Background thread scraping with real-time progress (list → detail)
- Incremental save: each list page and detail is written to SQLite immediately
- Existing details are skipped automatically to avoid duplicate scraping
- Click job card for details (JD + skill tags + Boss activity status)

### AI Matching (RAG)

1. **Phase 0 — Model Initialization**: Download and load bge-small-zh-v1.5 embedding model
2. **Phase 1 — Index Building**: Resume/JD/supplementary material semantic chunking → batch vectorization → write to ChromaDB
3. **Phase 2 — Per-Job Scoring**: Retrieve relevant chunks for each job → assemble context → LLM outputs score/evidence/gaps/suggestions
4. **Phase 3 — Comprehensive Summary**: LLM streams a structured job-search analysis report

Each job's matching result includes:
- **Score** (0-100)
- **Evidence Citations** — Specific resume content matching the job
- **Gap Analysis** — Gaps between the resume and JD
- **Optimization Suggestions** — Specific improvement directions for this job
- **Retrieval Context** — Most relevant text chunks from RAG retrieval

### Resume & Supplementary Materials

- Support PDF / DOCX / TXT / MD file upload with automatic text extraction
- Markdown editor (Vditor) for online resume editing
- Upload multiple supplementary materials (interview experience, target company info, etc.) to enhance matching

---

## ⚙️ Settings

The app has a built-in settings page for configuring LLM connections:

| Setting | Description | Default |
|---------|-------------|---------|
| Identity Mode | Job Seeker / Recruiter | Job Seeker |
| API Base URL | OpenAI-compatible API address | https://api.openai.com/v1 |
| API Key | LLM authentication key | — |
| API Model | Model to use | gpt-4o |

> For users in China: the embedding model downloads from hf-mirror.com by default. You can also manually download it and place it in `~/.boss-match/models/manual/BAAI_bge-small-zh-v1.5/`.

---

## 🛡️ Chrome Profile Security

- `--setup-chrome` uses a persistent isolated Profile (`~/.boss-match/chrome-profile-{geek|boss}/`), does not copy main Chrome data
- Geek (9222) and Boss (9223) use independent CDP ports without interference
- `--stop-chrome` precisely matches and closes by isolated Profile, never touches main Chrome
- Login probing uses keyword rotation + exponential backoff, stops immediately on risk-control detection

---

## 📌 TODO

- [ ] Intelligent Chat — Q&A about matching results
- [ ] Resume Optimization — AI rewrites resume targeting specific jobs
- [ ] Interview Preparation — Generate job-related interview questions and answer strategies
- [ ] Salary Negotiation — Salary suggestions based on market data
- [ ] Detail page Referer hardening

---

## License

MIT

## Acknowledgements

This project was inspired by [eatmoreduck/boss-zhipin-scraper](https://github.com/eatmoreduck/boss-zhipin-scraper). Thanks to the original author for their open-source contribution.

## Friends

- [LINUX DO](https://linux.do/) — A sincere, friendly, and vibrant tech community. This project endorses and recommends it.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=LouXiaXiaoHei/boss-match&type=Date)](https://star-history.com/#LouXiaXiaoHei/boss-match&Date)
