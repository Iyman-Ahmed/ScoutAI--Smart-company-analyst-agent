"""
Evidence & Provenance
---------------------
Council tradecraft finding: ScoutAI already captured source URLs (external_researcher
embedded `[{href}]` in its text) and then threw them away — the synthesizer prompt had
zero citation instructions. This module gives every gathered fact a provenance record
so the report can cite it and rank it by source reliability.

Source tiers (most → least reliable) drive both prompt weighting and citation trust:
  A  audited / statutory   — SEC EDGAR (10-K/8-K XBRL & filings)
  B  market data           — Yahoo Finance / exchange quotes
  C  reference / news       — Wikipedia, Wikidata, wire news, GDELT
  D  self-reported          — the company's own website & marketing copy
  E  community / signal      — GitHub, Hacker News (directional, not authoritative)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Ordered best → worst. Used for sorting the Sources appendix and prompt guidance.
TIER_ORDER = ["A", "B", "C", "D", "E"]

TIER_LABEL = {
    "A": "Audited/statutory (SEC EDGAR)",
    "B": "Market data (exchange/Yahoo Finance)",
    "C": "Reference/news (Wikipedia, wire news)",
    "D": "Self-reported (company website)",
    "E": "Community signal (GitHub, Hacker News)",
}


@dataclass
class Evidence:
    """One provenance-tagged snippet of gathered intelligence."""
    text: str                       # the claim / snippet
    source_type: str                # human label, e.g. "SEC 8-K", "Google News", "Company site"
    tier: str = "C"                 # one of TIER_ORDER
    source_url: str = ""            # citable link (empty for derived facts)
    published_at: str = ""          # ISO date the underlying item was published, if known
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    def as_line(self, idx: int) -> str:
        """Render for the numbered Sources appendix, e.g. `[S3] (Tier A, SEC 8-K, 2026-02-11) url`."""
        bits = [f"Tier {self.tier}", self.source_type]
        if self.published_at:
            bits.append(f"published {self.published_at}")
        bits.append(f"retrieved {self.retrieved_at}")
        meta = ", ".join(bits)
        tail = f" {self.source_url}" if self.source_url else ""
        return f"[S{idx}] ({meta}){tail}"


def build_sources_appendix(evidence: list[Evidence]) -> str:
    """
    Render a numbered, tier-sorted Sources appendix. The numbering here is what the
    synthesis prompt is told to cite with [S#] tags, so ordering must be stable.
    Returns "" when there is no citable evidence.
    """
    citable = [e for e in evidence if e.source_url]
    if not citable:
        return ""
    citable.sort(key=lambda e: (TIER_ORDER.index(e.tier) if e.tier in TIER_ORDER else 99,
                                 e.source_type))
    lines = ["## Sources", ""]
    for i, e in enumerate(citable, 1):
        lines.append(e.as_line(i))
    return "\n".join(lines)


def evidence_digest(evidence: list[Evidence], max_chars: int = 4000) -> str:
    """
    Compact, tier-ordered, numbered rendering of evidence for the LLM prompt so the
    model can attach [S#] citations that line up with build_sources_appendix().
    """
    citable = [e for e in evidence if e.source_url]
    citable.sort(key=lambda e: (TIER_ORDER.index(e.tier) if e.tier in TIER_ORDER else 99,
                                 e.source_type))
    out, used = [], 0
    for i, e in enumerate(citable, 1):
        snippet = e.text.strip().replace("\n", " ")
        line = f"[S{i}] (Tier {e.tier}, {e.source_type}) {snippet[:300]}"
        if used + len(line) > max_chars:
            break
        out.append(line)
        used += len(line)
    return "\n".join(out)
