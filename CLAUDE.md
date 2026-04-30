# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium   # optional: JS-heavy / captcha-protected site fallback

# Run the app
export GROQ_API_KEY=gsk_...
python app.py                 # Gradio UI at http://localhost:7860

# Run evaluations (no formal test suite)
python eval/run_eval.py       # public company pipeline eval
python eval/startup_eval.py   # private/startup company eval
```

There is no lint config, build step, or test framework — the eval scripts in `eval/` serve as integration tests.

## Architecture

ScoutAI is a **LangGraph-orchestrated multi-agent pipeline** that aggregates web, research, and financial data then synthesizes an AI report via Groq.

### Pipeline (graph.py)

```
User input
    → extract_company_info()   # parse URL vs name, normalize domain
    → gather_all_data()        # 3 agents in parallel (ThreadPoolExecutor)
    → synthesize_report_node() # Groq LLM writes 5-section report
    → app.py renders dashboard
```

`AgentState` (TypedDict in `graph.py`) is the single shared state object passed between all nodes.

### Three parallel agents (agents/)

**web_scraper.py** — crawls the company's own website  
- Uses `curl_cffi` with Chrome TLS impersonation (`chrome131`) to bypass Cloudflare/bot detection  
- Scores and crawls up to 12 internal pages by keyword relevance  
- Fallback chain: curl_cffi → Playwright headless browser → DuckDuckGo to find the real URL

**external_researcher.py** — DuckDuckGo web research  
- Runs 6 sequential DDG text searches (overview, news, funding, competitors, reviews, LinkedIn)  
- Exponential backoff for rate limiting

**financial_analyst.py** — live market + historical filing data (~1,490 lines, most complex file)  
- **Do not restructure this file** — the fallback chain is carefully ordered and tested  
- Ticker resolution order: known-private shortlist → YF autocomplete API → DDG fallback  
- See "Financial Data Fallback Chain" section below for full detail

**synthesizer.py** — Groq LLM report generation  
- Trims inputs to ~16KB total (8KB website + 5KB research + 3KB financial) before calling LLM  
- Model: `llama-3.3-70b-versatile`, temperature 0.1, max 4096 tokens  
- Output: 5-section structured markdown (Company Snapshot, Business Model, Competitive Landscape, Recent News, Investment Outlook)  
- Shows a user-friendly message when Groq rate limit (429) is hit

### app.py

The Gradio UI entry point. Handles all chart rendering (Matplotlib, dark theme) — stock price history, revenue/net income bars, cash flow, margin trends — plus metric cards, trader scorecard, competitor table, news feed, and the markdown download button. Chart-building functions and the `run_pipeline()` call live here.

### config.py

Central settings: `MAX_PAGES_TO_SCRAPE=12`, `REQUEST_TIMEOUT=15s`, `REQUEST_DELAY=0.8s`, `MAX_SEARCH_RESULTS=6`, `GROQ_MODEL`, page keyword scoring list.

---

## Financial Data Fallback Chain (critical — do not break)

HuggingFace blocks Yahoo Finance crumb auth unpredictably. The pipeline has a 4-level fallback so the dashboard always shows maximum available data. **Each level must remain intact.**

### Level 1 — Full quoteSummary (primary, works locally + fresh HF containers)
- `fetch_quote_summary(ticker)` — hits YF v10 with 5 modules: `price, financialData, defaultKeyStatistics, assetProfile, summaryDetail`
- Requires crumb (`_get_crumb()`). Crumb is fetched once per session and cached in `_STATE["crumb"]`.
- `build_raw_data(ticker, qs)` — parses all ~40 fields from the 5 modules.
- If this returns a non-empty dict → use it, then apply Level 4 supplement.

### Level 2 — Lighter quoteSummary (new fallback, often works when full is blocked)
- `_fetch_financial_data_module(ticker)` — hits YF v10 with only 3 modules: `financialData, defaultKeyStatistics, assetProfile`
- Avoids the `price` and `summaryDetail` modules which trigger most HF blocks.
- Still uses `build_raw_data()` on the result — same parser, partial input.
- Provides: EBITDA, ROE, analyst target, D/E ratio, current ratio, FCF, forward P/E, sector, industry, employees.

### Level 3 — yfinance library → v8 chart + EDGAR
- `_build_raw_data_from_yf(ticker)` — uses `yfinance` library which has different auth mechanism.
- If `market_cap` is still N/A after yfinance → `_build_raw_data_from_v8_edgar(ticker, edgar_annual)`.
- v8+EDGAR is the **guaranteed floor** — neither requires Yahoo Finance auth:
  - v8 chart: current price, 52W high/low (no crumb needed)
  - EDGAR XBRL: revenue, margins, EPS, shares, cash, debt, current ratio, D/E, FCF, sector, industry
  - Derives: market cap (shares × v8 price), P/E (price ÷ EPS), P/S (market cap ÷ revenue)

### Level 4 — v7 quote supplement (runs always, fills remaining gaps)
- `_fetch_v7_quote(ticker)` → `_supplement_from_v7(raw_data, v7_data)`
- Called **after every level** (1, 2, or 3) to fill any remaining N/A fields.
- v7 provides: market cap, P/E, forward P/E, EPS, beta, price/book, PEG, sector, industry, analyst rating.
- `_supplement_from_v7` only overwrites fields that are still `"N/A"` — safe to call unconditionally.

### Data flow summary

```
fetch_quote_summary (Level 1)
    → succeeded? → build_raw_data → Level 4 supplement
    → blocked?
        → _fetch_financial_data_module (Level 2)
            → succeeded? → build_raw_data → Level 4 supplement
            → blocked?
                → _build_raw_data_from_yf (Level 3a — yfinance)
                    → market_cap still N/A?
                        → _build_raw_data_from_v8_edgar (Level 3b — guaranteed floor)
                → Level 4 supplement (always)
```

---

## SEC EDGAR Agent (agents/sec_edgar.py, ~527 lines)

Two EDGAR API endpoints are used:

1. **`/api/xbrl/companyfacts/CIK{padded}.json`** — XBRL financial facts  
   - Annual revenue, net income, gross profit, operating income, OCF (last 5 years)  
   - Point-in-time: EPS (annual 10-K, full-year only, ≥340 days), shares outstanding, cash, debt, current assets/liabilities, capex → FCF

2. **`/submissions/CIK{padded}.json`** — company metadata  
   - SIC code + SIC description → sector (via `_sic_to_sector()`) + industry string

### Key helpers in sec_edgar.py
- `_latest_value(units_dict, concept, forms, min_days)` — gets the most recently filed value; uses `(filed, end)` as sort key to correctly pick the most recent fiscal year when multiple comparative-period entries share the same filing date. Handles `USD`, `USD/shares`, `shares`, `pure` unit types.
- `_annual_values(gaap, concept)` — extracts 10-K annual time series.
- `fetch_company_metadata(cik)` — fetches `/submissions/` endpoint for SIC/sector data.
- `search_company(name)` — fuzzy name match against ~12,000 EDGAR companies (no ticker needed).

### Fields populated from EDGAR (guaranteed on HuggingFace)
Revenue (5yr), net income (5yr), gross/operating/net margins (5yr), operating CF (5yr), EPS, shares outstanding, cash, long-term debt, current ratio, D/E ratio, free cash flow, sector, industry.

---

## Key design patterns

- **No API keys in code** — `GROQ_API_KEY` must be set as environment variable; on HuggingFace Spaces it is a Space secret. Never hardcode keys.
- **Graceful degradation** — private companies skip financial charts and still get a full AI report from web + DDG data. Any scraping failure falls through to the next method.
- **curl_cffi everywhere** — used for both Yahoo Finance and web scraping to impersonate Chrome (`chrome131`); `yfinance` library is the secondary fallback for HuggingFace.
- **`_STATE` dict for session state** — `_STATE["session"]` and `_STATE["crumb"]` in financial_analyst.py avoid global variables and persist the YF session across calls. Do not replace with globals.
- **No nested ThreadPoolExecutor** — `get_financial_data()` is already called from graph.py's parallel executor. All YF + EDGAR calls inside it are sequential to avoid curl_cffi session conflicts.
- **Deployed on HuggingFace Spaces** — `README.md` has the HF metadata frontmatter; the live demo URL is in the README badges. Push to both `origin` (GitHub) and `hf` (HuggingFace) remotes.

## What is N/A even on the best path

These fields have no free public source when Yahoo Finance crumb is fully blocked:
- **EBITDA** — not in EDGAR XBRL as a single concept; would require calculation from income + depreciation statements
- **Enterprise Value** — requires market cap + debt - cash (debt/cash partially available now, but EV = market cap + total debt - cash + minority interest; market cap from EDGAR is approximate)
- **Forward P/E** — analyst estimate; no free public API
- **Beta** — requires historical price correlation vs index; no free public API
- **Analyst target price** — proprietary; no free public API
- **Short ratio** — FINRA data; no free public API

## Git remotes

```bash
git push origin main   # GitHub
git push hf main       # HuggingFace Spaces (triggers rebuild ~2 min)
```

Always push to both after any change.
