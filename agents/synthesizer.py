"""
Report Synthesizer Agent
-------------------------
Council Phase 1 (tradecraft) + Phase 3 (durability) rewrite.

Phase 1 — stop the report from silently lying:
  * Source-tier hierarchy in the prompt (audited EDGAR > market data > news >
    company's own marketing site) instead of a flat "treat all as reliable".
  * Numbered [S#] citations + a Sources appendix, using provenance that the pipeline
    already gathered and previously threw away.
  * An "Intelligence Gaps" section fed by the errors/source_status the pipeline
    collected but never surfaced, so a failed source is never read as "no data".
  * The Buy/Hold/Avoid call moved into a clearly labelled "Model Assessment —
    inference, not fact" block, no longer voiced like a filed number.
  * As-of dating guidance so year-old filings aren't stamped as current.

Phase 3 — the $0 constraint made durable:
  * LLM fallback LADDER: Groq → Cerebras (if key present) → a deterministic template
    report assembled from structured evidence. Groq was a single point of failure.
  * Two-pass synthesis within a provider (small model extracts claims + contradictions,
    large model writes) — degrades to single-pass automatically.
"""

import logging
import os
import re
from datetime import datetime, timezone

from config import GROQ_MODEL, GROQ_EXTRACT_MODEL
from agents.evidence import (Evidence, TIER_LABEL, build_sources_appendix, evidence_digest)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a senior business intelligence analyst writing a rigorous, sourced report.

SOURCE RELIABILITY (trust in this order — never let a lower tier override a higher one):
  Tier A — SEC EDGAR filings (10-K/8-K): audited, authoritative. Use for financial facts.
  Tier B — market data (exchange / Yahoo Finance): reliable for price/valuation.
  Tier C — reference & news (Wikipedia, wire news): reliable for events, not for self-claims.
  Tier D — the company's OWN website/marketing: treat market-position and superlative claims
           as ASSERTIONS, not facts. Never state a company's marketing claim as established fact.
  Tier E — community signal (GitHub, Hacker News): directional sentiment only.

RULES:
- Use ONLY facts present in the provided data. Never invent numbers, dates, or names.
- Cite sources: when a fact comes from a numbered evidence item, append its tag, e.g. [S3].
- Distinguish FACT from INFERENCE. Use estimative language for judgements: "EDGAR filings show…"
  (fact) vs "we assess, with moderate confidence, that…" (inference).
- If a source FAILED this run (listed under DATA GAPS), do NOT infer the absence of that
  information. Say the source was unavailable.
- Date financial figures by their filing/quote period where known; do not imply stale data is current.
- Use tables and bullets; no filler. YoY comparisons must name specific years.
"""

REPORT_TEMPLATE = """DATA AVAILABLE (cite numbered items as [S#]):

== NUMBERED EVIDENCE (provenance-tagged) ==
{evidence_block}

== WEBSITE / PRODUCT INFO (Tier D — self-reported) ==
{website_content}

== EXTERNAL RESEARCH ROLLUP ==
{external_research}

== FINANCIAL DATA (Tier A/B) ==
{financial_data}

== DATA GAPS (sources unavailable this run — do NOT read as "no data exists") ==
{gaps_block}

{contradictions_block}
---

Write a Company Intelligence Report for **{company_name}** ({url}).
Use EXACTLY these sections with ## headers. Be data-dense, specific, and cite [S#] tags.

## 1. Company Snapshot
- One-line description, founding year, HQ, headcount, website (cite sources)
- Core products/services (3-5 bullets)
- Business model: how they make money
- Key leadership: CEO, founders (name + background)
- Target market: B2B/B2C, industries, geographies

## 2. Business Model & Revenue Deep-Dive
- Revenue streams; pricing model; notable customers/partnerships
- Revenue metrics (ARR/MRR/GMV as applicable)
- YoY revenue table when multiple years exist:
  | Year | Revenue | Growth |
  |------|---------|--------|

## 3. Competitive Landscape
- Top 3-5 competitors, one line each
- Comparison table (metrics available):
  | Company | Market Cap / Valuation | Revenue | Differentiator |
  |---------|------------------------|---------|----------------|
- Positioning: leader / challenger / niche

## 4. Recent News & Strategic Developments
- Dated timeline of major events (prefer Tier A SEC 8-K and Tier C news; include [S#])
- Regulatory / legal / macro headwinds or tailwinds
- Announced roadmap or upcoming catalysts

## 5. Investment & Risk Outlook
- Bull case: 3 reasons with data points
- Bear case: 3 risks with data points
- **Confidence:** state High/Moderate/Low and why (single-source? stale? self-reported?)

## Model Assessment — inference, not fact
> The following is a machine-generated signal derived from the data above. It is an
> INFERENCE, not investment advice and not a filed figure.
- Signal: Strong Buy / Buy / Hold / Watch / Avoid — one-sentence rationale
- Top 3 drivers of this signal
- Key metrics to monitor

## Intelligence Gaps
- What could NOT be verified this run, and which sources were unavailable (from DATA GAPS)

Start with ONLY this header (no preamble):
# {company_name} — Intelligence Report
*Generated {date} · Figures reflect latest available filings/quotes as of retrieval · Source: {url}*

End with:
---
*ScoutAI — Smart Company Analyst Agent*
"""

EXTRACT_PROMPT = """From the data below, extract a compact briefing for a report writer.
Output three short sections and nothing else:
1) KEY FACTS — bullet list of verifiable facts with their [S#] tags where present.
2) CONTRADICTIONS — any places where sources disagree (e.g. differing headcount, dates,
   revenue). If none, write "None detected."
3) UNVERIFIED CLAIMS — company self-reported (Tier D) claims to treat with caution.

DATA:
{data}
"""


# ─── Provider ladder ─────────────────────────────────────────────────────────

def _log_llm_failure(rung: str, error: Exception, api_key: str = "") -> None:
    """Retain provider diagnostics without exposing configured or BYO credentials."""
    message = str(error)
    for secret in (api_key, os.getenv("GROQ_API_KEY", ""), os.getenv("CEREBRAS_API_KEY", "")):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    message = re.sub(r"gsk_[A-Za-z0-9_-]+", "[REDACTED]", message)
    message = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", message)
    logger.warning("LLM rung %s failed: %s: %s", rung, type(error).__name__, message)


def _groq_call(model: str, system: str, human: str, api_key: str, max_tokens: int = 4096) -> str:
    from langchain_groq import ChatGroq
    from langchain_core.messages import HumanMessage, SystemMessage
    llm = ChatGroq(model=model, api_key=api_key, temperature=0.1, max_tokens=max_tokens)
    return llm.invoke([SystemMessage(content=system), HumanMessage(content=human)]).content


def _cerebras_call(system: str, human: str, max_tokens: int = 4096) -> str:
    """Optional second rung. Requires CEREBRAS_API_KEY and the cerebras SDK; else raises."""
    key = os.environ.get("CEREBRAS_API_KEY", "")
    if not key:
        raise RuntimeError("no CEREBRAS_API_KEY")
    from cerebras.cloud.sdk import Cerebras  # optional dep; ImportError → next rung
    client = Cerebras(api_key=key)
    resp = client.chat.completions.create(
        model=os.getenv("CEREBRAS_MODEL") or "llama-3.3-70b",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": human}],
        temperature=0.1, max_tokens=max_tokens,
    )
    return resp.choices[0].message.content


# ─── Deterministic template floor (no third party at all) ────────────────────

def _template_floor(company_name, url, evidence, financial_data, source_status, errors) -> str:
    """
    Last-resort deterministic report assembled from structured evidence when every LLM
    rung is down. Clearly banner-labelled so a machine-assembled fact sheet is never
    mistaken for analysis — the guaranteed output that keeps ScoutAI alive at $0 even
    if Groq revokes the free tier.
    """
    date = datetime.now(timezone.utc).strftime("%B %d, %Y")
    lines = [
        f"# {company_name} — Intelligence Report",
        f"*Generated {date} · Source: {url}*",
        "",
        "> ⚠️ **Machine-assembled fact sheet — no analytic judgement.** Every AI writer was "
        "unavailable, so this report lists the raw gathered facts with sources and applies no "
        "inference. Re-run when synthesis is available for a full analysis.",
        "",
    ]
    by_type: dict[str, list[Evidence]] = {}
    for e in evidence:
        by_type.setdefault(e.source_type, []).append(e)
    if by_type:
        lines.append("## Gathered Facts")
        for stype, items in by_type.items():
            lines.append(f"\n**{stype}:**")
            for e in items:
                tag = f" ({e.published_at})" if e.published_at else ""
                lines.append(f"- {e.text}{tag}")
    if financial_data and financial_data.strip():
        lines += ["", "## Financial Data", financial_data.strip()[:4000]]
    failed = [k for k, v in (source_status or {}).items() if v.startswith("failed")]
    if failed or errors:
        lines.append("\n## Intelligence Gaps")
        for f in failed:
            lines.append(f"- Source unavailable this run: {f}")
        for e in (errors or []):
            lines.append(f"- {e}")
    appendix = build_sources_appendix(evidence)
    if appendix:
        lines += ["", appendix]
    lines += ["", "---", "*ScoutAI — Smart Company Analyst Agent (deterministic fallback)*"]
    return "\n".join(lines)


# ─── Orchestration ───────────────────────────────────────────────────────────

def _build_gaps_block(source_status: dict, errors: list) -> str:
    failed = [k for k, v in (source_status or {}).items() if v.startswith("failed")]
    bits = []
    for f in failed:
        bits.append(f"- {f}: source failed/unavailable this run")
    for e in (errors or []):
        bits.append(f"- {e}")
    return "\n".join(bits) if bits else "- None — all sources responded."


def synthesize_report(
    company_name: str,
    url: str,
    website_content: str,
    external_research: str,
    financial_data: str,
    groq_api_key: str,
    evidence: list = None,
    source_status: dict = None,
    errors: list = None,
) -> str:
    evidence = evidence or []
    source_status = source_status or {}
    errors = list(errors or [])

    # Honest refusal (council ruling on the reliability contrarian): if the core
    # gatherers are BOTH down and we have essentially nothing verified, refuse rather
    # than synthesise a confident-sounding report from a homepage crawl. A system that
    # always emits a report is a system that sometimes lies.
    core_down = (source_status.get("web_scraper", "").startswith("failed")
                 and source_status.get("financial", "").startswith("failed"))
    if core_down and len(evidence) < 2 and not (website_content or "").strip():
        gaps = _build_gaps_block(source_status, errors)
        return ("# " + company_name + " — Insufficient Verified Data\n\n"
                "> ⚠️ ScoutAI could not gather enough verified data this run to produce an "
                "honest report — the core sources were unavailable. Please retry in a minute.\n\n"
                "**Sources unavailable this run:**\n" + gaps + "\n\n---\n"
                "*ScoutAI — Smart Company Analyst Agent*")

    # Budgets rebalanced toward audited filings; company marketing site no longer
    # gets the largest slice (it is the least-reliable Tier D source).
    financial_trimmed = (financial_data or "")[:6000]
    website_trimmed = (website_content or "")[:5000]
    external_trimmed = (external_research or "")[:5000]
    evidence_block = evidence_digest(evidence, max_chars=4500) or "No provenance-tagged evidence."
    gaps_block = _build_gaps_block(source_status, errors)

    # ── Optional pass 1: cheap extraction of facts + contradictions ──────────
    contradictions_block = ""
    if groq_api_key:
        try:
            extract_input = f"{evidence_block}\n\nFINANCIAL:\n{financial_trimmed}\n\nWEBSITE:\n{website_trimmed}"
            briefing = _groq_call(GROQ_EXTRACT_MODEL, "You extract facts precisely and never invent data.",
                                  EXTRACT_PROMPT.format(data=extract_input[:8000]),
                                  groq_api_key, max_tokens=1200)
            if briefing and briefing.strip():
                contradictions_block = ("== EXTRACTED BRIEFING (facts / contradictions / unverified) ==\n"
                                        + briefing.strip() + "\n")
        except Exception as e:
            _log_llm_failure("groq-extraction/" + GROQ_EXTRACT_MODEL, e, groq_api_key)

    human = REPORT_TEMPLATE.format(
        company_name=company_name,
        url=url,
        evidence_block=evidence_block,
        website_content=website_trimmed or "Not available.",
        external_research=external_trimmed or "Not available.",
        financial_data=financial_trimmed or "Not available.",
        gaps_block=gaps_block,
        contradictions_block=contradictions_block,
        date=datetime.now(timezone.utc).strftime("%B %d, %Y"),
    )

    # ── Provider ladder: Groq → Cerebras → deterministic floor ───────────────
    report = None
    rate_limited = False
    if groq_api_key:
        try:
            report = _groq_call(GROQ_MODEL, SYSTEM_PROMPT, human, groq_api_key, max_tokens=4096)
        except Exception as e:
            msg = str(e).lower()
            rate_limited = "429" in msg or "rate limit" in msg or "rate_limit" in msg
            _log_llm_failure("groq-synthesis/" + GROQ_MODEL, e, groq_api_key)

    else:
        _log_llm_failure("groq-synthesis/" + GROQ_MODEL, RuntimeError("no GROQ_API_KEY"))

    if not report:
        try:
            report = _cerebras_call(SYSTEM_PROMPT, human)
            logger.info("Synthesis served by Cerebras fallback rung.")
        except Exception as e:
            _log_llm_failure("cerebras-synthesis/" + (os.getenv("CEREBRAS_MODEL") or "llama-3.3-70b"), e, groq_api_key)

    if not report:
        banner = ("\n\n> ℹ️ *AI synthesis was rate-limited; showing a deterministic fact sheet. "
                  "Retry shortly for full analysis.*") if rate_limited else ""
        return _template_floor(company_name, url, evidence, financial_data, source_status, errors) + banner

    # Append the numbered Sources appendix that the [S#] citations refer to.
    appendix = build_sources_appendix(evidence)
    if appendix and "## Sources" not in report:
        report = report.rstrip() + "\n\n" + appendix
    return report
