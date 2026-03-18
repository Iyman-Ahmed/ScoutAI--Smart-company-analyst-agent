"""
LangGraph Orchestration
-----------------------
Defines the multi-agent pipeline as a state graph:

  [START]
    ↓
  extract_company_info       — parse URL, get domain & initial company name
    ↓
  gather_all_data            — runs 3 agents in parallel (async):
    ├── web_scraper          — crawl & scrape the website
    ├── external_researcher  — DuckDuckGo searches
    └── financial_analyst    — stock / funding data
    ↓
  synthesize_report          — LLM creates final structured report
    ↓
  [END]
"""

import asyncio
import logging
import re
from typing import TypedDict
from urllib.parse import urlparse

from langgraph.graph import StateGraph, END

from agents.web_scraper import scrape_website
from agents.external_researcher import research_external
from agents.financial_analyst import get_financial_data
from agents.synthesizer import synthesize_report

logger = logging.getLogger(__name__)


# ─── State Definition ────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # Inputs
    url: str
    groq_api_key: str

    # Intermediate data
    company_name: str
    domain: str
    website_content: str
    external_research: str
    financial_data: str
    raw_financial: dict     # chart-ready: ticker, stock_history, quarterly, raw_data
    news_items: list        # recent news/deals from Yahoo Finance / DDG
    pages_scraped: int

    # Output
    final_report: str

    # Progress & errors
    progress: list[str]
    errors: list[str]


# ─── Node: Extract Company Info ───────────────────────────────────────────────

def _looks_like_url(text: str) -> bool:
    """Return True if the input looks like a URL or domain, not a plain company name."""
    t = text.strip().lower()
    if t.startswith(("http://", "https://")):
        return True
    # Bare domain: contains a dot with a known TLD after it, no spaces
    if " " not in t and re.search(r'\.[a-z]{2,6}(/|$)', t):
        return True
    return False


def extract_company_info(state: AgentState) -> AgentState:
    """
    Parse input — accepts either a website URL (https://nvidia.com)
    or a plain company name (Nvidia, Upwork, Shopify).
    """
    raw = state["url"].strip()
    progress = state.get("progress", [])

    if _looks_like_url(raw):
        # ── URL / domain mode ──────────────────────────────────────────────
        url = raw if raw.startswith(("http://", "https://")) else "https://" + raw
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        # Guess company name from domain (e.g. stripe.com → Stripe)
        netloc = parsed.netloc.replace("www.", "")
        company_name = netloc.split(".")[0].replace("-", " ").replace("_", " ").title()
        progress.append(f"Analyzing URL: {url}")
    else:
        # ── Company name mode ──────────────────────────────────────────────
        company_name = raw.title() if raw.islower() else raw
        # Build a best-guess URL; web scraper will use DDG to find the real one
        slug = re.sub(r'[^a-z0-9]', '', company_name.lower())
        url = f"https://www.{slug}.com"
        domain = url
        progress.append(f"Company name input: {company_name}")

    return {
        **state,
        "url": url,
        "domain": domain,
        "company_name": company_name,
        "progress": progress,
    }


# ─── Node: Gather All Data (parallel async) ──────────────────────────────────

def gather_all_data(state: AgentState) -> AgentState:
    """
    Run web scraper, external researcher, and financial analyst in parallel.
    Falls back to sequential if async is unavailable.
    """
    url = state["url"]
    company_name = state["company_name"]
    domain = state["domain"]
    progress = state.get("progress", [])
    errors = state.get("errors", [])

    async def _run_parallel():
        loop = asyncio.get_event_loop()
        web_task = loop.run_in_executor(None, scrape_website, url)
        ext_task = loop.run_in_executor(None, research_external, company_name, domain)
        fin_task = loop.run_in_executor(None, get_financial_data, company_name)
        return await asyncio.gather(web_task, ext_task, fin_task, return_exceptions=True)

    try:
        # Try to run parallel
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already inside an event loop (e.g. Jupyter / Gradio async context)
            # Use concurrent.futures instead
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=3) as executor:
                web_future = executor.submit(scrape_website, url)
                ext_future = executor.submit(research_external, company_name, domain)
                fin_future = executor.submit(get_financial_data, company_name)
                web_result = web_future.result()
                ext_result = ext_future.result()
                fin_result = fin_future.result()
        else:
            # No running loop — use asyncio.run
            web_result, ext_result, fin_result = asyncio.run(_run_parallel())

    except Exception as e:
        logger.error(f"Parallel execution failed, falling back to sequential: {e}")
        web_result = scrape_website(url)
        ext_result = research_external(company_name, domain)
        fin_result = get_financial_data(company_name)

    # Handle exceptions from gather
    if isinstance(web_result, Exception):
        errors.append(f"Web scraper error: {web_result}")
        web_result = {"company_name": company_name, "pages": [], "combined_text": "", "pages_scraped": 0}
    if isinstance(ext_result, Exception):
        errors.append(f"External research error: {ext_result}")
        ext_result = {"combined_text": ""}
    if isinstance(fin_result, Exception):
        errors.append(f"Financial analyst error: {fin_result}")
        fin_result = {"combined_text": ""}

    # Update company name with what scraper found (more accurate)
    scraped_name = web_result.get("company_name", "")
    if scraped_name and len(scraped_name) > 2:
        company_name = scraped_name

    pages = web_result.get("pages_scraped", len(web_result.get("pages", [])))
    progress.append(f"Scraped {pages} pages from {url}")
    progress.append("External research completed (news, funding, LinkedIn, competitors)")

    is_public = fin_result.get("is_public", False)
    ticker = fin_result.get("ticker", "")
    fin_label = f"Financial data: {'Public — ' + ticker if is_public and ticker else 'Private company'}"
    progress.append(fin_label)

    return {
        **state,
        "company_name": company_name,
        "website_content": web_result.get("combined_text", ""),
        "external_research": ext_result.get("combined_text", ""),
        "financial_data": fin_result.get("combined_text", ""),
        "raw_financial": {
            "is_public":     fin_result.get("is_public", False),
            "ticker":        fin_result.get("ticker"),
            "raw_data":      fin_result.get("raw_data", {}),
            "stock_history": fin_result.get("stock_history"),
            "quarterly":     fin_result.get("quarterly", {}),
            "annual":        fin_result.get("annual", {}),
            "competitors":   fin_result.get("competitors", []),
        },
        "news_items": fin_result.get("news_items", []),
        "pages_scraped": pages,
        "progress": progress,
        "errors": errors,
    }


# ─── Node: Synthesize Report ─────────────────────────────────────────────────

def synthesize_report_node(state: AgentState) -> AgentState:
    """Call the LLM synthesizer to produce the final report."""
    progress = state.get("progress", [])
    progress.append("Generating intelligence report with LLM...")

    report = synthesize_report(
        company_name=state["company_name"],
        url=state["url"],
        website_content=state["website_content"],
        external_research=state["external_research"],
        financial_data=state["financial_data"],
        groq_api_key=state["groq_api_key"],
    )

    progress.append("Report generated successfully.")

    return {
        **state,
        "final_report": report,
        "progress": progress,
    }


# ─── Build Graph ─────────────────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("extract_company_info", extract_company_info)
    graph.add_node("gather_all_data", gather_all_data)
    graph.add_node("synthesize_report", synthesize_report_node)

    graph.set_entry_point("extract_company_info")
    graph.add_edge("extract_company_info", "gather_all_data")
    graph.add_edge("gather_all_data", "synthesize_report")
    graph.add_edge("synthesize_report", END)

    return graph.compile()


# Singleton compiled graph
_graph = None

def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run_pipeline(url: str, groq_api_key: str) -> AgentState:
    """Run the full pipeline and return the final state."""
    graph = get_graph()
    initial_state: AgentState = {
        "url": url,
        "groq_api_key": groq_api_key,
        "company_name": "",
        "domain": "",
        "website_content": "",
        "external_research": "",
        "financial_data": "",
        "raw_financial": {},
        "news_items": [],
        "pages_scraped": 0,
        "final_report": "",
        "progress": [],
        "errors": [],
    }
    return graph.invoke(initial_state)
