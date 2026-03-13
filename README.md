---
title: ScoutAI
emoji: 🔍
colorFrom: violet
colorTo: purple
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
short_description: Smart company analyst agent — drop a URL, get a full intelligence report + live financial dashboard.
---

# 🔍 ScoutAI — Smart Company Analyst Agent

> **Drop any company URL. Get a full research report + live financial dashboard in under 90 seconds.**

---

## What It Does

ScoutAI is a team of AI agents that fully analyzes any company from just its website URL.
It crawls the official site, searches the web, pulls live financial data — then writes a structured
intelligence report using a free LLM, alongside a full investment-grade financial dashboard.

### Output Tabs

| Tab | Contents |
|---|---|
| 📈 Financial Dashboard | Metric cards · Stock chart · Revenue & Net Income · FCF · Margins · Balance sheet health · Competitor table · News feed |
| ⚔️ Competitive Landscape | LLM-written competitive analysis |
| 📰 News & Developments | LLM-written recent news & deals summary |

---

## Financial Dashboard (Public Companies)

- **12 metric cards** — Market Cap, Revenue TTM, Net Income, EBITDA, EPS, Enterprise Value, EV/EBITDA, P/S, Revenue Growth, ROE, Gross Margin, Operating Margin
- **1-year stock chart** with 52-week high/low annotations
- **Annual revenue & net income** — up to 4 years with YoY % growth and CAGR badge
- **Cash flow chart** — Operating CF + Free Cash Flow (annual trend or TTM snapshot)
- **Margin expansion chart** — Gross / Operating / Net margin over time
- **Balance sheet health** — Debt/Equity ratio, Current Ratio, FCF, Cash & Equivalents
- **Competitor comparison table** — side-by-side metrics vs sector peers
- **Live news feed** — recent headlines from Yahoo Finance

> Private companies show graceful fallback notices wherever public data is unavailable.

---

## Agent Architecture

See [agents.md](agents.md) for a detailed flowchart of each agent's responsibilities.

```
User Input (URL)
      │
      ▼
┌─────────────────────────┐
│   URL Parser Node        │  Parse domain, guess company name
└─────────────────────────┘
      │
      ▼
┌──────────────────────────────────────────────────┐
│           Parallel Data Gathering                │
│                                                  │
│  🕷  Web Scraper Agent                           │
│      Crawls up to 12 pages · BS4 + Playwright    │
│                                                  │
│  🌐  External Research Agent                    │
│      8 DuckDuckGo queries · news · LinkedIn      │
│      funding · reviews · competitors             │
│                                                  │
│  📊  Financial Analyst Agent                    │
│      yFinance · stock history · annual P&L       │
│      balance sheet · competitors · news         │
└──────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────┐
│   Synthesizer Node       │  Groq LLM (llama-3.3-70b-versatile)
│                          │  writes structured intelligence report
└─────────────────────────┘
      │
      ▼
  📊 Financial Dashboard  +  📄 Intelligence Report (downloadable)
```

**Orchestration:** LangGraph StateGraph
**LLM:** Groq — `llama-3.3-70b-versatile` *(completely free)*
**Web Research:** DuckDuckGo Search *(no API key needed)*
**Financial Data:** yFinance *(no API key needed)*
**Web Scraping:** requests + BeautifulSoup + Playwright fallback

---

## Getting Started

### 1. Get a Free Groq API Key
1. Go to **[console.groq.com](https://console.groq.com)**
2. Sign up for free — no credit card required
3. Click **Create API Key**
4. Paste the key into the app (or set `GROQ_API_KEY` as an environment variable)

### 2. Enter the Company URL
Paste any company website — e.g. `https://nvidia.com` or `https://stripe.com`

### 3. Click Analyze
The agents run in parallel. Full dashboard + report in ~30–90 seconds.

---

## Running Locally

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/ScoutAI
cd ScoutAI

# Virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser (optional — for JS-heavy sites)
playwright install chromium

# Set your Groq key
export GROQ_API_KEY=gsk_...     # Windows: set GROQ_API_KEY=gsk_...

# Launch
python app.py
# Open http://localhost:7860
```

---

## Tech Stack

| Component | Library | Cost |
|---|---|---|
| LLM | Groq `llama-3.3-70b-versatile` | Free |
| Agent orchestration | LangGraph | Free |
| Web scraping | BeautifulSoup + requests | Free |
| JS rendering | Playwright (optional) | Free |
| Web research | DuckDuckGo Search | Free |
| Financial data | yFinance + curl-cffi | Free |
| UI | Gradio 4.x | Free |

**Total API cost: $0.00**

---

## Use Cases

- **Sales teams** — research prospects before calls
- **Investors** — quick company due diligence
- **Job seekers** — understand a company before an interview
- **Analysts** — fast competitive market intelligence
- **Journalists** — rapid company background research
- **Founders** — analyze competitors in minutes

---

## Limitations

- Some websites block scrapers (Cloudflare-protected sites may return limited data)
- Financial charts are only available for publicly traded companies
- Private company financials are estimates sourced from web searches
- Report quality depends on the amount of public information available

---

*Built with LangGraph · Groq · DuckDuckGo · yFinance*
# ScoutAI--Smart-company-analyst-agent
