"""
External Research Agent
-----------------------
Council Phase 2 rewrite. Previously this ran 6 flaky DuckDuckGo text searches and,
when DDG rate-limited, rendered "No results found." — indistinguishable from a
company genuinely having no news. The LLM then confidently reported the absence.

Now it orchestrates keyless STRUCTURED feeds first (Wikipedia/Wikidata, SEC 8-K,
Google News RSS, GitHub, Hacker News) and keeps DuckDuckGo only for the two things
those feeds can't provide — funding/revenue chatter and competitor mentions —
which is also the graceful-degradation path for private companies with no filings
and no Wikipedia page. Every source reports a tri-state status
(`ok | empty | failed:<reason>`) so downstream code can tell "no data" from
"source down", and every fact carries an Evidence provenance record.
"""

import logging
import time

from ddgs import DDGS

from config import MAX_SEARCH_RESULTS
from agents.deadline import Deadline, as_deadline
from agents.evidence import Evidence
from agents import structured_feeds as sf

logger = logging.getLogger(__name__)

SEARCH_DELAY = 3.0   # seconds between DDG queries (shared IPs rate-limit fast)
MAX_RETRIES = 2


def _safe_search(query: str, dl: Deadline, max_results: int = MAX_SEARCH_RESULTS):
    """
    Deadline-aware DDG search with retry. Returns (results, status) where status is
    'ok' | 'empty' | 'failed:<reason>' — critically, a rate-limit failure is reported
    as 'failed', never silently as an empty result.
    """
    for attempt in range(MAX_RETRIES):
        if dl.expired():
            return [], "failed:deadline"
        try:
            ddgs = DDGS(timeout=int(dl.clamp(20)))
            results = list(ddgs.text(query, max_results=max_results))
            if not dl.expired():
                time.sleep(min(SEARCH_DELAY, dl.remaining()))
            return results, ("ok" if results else "empty")
        except Exception as e:
            wait = SEARCH_DELAY * (2 ** attempt)
            logger.warning(f"DDG search failed (attempt {attempt+1}) for '{query}': {e}")
            if dl.remaining() <= wait:
                break
            time.sleep(wait)
    logger.error(f"DDG gave up for '{query}'")
    return [], "failed:rate_limited"


def _ddg_evidence(results: list[dict], source_type: str, tier: str = "D") -> list[Evidence]:
    ev = []
    for r in results:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        href = r.get("href", "")
        if title or body:
            ev.append(Evidence(text=f"{title}: {body[:300]}", source_type=source_type,
                                tier=tier, source_url=href))
    return ev


def research_external(company_name: str, domain: str = "", deadline=None) -> dict:
    """
    Gather external intelligence from structured feeds + demoted DDG.

    Returns:
      evidence:      list[Evidence]           — provenance-tagged facts
      combined_text: str                       — human/LLM-readable rollup (back-compat)
      source_status: dict[str, str]            — per-source ok|empty|failed:reason
    """
    dl = as_deadline(deadline, 70)
    if not company_name:
        return {"evidence": [], "combined_text": "No company name available for research.",
                "source_status": {}}

    evidence: list[Evidence] = []
    status: dict[str, str] = {}

    # ── Structured feeds (primary) ───────────────────────────────────────────
    for name, fn in [
        ("company_profile", lambda: sf.fetch_company_profile(company_name, domain, dl)),
        ("sec_8k",          lambda: sf.fetch_sec_8k(company_name, dl)),
        ("google_news",     lambda: sf.fetch_google_news(company_name, dl)),
        ("github",          lambda: sf.fetch_github(company_name, domain, dl)),
        ("hacker_news",     lambda: sf.fetch_hackernews(company_name, dl)),
    ]:
        if dl.expired():
            status[name] = "failed:deadline"
            continue
        try:
            ev, st = fn()
            evidence.extend(ev)
            status[name] = st
        except Exception as e:
            logger.warning(f"feed {name} raised: {e}")
            status[name] = f"failed:{type(e).__name__}"

    # ── DuckDuckGo (demoted) — only what structured feeds can't cover ─────────
    for name, query, source_type in [
        ("funding",     f"{company_name} funding raised investors revenue ARR valuation Crunchbase", "Web: funding"),
        ("competitors", f"{company_name} competitors alternatives market vs", "Web: competitors"),
    ]:
        if dl.expired():
            status[name] = "failed:deadline"
            continue
        results, st = _safe_search(query, dl)
        evidence.extend(_ddg_evidence(results, source_type))
        status[name] = st

    combined_text = _render(evidence, status)
    return {"evidence": evidence, "combined_text": combined_text, "source_status": status}


def _render(evidence: list[Evidence], status: dict[str, str]) -> str:
    """Human-readable rollup grouped by source type, with an explicit status footer."""
    if not evidence and not status:
        return "No external research available."
    by_type: dict[str, list[Evidence]] = {}
    for e in evidence:
        by_type.setdefault(e.source_type, []).append(e)
    lines = []
    for stype, items in by_type.items():
        lines.append(f"**{stype}:**")
        for e in items:
            tail = f" [{e.source_url}]" if e.source_url else ""
            lines.append(f"- {e.text[:300]}{tail}")
        lines.append("")
    # Surface failures explicitly so nothing reads a source outage as "no data".
    failed = [k for k, v in status.items() if v.startswith("failed")]
    if failed:
        lines.append(f"_Sources unavailable this run (data may be incomplete): {', '.join(failed)}_")
    return "\n".join(lines).strip()
