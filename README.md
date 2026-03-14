---
title: ScoutAI
emoji: 🔍
colorFrom: purple
colorTo: indigo
sdk: gradio
sdk_version: 5.29.0
app_file: app.py
pinned: false
short_description: Drop a URL. Get a full intelligence report + dashboard.
---

# 🔍 ScoutAI — Smart Company Analyst Agent

> **Drop any company URL. Get an investment-grade intelligence report with live financial charts in under 90 seconds.**

[![HuggingFace Space](https://img.shields.io/badge/🤗%20HuggingFace-Live%20Demo-yellow)](https://huggingface.co/spaces/Iyman-ahmed/ScoutAI-Smart-company-analyst-agent)
[![GitHub](https://img.shields.io/badge/GitHub-Source%20Code-181717?logo=github)](https://github.com/Iyman-Ahmed/ScoutAI--Smart-company-analyst-agent)
[![License: MIT](https://img.shields.io/badge/License-MIT-purple)](LICENSE)
[![Total Cost: $0](https://img.shields.io/badge/API%20Cost-$0.00-green)](https://console.groq.com)

---

## What It Does

ScoutAI is a **multi-agent AI system** that fully analyzes any company from a single website URL. Three specialized agents run in parallel — crawling the official site, researching the web, and pulling live financial data — before a Groq LLM synthesizes everything into a structured intelligence report.

All output lives on **one scrollable Full AI Report page**, organized as:

```
📊 12 Metric Cards        Market Cap · Revenue · Net Income · EBITDA · EPS ·
                          EV · EV/EBITDA · P/S · Rev Growth · ROE · Gross & Op Margin

📈 Stock Chart            1-year price history with 52W high/low annotations
📊 Revenue & Net Income   Up to 4 years annual data with CAGR badge
💰 Cash Flow Chart        Operating CF + Free Cash Flow (annual trend)
📉 Margin Expansion       Gross / Operating / Net margin over time

📊 Trader Scorecard       Signal chips (Bullish / Neutral / Bearish) ·
                          12 key trade metrics · Bullish Signals + Risk Flags

🏦 Balance Sheet Health   D/E Ratio · Current Ratio · FCF · Cash · Debt
⚔️  Competitor Table       Side-by-side metrics vs up to 3 sector peers
📰 News Feed              Live recent headlines from Yahoo Finance

📄 Full AI Report         LLM-written structured intelligence report
⬇  Download               Export full report as Markdown
```

> Private companies receive graceful fallback notices wherever public data is unavailable.

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

See [agents.md](agents.md) for a detailed flowchart of each agent's responsibilities.

```
User Input (URL)
      │
      ▼
┌─────────────────────────┐
│   URL Parser Node        │  Extract domain, infer company name
└─────────────────────────┘
      │
      ▼
┌──────────────────────────────────────────────────────────┐
│                  Parallel Data Gathering                  │
│                                                          │
│  🕷  Web Scraper Agent                                   │
│      Crawls up to 12 pages · BeautifulSoup + Playwright  │
│      Extracts: description, leadership, products,        │
│      pricing, news, about, careers                       │
│                                                          │
│  🌐  External Research Agent                             │
│      6 DuckDuckGo queries (no API key needed)            │
│      Covers: overview · news · funding · competitors     │
│               reviews · LinkedIn / team size             │
│                                                          │
│  📊  Financial Analyst Agent                             │
│      Yahoo Finance via Chrome impersonation              │
│      Fetches: stock · P&L · cash flow · balance sheet    │
│               trader metrics · competitors · news        │
└──────────────────────────────────────────────────────────┘
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
**Financial Data:** Yahoo Finance via `curl-cffi` Chrome impersonation *(no API key needed)*
**Web Scraping:** BeautifulSoup + requests + Playwright fallback

---

## Data Extracted

### Web Scraper
Company description · Products & services · Leadership team · Pricing · About / Mission · Careers

### External Research (DuckDuckGo)
Wikipedia overview · CEO & founders · Recent news (2025–2026) · Funding rounds & investors ·
ARR/revenue estimates · Competitor landscape · G2/Glassdoor reviews · LinkedIn employee count ·
Headquarters & founding year

### Financial Analyst (public companies only)

| Category | Fields |
|---|---|
| **Valuation** | Market Cap · P/E · Forward P/E · Enterprise Value · EV/EBITDA · P/S · P/B · PEG Ratio |
| **Profitability** | Revenue TTM · Net Income · EBITDA · Gross Margin · Operating Margin · Net Margin · ROE |
| **Per Share** | EPS TTM · EPS Forward · Shares Outstanding |
| **Cash Flow** | Free Cash Flow · Operating Cash Flow · Annual FCF trend |
| **Annual Trends** | Revenue · Net Income · EBITDA · Gross Profit (up to 4 years + CAGR) |
| **Balance Sheet** | Total Debt · Cash & Equivalents · Debt/Equity Ratio · Current Ratio |
| **Stock** | Current Price · 52W High/Low · 52W Return % · Beta · Dividend Yield · Payout Ratio |
| **Analyst** | Target Price (high/mean/low) · Recommendation · Analyst Count |
| **Trader Signals** | Short Ratio · Upside to Target % · PEG Signal · Overall Trade Signal |
| **Competitors** | Up to 3 sector peers — Market Cap · Revenue · Margins · P/E · ROE |
| **News** | Live headlines from Yahoo Finance |

---

## Getting Started

### On HuggingFace Spaces

No setup required — just paste a company URL and click **Analyze**. The Groq API key is pre-configured as a Space secret.

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

# 4. (Optional) Install Playwright for JS-heavy websites
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
| Web scraping | BeautifulSoup4 + requests | Free |
| JS rendering | Playwright (optional fallback) | Free |
| Web research | DuckDuckGo Search | Free |
| Financial data | Yahoo Finance + curl-cffi | Free |
| UI | Gradio 5.x | Free |
| Charts | Matplotlib | Free |

**Total API cost: $0.00**

---

## Use Cases

| Role | How ScoutAI Helps |
|---|---|
| **Traders & Investors** | Pre-trade due diligence — signals, valuation, short interest, analyst consensus |
| **Sales Teams** | Deep prospect research before outreach calls |
| **Founders** | Analyze competitors in minutes |
| **Job Seekers** | Understand a company's financials and culture before interviews |
| **Analysts** | Fast competitive market intelligence |
| **Journalists** | Rapid company background research |

---

## Limitations

- Some websites block automated scrapers (Cloudflare-protected sites may return limited data)
- Financial charts and trader metrics are only available for **publicly traded companies**
- Private company financials are estimates sourced from web searches
- Report quality scales with the amount of publicly available information
- Yahoo Finance data is subject to their availability and rate limits

---

## Security

- No API keys are stored in code or committed to this repository
- The Groq API key is passed at runtime via environment variable (`GROQ_API_KEY`)
- On HuggingFace Spaces, the key is stored as a **Space secret** (never visible to users)
- All financial data is fetched read-only from public sources

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

*Developed by [Iyman Ahmed](https://iymanahmed.tech) &nbsp;·&nbsp; [GitHub](https://github.com/Iyman-Ahmed/ScoutAI--Smart-company-analyst-agent) &nbsp;·&nbsp; [iyman12393@gmail.com](mailto:iyman12393@gmail.com)*

*Built with LangGraph · Groq · DuckDuckGo · Yahoo Finance · Gradio*
