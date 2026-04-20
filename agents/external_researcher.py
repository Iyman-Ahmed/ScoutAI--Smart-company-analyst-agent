"""
External Research Agent
-----------------------
Searches the web for company information beyond the official website.
Covers: news, LinkedIn, funding rounds, reviews, competitors, Wikipedia.
Uses DuckDuckGo (no API key required — completely free).
"""

import time
import logging

from ddgs import DDGS

from config import MAX_SEARCH_RESULTS

logger = logging.getLogger(__name__)

SEARCH_DELAY = 3.0   # seconds between DDG queries (HuggingFace IPs get rate-limited faster)
MAX_RETRIES  = 2     # retry a failed query before giving up


def _safe_search(query: str, max_results: int = MAX_SEARCH_RESULTS) -> list[dict]:
    """
    Search with retry + exponential backoff to handle DDG rate limiting on shared IPs.
    Creates a fresh DDGS instance per query — avoids one blocked session poisoning all queries.
    """
    for attempt in range(MAX_RETRIES):
        try:
            ddgs = DDGS(timeout=20)
            results = list(ddgs.text(query, max_results=max_results))
            time.sleep(SEARCH_DELAY)
            return results
        except Exception as e:
            wait = SEARCH_DELAY * (2 ** attempt)   # 3s → 6s → give up
            logger.warning(f"DDG search failed (attempt {attempt+1}) for '{query}': {e} — retrying in {wait:.1f}s")
            time.sleep(wait)
    logger.error(f"DDG search gave up after {MAX_RETRIES} attempts: '{query}'")
    return []


def _format_results(results: list[dict], label: str) -> str:
    if not results:
        return f"**{label}:** No results found.\n"
    lines = [f"**{label}:**"]
    for r in results:
        title = r.get("title", "").strip()
        body = r.get("body", "").strip()
        href = r.get("href", "")
        if title or body:
            lines.append(f"- {title}: {body[:300]} [{href}]")
    return "\n".join(lines) + "\n"


def research_external(company_name: str, domain: str = "") -> dict:
    """
    Main entry point for the external research agent.
    Returns a dict with categorized research results.
    """
    if not company_name:
        return {"combined_text": "No company name available for research."}

    sections = {}

    # 1. General overview, Wikipedia & leadership
    results = _safe_search(f"{company_name} company overview CEO founder leadership Wikipedia")
    sections["overview"] = _format_results(results, "General Overview & Leadership")

    # 2. Recent news (last 12 months)
    results = _safe_search(f"{company_name} company news 2025 2026")
    sections["news"] = _format_results(results, "Recent News (2025-2026)")

    # 3. Funding, investors & revenue
    results = _safe_search(f"{company_name} funding raised investors revenue ARR growth Crunchbase")
    sections["funding"] = _format_results(results, "Funding, Revenue & Investors")

    # 4. Competitors & market position
    results = _safe_search(f"{company_name} competitors market alternatives vs")
    sections["competitors"] = _format_results(results, "Competitors & Market Position")

    # 5. Customer reviews & reputation
    results = _safe_search(f"{company_name} reviews G2 Trustpilot Glassdoor rating")
    sections["reviews"] = _format_results(results, "Customer & Employee Reviews")

    # 6. LinkedIn, employees & team size
    results = _safe_search(f"{company_name} LinkedIn employees team size founded headquarters")
    sections["linkedin"] = _format_results(results, "LinkedIn & Employee Data")

    # Combine into one text block
    combined = "\n\n".join(sections.values())

    return {
        "sections": sections,
        "combined_text": combined,
    }
