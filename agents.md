# ScoutAI — Agent Architecture & Flow

> Detailed breakdown of each agent's role, inputs, outputs, and tools used in the pipeline.

---

## Pipeline Overview

```
                        ┌─────────────────┐
                        │   User Input     │
                        │  (Company URL)   │
                        └────────┬────────┘
                                 │
                                 ▼
                   ┌─────────────────────────┐
                   │    URL Parser Node       │
                   │    graph.py              │
                   └─────────────┬───────────┘
                                 │
                                 ▼
          ┌──────────────────────────────────────────┐
          │          Parallel Execution              │
          │      (ThreadPoolExecutor × 3)            │
          │                                          │
          │   ┌────────────┐  ┌────────────┐  ┌────────────┐
          │   │ Web Scraper│  │  External  │  │ Financial  │
          │   │   Agent    │  │ Researcher │  │  Analyst   │
          │   └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
          │         │               │                │
          └─────────┴───────────────┴────────────────┘
                                 │
                          (results merged)
                                 │
                                 ▼
                   ┌─────────────────────────┐
                   │   Synthesizer Node       │
                   │   (Groq LLM)            │
                   └─────────────┬───────────┘
                                 │
                                 ▼
                   ┌─────────────────────────┐
                   │       Final Output       │
                   │  Dashboard + Report      │
                   └─────────────────────────┘
```

---

## Node 1 — URL Parser

**File:** `graph.py` → `extract_company_info()`

**Responsibility:** Validate and normalize the input URL, extract the domain, and make an initial guess at the company name.

```
Input:  Raw URL string (e.g. "nvidia.com")
        │
        ├── Prepend https:// if missing
        ├── Parse scheme + netloc → domain
        └── Strip www., split on ".", title-case → company name guess

Output: url (normalized), domain, company_name (preliminary)
```

**Example:**
```
"nvidia.com"  →  url: "https://nvidia.com"
                 domain: "https://www.nvidia.com"
                 company_name: "Nvidia"
```

---

## Node 2 — Gather All Data (Orchestrator)

**File:** `graph.py` → `gather_all_data()`

**Responsibility:** Launch the three specialist agents in parallel using `ThreadPoolExecutor`, collect their results, handle exceptions gracefully, and merge everything into the shared state.

```
Input:  url, company_name, domain
        │
        ├── Thread 1 → Web Scraper Agent
        ├── Thread 2 → External Researcher Agent
        └── Thread 3 → Financial Analyst Agent
                │
                ▼ (all three finish)
        ├── Update company_name from scraper (more accurate than URL guess)
        ├── Merge website_content, external_research, financial_data text
        ├── Store raw_financial dict (charts + competitor data)
        └── Store news_items list

Output: website_content, external_research, financial_data,
        raw_financial, news_items, pages_scraped, errors
```

**Failure handling:** If any agent throws an exception, its result is replaced with an empty dict and the error is logged — the other agents continue unaffected.

---

## Agent A — Web Scraper

**File:** `agents/web_scraper.py`

**Responsibility:** Crawl the company's official website and extract clean text content for the LLM.

```
Input:  URL (company homepage)
        │
        ├── Fetch homepage with requests + BeautifulSoup
        ├── Score all internal links by keyword relevance
        │     (about, team, product, pricing, careers, etc.)
        ├── Pick top N pages (MAX_PAGES_TO_SCRAPE = 12)
        ├── For each page:
        │     ├── Fetch HTML
        │     ├── Strip nav, footer, scripts, ads
        │     ├── Extract clean body text
        │     └── Try to detect company name from <title> / <meta>
        └── Fallback: Playwright headless Chrome for JS-rendered sites

Output: {
  company_name: str,        # extracted from site metadata
  combined_text: str,       # all page text concatenated
  pages: list[dict],        # per-page title + text
  pages_scraped: int
}
```

**Tools:** `requests`, `beautifulsoup4`, `lxml`, `playwright` (fallback)

---

## Agent B — External Researcher

**File:** `agents/external_researcher.py`

**Responsibility:** Search the open web for information about the company that may not be on its own website — news, funding, competitors, reviews, LinkedIn, and market position.

```
Input:  company_name, domain
        │
        ├── Run 8 targeted DuckDuckGo text searches:
        │     1. "{company_name}" company overview funding revenue
        │     2. "{company_name}" LinkedIn employees leadership
        │     3. "{company_name}" competitors market share
        │     4. "{company_name}" news 2024 2025
        │     5. "{company_name}" product review customers
        │     6. "{company_name}" funding valuation investors
        │     7. "{company_name}" site:crunchbase.com OR site:pitchbook.com
        │     8. "{company_name}" technology stack engineering blog
        │
        ├── For each query: take top 3–5 results
        ├── Fetch snippet text from each result URL
        └── Concatenate all findings into combined_text

Output: {
  combined_text: str    # all external research concatenated
}
```

**Tools:** `duckduckgo-search`, `requests`, `beautifulsoup4`

---

## Agent C — Financial Analyst

**File:** `agents/financial_analyst.py`

**Responsibility:** Determine whether the company is publicly traded, fetch live financial data from Yahoo Finance, build chart-ready datasets, and identify sector competitors.

```
Input:  company_name
        │
        ├── ── Ticker Discovery ──────────────────────────────
        │     ├── Search Yahoo Finance autocomplete API
        │     ├── Match company_name to result
        │     └── Validate: fetch quote to confirm ticker is active
        │
        ├── ── Public Company Branch ────────────────────────
        │   (if ticker found)
        │     │
        │     ├── build_raw_data(ticker)
        │     │     └── Yahoo Finance quoteSummary modules:
        │     │           price · financialData · defaultKeyStatistics
        │     │           summaryProfile · recommendationTrend
        │     │         Extracts: market cap, revenue, net income, EBITDA,
        │     │           EPS, PE, forward PE, EV, EV/EBITDA, P/S,
        │     │           gross margin, operating margin, profit margin,
        │     │           ROE, revenue growth, D/E ratio, current ratio,
        │     │           free cashflow, operating cashflow, cash,
        │     │           52W high/low, analyst target, recommendation,
        │     │           sector, industry, employees, beta
        │     │
        │     ├── fetch_stock_history(ticker)
        │     │     └── Yahoo Finance /v8/finance/chart/
        │     │           1 year of daily OHLCV → dates + closes list
        │     │
        │     ├── fetch_quarterly_financials(ticker)
        │     │     └── Yahoo Finance timeseries API
        │     │           Quarterly revenue + net income (last 8 quarters)
        │     │
        │     ├── fetch_annual_financials(ticker)
        │     │     └── Yahoo Finance earnings module
        │     │           financialsChart.yearly → up to 4 years of
        │     │           revenue, net income, net margin, revenue CAGR
        │     │
        │     ├── fetch_news(ticker)
        │     │     └── Yahoo Finance news API
        │     │           Recent headlines → title, publisher, date, URL, thumbnail
        │     │
        │     └── find_and_fetch_competitors(company_name, ticker, sector)
        │           ├── Tier 1: Yahoo Finance recommendationsbyticker API
        │           ├── Tier 2: _SECTOR_PEERS dict (curated by sector/industry)
        │           └── Tier 3: DuckDuckGo → extract ticker patterns from URLs
        │               For each competitor ticker (max 3):
        │               fetch market cap, revenue, margins, P/E, ROE
        │
        └── ── Private Company Branch ───────────────────────
            (if no ticker found)
              └── DuckDuckGo: "{company_name}" funding valuation revenue
                  Return is_public=False, combined_text with funding info

Output: {
  is_public: bool,
  ticker: str,
  combined_text: str,           # text summary for LLM
  raw_data: dict,               # all metrics for dashboard cards
  stock_history: dict,          # dates + closes for stock chart
  quarterly: dict,              # quarterly revenue + NI
  annual: dict,                 # annual revenue, NI, margins, CAGR
  news_items: list[dict],       # recent headlines
  competitors: list[dict]       # sector peer metrics
}
```

**Tools:** `curl-cffi` (Chrome impersonation for Yahoo Finance), `duckduckgo-search`

---

## Node 3 — Synthesizer

**File:** `agents/synthesizer.py` → called from `graph.py` → `synthesize_report_node()`

**Responsibility:** Feed all gathered data into a Groq LLM and produce a structured Markdown intelligence report.

```
Input:  company_name, url,
        website_content    (from Web Scraper)
        external_research  (from External Researcher)
        financial_data     (from Financial Analyst — text summary)
        groq_api_key
        │
        ├── Build structured prompt:
        │     ├── System: "You are a senior business intelligence analyst..."
        │     ├── Inject all three data sources
        │     └── Request 5 specific report sections with headers
        │
        ├── Call Groq API: llama-3.3-70b-versatile
        │     max_tokens: 4096, temperature: 0.3
        │
        └── Return Markdown report string

Output: final_report: str   (Markdown, ~1500–3000 words)
```

**Report sections produced:**
1. Company Snapshot
2. Products & Services
3. Competitive Landscape
4. Recent News & Strategic Developments
5. Investment & Partnership Potential

**Tools:** `langchain-groq`, `langchain`

---

## State Object (shared across all nodes)

**File:** `graph.py` → `AgentState` TypedDict

```python
AgentState = {
  # Inputs
  url:               str,
  groq_api_key:      str,

  # Set by URL Parser
  company_name:      str,
  domain:            str,

  # Set by Gather All Data
  website_content:   str,
  external_research: str,
  financial_data:    str,
  raw_financial:     dict,   # chart-ready data for dashboard
  news_items:        list,
  pages_scraped:     int,

  # Set by Synthesizer
  final_report:      str,

  # Metadata
  progress:          list[str],
  errors:            list[str],
}
```

---

## File Map

```
ScoutAI/
├── app.py                    ← Gradio UI + chart builders
├── graph.py                  ← LangGraph pipeline + AgentState
├── config.py                 ← Global settings (MAX_PAGES, LLM model)
├── agents/
│   ├── web_scraper.py        ← Agent A
│   ├── external_researcher.py← Agent B
│   ├── financial_analyst.py  ← Agent C
│   └── synthesizer.py        ← LLM report writer
├── requirements.txt
├── README.md
└── agents.md                 ← This file
```
