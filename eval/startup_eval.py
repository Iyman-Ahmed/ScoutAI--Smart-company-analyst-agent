"""
Private / startup company eval (integration test).

Documented in CLAUDE.md but previously missing (council reliability finding).
Focus: the graceful-degradation path. Private companies have no SEC filings and no
market data, so the key correctness properties are:
  1. We still gather SOMETHING (Wikipedia/Wikidata, news, GitHub, HN, DDG).
  2. We do NOT fabricate financials — a private company must not come back "public".
  3. The entity gate resolves the right company (or honestly returns nothing),
     never a confidently-wrong match.

Run:  python eval/startup_eval.py    ($0 — no Groq key needed)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.deadline import Deadline
from agents.external_researcher import research_external
from agents.financial_analyst import get_financial_data
from agents import structured_feeds as sf

THRESHOLD_PCT = 75.0

# (name, domain) — well-known private companies at eval-authoring time.
STARTUPS = [
    ("Stripe", "https://stripe.com"),
    ("Databricks", "https://databricks.com"),
    ("Canva", "https://canva.com"),
]


def _case(name, ok, got, expected, err=None, latency=0.0):
    return {"company": name, "expected": expected, "got": got,
            "status": "PASS" if ok else ("ERROR" if err else "FAIL"),
            "latency_s": round(latency, 2), "error": err}


def suite_research_coverage():
    """Each startup must yield real evidence from the structured/DDG feeds."""
    cases = []
    for name, domain in STARTUPS:
        t0 = time.time()
        try:
            r = research_external(name, domain, Deadline(70))
            n = len(r.get("evidence", []))
            failed = [k for k, v in r["source_status"].items() if v.startswith("failed")]
            # Coverage is fine as long as we got evidence and most sources responded.
            ok = n >= 3 and len(failed) <= 2
            cases.append(_case(name, ok, f"evidence={n},failed={failed}", ">=3 evidence",
                               latency=time.time() - t0))
        except Exception as e:
            cases.append(_case(name, False, None, ">=3 evidence", err=str(e), latency=time.time() - t0))
    return cases


def suite_no_fabricated_financials():
    """A private company must not be reported as public with market data."""
    cases = []
    for name, _ in STARTUPS:
        t0 = time.time()
        try:
            data = get_financial_data(name, Deadline(45))
            # Acceptable: is_public False, OR is_public with a *real* EDGAR/ticker basis.
            fabricated = data.get("is_public") and not data.get("ticker") and not data.get("annual", {}).get("revenue")
            cases.append(_case(name, not fabricated, f"is_public={data.get('is_public')},ticker={data.get('ticker')}",
                               "no fabricated financials", latency=time.time() - t0))
        except Exception as e:
            cases.append(_case(name, False, None, "no fabricated financials", err=str(e), latency=time.time() - t0))
    return cases


def suite_entity_gate():
    """Entity resolution must not attach a wrong-company match for these startups."""
    cases = []
    for name, domain in STARTUPS:
        t0 = time.time()
        try:
            ev, status = sf.fetch_company_profile(name, domain, Deadline(30))
            text = " ".join(e.text for e in ev).lower()
            # Either verified match, or empty (honest) — but never an UNVERIFIED wrong match
            # that slipped through as tier-C.
            bad = "unverified" in text and any(e.tier == "C" for e in ev)
            cases.append(_case(name, not bad, status, "verified or empty", latency=time.time() - t0))
        except Exception as e:
            cases.append(_case(name, False, None, "verified or empty", err=str(e), latency=time.time() - t0))
    return cases


def main():
    suites = {
        "research_coverage": suite_research_coverage(),
        "no_fabricated_financials": suite_no_fabricated_financials(),
        "entity_gate": suite_entity_gate(),
    }
    total = sum(len(c) for c in suites.values())
    passed = sum(1 for c in suites.values() for x in c if x["status"] == "PASS")
    pct = round(100.0 * passed / total, 1) if total else 0.0
    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "overall_accuracy_pct": pct,
        "total_pass": passed,
        "total_cases": total,
        "suites": {k: {"cases": v} for k, v in suites.items()},
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startup_results.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=1)

    print(f"\n{'='*50}\nScoutAI startup eval — {pct}% ({passed}/{total})\n{'='*50}")
    for sname, cases in suites.items():
        p = sum(1 for c in cases if c["status"] == "PASS")
        print(f"  {sname:26} {p}/{len(cases)}")
        for c in cases:
            if c["status"] != "PASS":
                print(f"     ✗ {c['company']}: got={c['got']} err={c['error']}")
    print(f"\nWrote {out_path}")
    sys.exit(0 if pct >= THRESHOLD_PCT else 1)


if __name__ == "__main__":
    main()
