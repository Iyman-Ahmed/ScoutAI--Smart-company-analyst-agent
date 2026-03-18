---
title: ScoutAI
emoji: 🔍
colorFrom: purple
colorTo: indigo
sdk: gradio
sdk_version: 5.29.0
app_file: app.py
pinned: false
short_description: Type a company name or URL. Get a full intelligence report + dashboard.
---

# 🔍 ScoutAI — Smart Company Analyst Agent

> **Type any company name or paste a URL. Get an investment-grade intelligence report with live financial charts in under 90 seconds.**

[![HuggingFace Space](https://img.shields.io/badge/🤗%20HuggingFace-Live%20Demo-yellow)](https://huggingface.co/spaces/Iyman-ahmed/ScoutAI-Smart-company-analyst-agent)
[![GitHub](https://img.shields.io/badge/GitHub-Source%20Code-181717?logo=github)](https://github.com/Iyman-Ahmed/ScoutAI--Smart-company-analyst-agent)
[![License: MIT](https://img.shields.io/badge/License-MIT-purple)](LICENSE)
[![Total Cost: $0](https://img.shields.io/badge/API%20Cost-$0.00-green)](https://console.groq.com)

---

## What It Does

ScoutAI is a **multi-agent AI system** that fully analyzes any company from a company name or website URL. Three specialized agents run in parallel — crawling the official site, researching the web, and pulling live financial data — before a Groq LLM synthesizes everything into a structured intelligence report.

All output lives on **one scrollable Full AI Report page**, organized as:

```
📊 12 Metric Cards        Market Cap · Revenue · Net Income · EBITDA · EPS ·
                          EV · EV/EBITDA · P/S · Rev Growth · ROE · Gross & Op Margin

📈 Stock Chart            1-year price history with 52W high/low annotations
📊 Revenue & Net Income   Up to 10 years annual data (from SEC EDGAR) with CAGR badge
💰 Cash Flow Chart        Operating CF + Free Cash Flow (annual trend)
📉 Margin Expansion       Gross / Operating / Net margin over time

📊 Trader Scorecard       Signal chips (Bullish / Neutral / Bearish) ·
                          12 key trade metrics · Bullish Signals + Risk Flags

🏦 Balance Sheet Health   D/E Ratio · Current Ratio · FCF · Cash · Debt
⚔️  Competitor Table       Side-by-side metrics vs up to 3 sector peers
📰 News Feed              Live recent headlines from Yahoo Finance + DuckDuckGo

📄 Full AI Report         LLM-written structured intelligence report
⬇  Download               Export full report as Markdown
```

> Private companies and non-US companies receive graceful fallback handling — the AI report still runs using web-scraped and DuckDuckGo data.

---

## Trader Scorecard

Built for traders and investors who need fast, signal-driven data before taking a position.

**Four signal chips** computed automatically:

| Signal | Logic |
|---|---|
| Overall Signal | Composite of all signals below (🟢 Bullish / 🟡 Neutral / 🔴 Bearish) |
| Valuation | P/E + PEG scoring — Undervalued / Fairly Valued / Expensive |
| 52W Momentum | 52-week return — Strong Bull → Strong Bear |
| Analyst Verdict | Consensus from analyst opinions — Strong Buy → Strong Sell |

**12 key trade metric cards**: PEG Ratio · Short Ratio · 52W Return · Upside to Target · Price/Book · EV/EBITDA · Beta · Analyst Count · EPS TTM · EPS Forward · Dividend Yield · Payout Ratio

**Bullish Signals + Risk Flags** — auto-generated plain-English callouts, e.g.:
- ✅ PEG 0.8 — growing faster than it costs
- ⚠️ Short ratio 7.2 — heavy short pressure

---

## Agent Architecture

```
User Input (URL)
      │
      ▼
┌─────────────────────────┐
│   URL Parser Node        │  Extract domain, infer company name
└─────────────────────────┘
      │
      ▼
┌──────────────────────────────────────────────────────────────┐
│                    Parallel Data Gathering                    │
│                                                              │
│  🕷  Web Scraper Agent                                       │
│      curl_cffi Chrome impersonation (bypasses Cloudflare)    │
│      Playwright fallback for JS-heavy / captcha sites        │
│      Crawls up to 12 pages · extracts description,          │
│      leadership, products, pricing, news, about, careers     │
│                                                              │
│  🌐  External Research Agent                                 │
│      6 DuckDuckGo queries (no API key needed)                │
│      Covers: overview · news · funding · competitors         │
│               reviews · LinkedIn / team size                 │
│                                                              │
│  📊  Financial Analyst Agent  ──────────────────────────┐   │
│      Two sub-sources run concurrently:                   │   │
│                                                          │   │
│      📈 Yahoo Finance (Chrome impersonation)             │   │
│         Stock price · P/E · Margins · TTM metrics        │   │
│         Valuation multiples · Trader signals             │   │
│         Works for any globally listed company            │   │
│                                                          │   │
│      🏛  SEC EDGAR (official SEC filings — free)         │   │
│         Searches ~12,000 companies by name (no ticker)   │   │
│         10-K annual revenue · net income · margins       │   │
│         Historical data going back 10+ years             │   │
│         Works for any SEC-registered US company          │   │
│      ──────────────────────────────────────────────────  │   │
│      Results merged: EDGAR for history + YF for live     │   │
│                                                     ─────┘   │
└──────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────┐
│   Synthesizer Node       │  Groq LLM (llama-3.3-70b-versatile)
│                          │  Writes full structured intelligence report
└─────────────────────────┘
      │
      ▼
  📊 Full AI Report  (single-page dashboard + LLM report)
  ⬇  Downloadable as Markdown
```

**Orchestration:** LangGraph StateGraph
**LLM:** Groq — `llama-3.3-70b-versatile` *(completely free)*
**Web Research:** DuckDuckGo Search *(no API key needed)*
**Financial Data:** Yahoo Finance + SEC EDGAR *(both free, no API key)*
**Web Scraping:** curl_cffi Chrome impersonation + Playwright fallback

---

## Universal Company Coverage

ScoutAI works for **any company** — type the name or paste a URL, no hardcoded list required.

**Input formats accepted:**
```
Nvidia                       ← plain company name
Upwork                       ← works for small/mid-cap too
https://shopify.com          ← full URL
shopify.com                  ← bare domain
```

| Type | How it works |
|---|---|
| **Any US public company** | SEC EDGAR name search → 10-K annual filings (revenue, net income, margins) |
| **Any globally listed company** | Yahoo Finance autocomplete API → ticker → full market data |
| **Small/mid-cap companies** | Same pipeline — Upwork, Fiverr, Duolingo, Monday.com, etc. all work |
| **Private companies** | Web scraper + DuckDuckGo research + LLM report (no financial charts) |
| **Cloudflare/captcha websites** | curl_cffi Chrome TLS impersonation + Playwright headless browser fallback |
| **No website available** | Financial data still fetched from Yahoo Finance + SEC EDGAR by company name |

**Ticker resolution priority:**
1. Known-private shortlist (OpenAI, Stripe, etc.) — skip lookup immediately
2. Yahoo Finance autocomplete API — fast, works for any exchange worldwide
3. SEC EDGAR company list — cross-reference for US companies
4. DuckDuckGo text search — last resort

---

## Data Extracted

### Web Scraper
Company description · Products & services · Leadership team · Pricing · About / Mission · Careers

### External Research (DuckDuckGo)
Wikipedia overview · CEO & founders · Recent news (2025–2026) · Funding rounds & investors ·
ARR/revenue estimates · Competitor landscape · G2/Glassdoor reviews · LinkedIn employee count ·
Headquarters & founding year

### Financial Analyst (public companies)

| Category | Fields | Source |
|---|---|---|
| **Valuation** | Market Cap · P/E · Forward P/E · Enterprise Value · EV/EBITDA · P/S · P/B · PEG Ratio | Yahoo Finance |
| **Profitability** | Revenue TTM · Net Income · EBITDA · Gross Margin · Operating Margin · Net Margin · ROE | Yahoo Finance |
| **Per Share** | EPS TTM · EPS Forward · Shares Outstanding | Yahoo Finance |
| **Cash Flow** | Free Cash Flow · Operating Cash Flow | Yahoo Finance |
| **Annual Trends** | Revenue · Net Income · Net Margin (up to 10 years + CAGR) | **SEC EDGAR** (primary) |
| **Balance Sheet** | Total Debt · Cash & Equivalents · D/E Ratio · Current Ratio | Yahoo Finance |
| **Stock** | Price · 52W High/Low · 52W Return % · Beta · Dividend Yield · Payout Ratio | Yahoo Finance |
| **Analyst** | Target Price (high/mean/low) · Recommendation · Analyst Count | Yahoo Finance |
| **Trader Signals** | Short Ratio · Upside to Target % · PEG Signal · Overall Signal | Yahoo Finance |
| **Competitors** | Up to 3 sector peers — Market Cap · Revenue · Margins · P/E · ROE | Yahoo Finance |
| **News** | Live headlines | Yahoo Finance + DuckDuckGo |

---

## Getting Started

### On HuggingFace Spaces

No setup required — type a company name or paste a URL and click **Analyze**. The Groq API key is pre-configured as a Space secret.

[**→ Try it live on HuggingFace**](https://huggingface.co/spaces/Iyman-ahmed/ScoutAI-Smart-company-analyst-agent)

### Running Locally

You need a **free** Groq API key (no credit card required):

1. Go to [console.groq.com](https://console.groq.com)
2. Sign up → **Create API Key**
3. Copy the key (starts with `gsk_...`)

```bash
# 1. Clone the repository
git clone https://github.com/Iyman-Ahmed/ScoutAI--Smart-company-analyst-agent.git
cd ScoutAI--Smart-company-analyst-agent

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Install Playwright for JS-heavy / captcha-protected websites
playwright install chromium

# 5. Set your Groq API key
export GROQ_API_KEY=gsk_...       # Windows: set GROQ_API_KEY=gsk_...

# 6. Launch
python app.py
# → Open http://localhost:7860
```

---

## Tech Stack

| Component | Library | Cost |
|---|---|---|
| LLM | Groq `llama-3.3-70b-versatile` | Free |
| Agent orchestration | LangGraph StateGraph | Free |
| Web scraping | curl_cffi Chrome impersonation + BeautifulSoup4 | Free |
| JS / captcha bypass | Playwright (optional fallback) | Free |
| Web research | DuckDuckGo Search | Free |
| Financial data (live) | Yahoo Finance via curl_cffi | Free |
| Financial data (historical) | SEC EDGAR XBRL API | Free |
| UI | Gradio 5.x | Free |
| Charts | Matplotlib | Free |

**Total API cost: $0.00**

---

## Use Cases

| Role | How ScoutAI Helps |
|---|---|
| **Traders & Investors** | Pre-trade due diligence — signals, valuation, short interest, analyst consensus |
| **Sales Teams** | Deep prospect research before outreach calls |
| **Founders** | Analyze any competitor in minutes — even small ones |
| **Job Seekers** | Understand a company's financials and culture before interviews |
| **Analysts** | Fast competitive market intelligence with 10+ years of official data |
| **Journalists** | Rapid company background research |

---

## Limitations

- Balance sheet health and trader metrics require a Yahoo Finance listing (some non-US companies may have limited data)
- Private company financials are estimates sourced from web searches (no charts)
- SEC EDGAR covers US-registered companies only; international companies use Yahoo Finance only
- Some websites may still block scraping even with Chrome impersonation (very aggressive WAFs)
- Report quality scales with the amount of publicly available information

---

## Security

- No API keys are stored in code or committed to this repository
- The Groq API key is passed at runtime via environment variable (`GROQ_API_KEY`)
- On HuggingFace Spaces, the key is stored as a **Space secret** (never visible to users)
- All financial data is fetched read-only from public sources (Yahoo Finance, SEC EDGAR)

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

*Developed by [Iyman Ahmed](https://iymanahmed.tech) &nbsp;·&nbsp; [GitHub](https://github.com/Iyman-Ahmed/ScoutAI--Smart-company-analyst-agent) &nbsp;·&nbsp; [iyman12393@gmail.com](mailto:iyman12393@gmail.com)*

*Built with LangGraph · Groq · DuckDuckGo · Yahoo Finance · SEC EDGAR · Gradio*
