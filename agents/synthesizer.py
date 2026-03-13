"""
Report Synthesizer Agent
-------------------------
Uses Groq LLM to combine all gathered data into a structured 5-section
company intelligence report with year-over-year comparisons.
Model: llama-3.3-70b-versatile via Groq API (free at console.groq.com)
"""

import logging
from datetime import datetime

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage

from config import GROQ_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior business intelligence analyst at a top-tier investment bank.
Write a concise, data-driven company intelligence report in exactly 5 sections.

Rules:
- Use ONLY facts from the provided data. Never hallucinate numbers or dates.
- If data is missing for a field, write a short honest note — do NOT pad with filler.
- Use tables and bullet points for comparisons. Avoid long paragraphs.
- Year-over-year (YoY) comparisons must use specific years (e.g. 2023 vs 2024), not vague language.
- Each section must be dense with information — no fluff, no filler sentences.
- Tone: professional, analytical, like a Goldman Sachs equity research note.
"""

REPORT_TEMPLATE = """
DATA AVAILABLE:

== WEBSITE / PRODUCT INFO ==
{website_content}

== EXTERNAL RESEARCH (news, funding, competitors, LinkedIn) ==
{external_research}

== FINANCIAL DATA ==
{financial_data}

---

Write a Company Intelligence Report for **{company_name}** ({url}).
Use EXACTLY these 5 sections with ## headers. Be data-dense and specific.

## 1. Company Snapshot
- One-line description, founding year, HQ, headcount, website
- Core products/services with brief descriptions (3-5 bullet points)
- Business model: how they make money (SaaS/marketplace/licensing/services)
- Key leadership: CEO, CTO, founders (name + brief background)
- Target market: B2B/B2C, industries, geographies, company size

## 2. Business Model & Revenue Deep-Dive
- Revenue streams broken down with estimated % contribution if known
- Pricing model (subscription tiers, enterprise contracts, usage-based, etc.)
- Key clients / notable customers and partnerships
- Revenue metrics: ARR, MRR, GMV, or whatever is applicable
- YoY revenue or ARR growth: create a comparison table if multiple years of data exist
  Example table format:
  | Year | Revenue | Growth |
  |------|---------|--------|
  | 2022 | $X | — |
  | 2023 | $X | +Y% |
  | 2024 | $X | +Y% |

## 3. Competitive Landscape
- Top 3-5 direct competitors with one-line description of each
- Side-by-side comparison table (use whatever metrics are available):
  | Company | Market Cap / Valuation | Revenue | Key Differentiator |
  |---------|----------------------|---------|-------------------|
- Where this company wins vs. where competitors win
- Market share or positioning (leader / challenger / niche player)

## 4. Recent News & Strategic Developments
- Bullet-point timeline of major events (product launches, M&A, funding, partnerships) from the last 12-18 months — include specific dates/months
- YoY comparison of growth initiatives (what changed from 2023 to 2024 to 2025?)
- Any regulatory, legal, or macroeconomic headwinds/tailwinds
- Upcoming catalysts or announced roadmap items

## 5. Investment & Risk Outlook
- Bull case: 3 key reasons to be optimistic (with data points)
- Bear case: 3 key risks or concerns (with data points)
- Overall signal: Strong Buy / Buy / Hold / Watch / Avoid — with one-sentence rationale
- Key metrics to monitor going forward

Start the report with ONLY this header (no preamble):
# {company_name} — Intelligence Report
*{date} | Source: {url}*

End with:
---
*CompanyRadar — AI Business Intelligence*
"""


def synthesize_report(
    company_name: str,
    url: str,
    website_content: str,
    external_research: str,
    financial_data: str,
    groq_api_key: str,
) -> str:
    if not groq_api_key:
        return "**Error:** No Groq API key provided. Get a free key at https://console.groq.com"

    llm = ChatGroq(
        model=GROQ_MODEL,
        api_key=groq_api_key,
        temperature=0.1,
        max_tokens=4096,
    )

    website_trimmed = website_content[:8000] if len(website_content) > 8000 else website_content
    external_trimmed = external_research[:5000] if len(external_research) > 5000 else external_research
    financial_trimmed = financial_data[:3000] if len(financial_data) > 3000 else financial_data

    prompt = REPORT_TEMPLATE.format(
        company_name=company_name,
        website_content=website_trimmed or "Not available.",
        external_research=external_trimmed or "Not available.",
        financial_data=financial_trimmed or "Not available.",
        date=datetime.now().strftime("%B %d, %Y"),
        url=url,
    )

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]

    try:
        response = llm.invoke(messages)
        return response.content
    except Exception as e:
        logger.error(f"LLM synthesis failed: {e}")
        return f"**Report generation failed:** {str(e)}\n\nPlease check your Groq API key and try again."
