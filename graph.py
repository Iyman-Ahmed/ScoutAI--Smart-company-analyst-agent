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

import logging
import re
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait
from typing import TypedDict
from urllib.parse import urlparse

from langgraph.graph import StateGraph, END

from agents.web_scraper import scrape_website
from agents.external_researcher import research_external
from agents.financial_analyst import get_financial_data, reset_session as reset_financial_session
from agents.synthesizer import synthesize_report
from agents.deadline import Deadline

logger = logging.getLogger(__name__)

# Wall-clock budget for the whole parallel gather phase. Agents check this
# cooperatively; the executor backstop is a few seconds beyond it.
GATHER_BUDGET_SECONDS = 100


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
    research_evidence: list  # list[Evidence] — provenance-tagged facts for citations
    source_status: dict      # {source_name: "ok"|"empty"|"failed:reason"}

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
    Run web scraper, external researcher, and financial analyst in parallel under a
    shared wall-clock deadline. Each agent checks the deadline cooperatively; the
    executor backstop below only bounds the wait — it cannot kill a wedged thread,
    so on timeout we detach the shared financial session (reset_financial_session)
    to stop a zombie thread corrupting it, and we return PARTIAL results rather than
    re-running everything (the old sequential-rerun path doubled load exactly when
    rate-limited).
    """
    url = state["url"]
    company_name = state["company_name"]
    domain = state["domain"]
    progress = state.get("progress", [])
    errors = state.get("errors", [])

    dl = Deadline(GATHER_BUDGET_SECONDS)
    empty_web = {"company_name": company_name, "pages": [], "combined_text": "", "pages_scraped": 0}
    empty_ext = {"combined_text": "", "evidence": [], "source_status": {}}
    empty_fin = {"combined_text": "", "news_items": []}

    # Single thread-based executor works whether or not we're inside an event loop.
    executor = ThreadPoolExecutor(max_workers=3)
    futures = {
        "web":      executor.submit(scrape_website, url, dl),
        "external": executor.submit(research_external, company_name, domain, dl),
        "financial": executor.submit(get_financial_data, company_name, dl),
    }
    # Backstop a few seconds past the cooperative deadline for well-behaved agents to finish.
    futures_wait(futures.values(), timeout=GATHER_BUDGET_SECONDS + 10)

    def _collect(name, fallback):
        fut = futures[name]
        if not fut.done():
            errors.append(f"{name} timed out after {GATHER_BUDGET_SECONDS}s — partial results used.")
            logger.warning(f"{name} agent exceeded deadline; using partial/empty result.")
            return fallback, "failed:timeout"
        try:
            return fut.result(), "ok"
        except Exception as e:
            errors.append(f"{name} error: {e}")
            logger.error(f"{name} agent raised: {e}")
            return fallback, f"failed:{type(e).__name__}"

    web_result, web_state = _collect("web", empty_web)
    ext_result, ext_state = _collect("external", empty_ext)
    fin_result, fin_state = _collect("financial", empty_fin)

    # A timed-out worker can't be killed; detach the shared curl_cffi session so it
    # can't be used concurrently by the zombie thread and the next request.
    if fin_state.startswith("failed"):
        reset_financial_session()
    # Don't block the pipeline waiting for zombie threads.
    executor.shutdown(wait=False)

    # Merge per-agent status with the external researcher's per-source detail.
    if web_state == "ok":
        web_state = web_result.get("source_status") or ("ok" if web_result.get("pages_scraped", 0) else "failed:no_readable_content")
    source_status = {"web_scraper": web_state, "financial": fin_state}
    financial_sources = fin_result.get("source_status", {})
    if financial_sources:
        source_status.pop("financial", None)
        source_status.update(financial_sources)
    source_status.update(ext_result.get("source_status", {}))
    if ext_state.startswith("failed"):
        source_status["external_research"] = ext_state

    # Update company name with what scraper found (more accurate)
    scraped_name = web_result.get("company_name", "")
    if scraped_name and len(scraped_name) > 2:
        company_name = scraped_name

    pages = web_result.get("pages_scraped", len(web_result.get("pages", [])))
    progress.append(f"Scraped {pages} pages from {url}")
    ok_sources = [k for k, v in source_status.items() if v == "ok"]
    failed_sources = [k for k, v in source_status.items() if v.startswith("failed")]
    progress.append(f"External research: {len(ok_sources)} sources ok"
                    + (f", {len(failed_sources)} unavailable" if failed_sources else ""))

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
        "research_evidence": ext_result.get("evidence", []),
        "source_status": source_status,
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
        evidence=state.get("research_evidence", []),
        source_status=state.get("source_status", {}),
        errors=state.get("errors", []),
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
        "research_evidence": [],
        "source_status": {},
        "final_report": "",
        "progress": [],
        "errors": [],
    }
    return graph.invoke(initial_state)
