"""
External Research Agent
-----------------------
Searches the web for company information beyond the official website.
Covers: news, LinkedIn, funding rounds, reviews, competitors, Wikipedia.
Uses DuckDuckGo (no API key required — completely free).
"""

import time
import logging
from typing import Optional

from duckduckgo_search import DDGS

from config import MAX_SEARCH_RESULTS

logger = logging.getLogger(__name__)

SEARCH_DELAY = 1.2  # seconds between DDG queries to avoid rate-limiting


def _safe_search(ddgs: DDGS, query: str, max_results: int = MAX_SEARCH_RESULTS) -> list[dict]:
    try:
        results = list(ddgs.text(query, max_results=max_results))
        time.sleep(SEARCH_DELAY)
        return results
    except Exception as e:
        logger.warning(f"DDG search failed for '{query}': {e}")
        time.sleep(2)
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

    ddgs = DDGS()
    sections = {}

    # 1. General overview & Wikipedia
    results = _safe_search(ddgs, f"{company_name} company overview Wikipedia")
    sections["overview"] = _format_results(results, "General Overview / Wikipedia")

    # 2. Recent news (last 12 months)
    results = _safe_search(ddgs, f"{company_name} company news 2025 2026")
    sections["news"] = _format_results(results, "Recent News (2025-2026)")

    # 3. LinkedIn presence
    results = _safe_search(ddgs, f"{company_name} LinkedIn company employees founded")
    sections["linkedin"] = _format_results(results, "LinkedIn & Employee Data")

    # 4. Funding & investors
    results = _safe_search(ddgs, f"{company_name} funding raised investors Crunchbase series")
    sections["funding"] = _format_results(results, "Funding & Investors")

    # 5. Revenue & financials (public info)
    results = _safe_search(ddgs, f"{company_name} annual revenue ARR growth 2024 2025")
    sections["revenue"] = _format_results(results, "Revenue & Growth")

    # 6. Competitors & market position
    results = _safe_search(ddgs, f"{company_name} competitors market alternatives vs")
    sections["competitors"] = _format_results(results, "Competitors & Market Position")

    # 7. Customer reviews & reputation
    results = _safe_search(ddgs, f"{company_name} reviews G2 Trustpilot Glassdoor rating")
    sections["reviews"] = _format_results(results, "Customer & Employee Reviews")

    # 8. Key people / leadership
    results = _safe_search(ddgs, f"{company_name} CEO founder leadership team")
    sections["leadership"] = _format_results(results, "Key Leadership")

    # Combine into one text block
    combined = "\n\n".join(sections.values())

    return {
        "sections": sections,
        "combined_text": combined,
    }
