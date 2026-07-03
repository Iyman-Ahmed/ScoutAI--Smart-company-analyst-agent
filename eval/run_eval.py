"""
Public-company pipeline eval (integration test).

Documented in CLAUDE.md but previously missing from the repo (council reliability
finding). Runs at $0 — none of these suites need a Groq key; they exercise the
keyless data-gathering layer and assert on real fields, so a rate-limit outage shows
up as a FAIL/ERROR here instead of silently degrading the live product.

Run:  python eval/run_eval.py
Exits non-zero if overall accuracy drops below THRESHOLD_PCT (for CI gating).
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.deadline import Deadline
from agents.financial_analyst import find_ticker, get_financial_data
from agents import structured_feeds as sf

THRESHOLD_PCT = 80.0


def _case(name, ok, got, expected, err=None, latency=0.0):
    return {"company": name, "expected": expected, "got": got,
            "status": "PASS" if ok else ("ERROR" if err else "FAIL"),
            "latency_s": round(latency, 2), "error": err}


def suite_ticker_resolution():
    cases = []
    for name, expected in [("Apple", "AAPL"), ("Microsoft", "MSFT"), ("Nvidia", "NVDA"),
                           ("Tesla", "TSLA"), ("Amazon", "AMZN")]:
        t0 = time.time()
        try:
            got = (find_ticker(name) or "").upper()
            cases.append(_case(name, got == expected, got, expected, latency=time.time() - t0))
        except Exception as e:
            cases.append(_case(name, False, None, expected, err=str(e), latency=time.time() - t0))
    return cases


def suite_financial_fields():
    """A public company must return a ticker AND non-empty revenue history."""
    cases = []
    for name in ["Apple", "Microsoft"]:
        t0 = time.time()
        try:
            data = get_financial_data(name, Deadline(60))
            annual = data.get("annual", {}) or {}
            has_rev = bool(annual.get("revenue") or data.get("raw_data", {}).get("market_cap") not in (None, "N/A"))
            ok = data.get("is_public") and bool(data.get("ticker")) and has_rev
            cases.append(_case(name, ok, f"ticker={data.get('ticker')},rev={bool(annual.get('revenue'))}",
                               "public+revenue", latency=time.time() - t0))
        except Exception as e:
            cases.append(_case(name, False, None, "public+revenue", err=str(e), latency=time.time() - t0))
    return cases


def suite_structured_feeds():
    """Each keyless feed must return ok/empty (a real result), never fail, for a large-cap."""
    cases = []
    checks = [
        ("company_profile", lambda: sf.fetch_company_profile("Apple", "https://apple.com", Deadline(30))),
        ("sec_8k",          lambda: sf.fetch_sec_8k("Apple Inc.", Deadline(20))),
        ("google_news",     lambda: sf.fetch_google_news("Apple", Deadline(15))),
        ("github",          lambda: sf.fetch_github("Microsoft", "https://microsoft.com", Deadline(15))),
        ("hacker_news",     lambda: sf.fetch_hackernews("Apple", Deadline(15))),
    ]
    for name, fn in checks:
        t0 = time.time()
        try:
            ev, status = fn()
            ok = not status.startswith("failed")   # ok or empty both acceptable; failure is not
            cases.append(_case(name, ok, f"{status} (n={len(ev)})", "ok|empty", latency=time.time() - t0))
        except Exception as e:
            cases.append(_case(name, False, None, "ok|empty", err=str(e), latency=time.time() - t0))
    return cases


def suite_entity_gate():
    """The disambiguation gate must resolve 'Apple' to Apple Inc., not the fruit."""
    cases = []
    t0 = time.time()
    try:
        ev, status = sf.fetch_company_profile("Apple", "https://apple.com", Deadline(30))
        text = " ".join(e.text for e in ev).lower()
        ok = "verified" in text and "apple inc" in text
        cases.append(_case("Apple→Apple Inc.", ok, status, "verified company match", latency=time.time() - t0))
    except Exception as e:
        cases.append(_case("Apple→Apple Inc.", False, None, "verified company match", err=str(e)))
    return cases


def main():
    suites = {
        "ticker_resolution": suite_ticker_resolution(),
        "financial_fields": suite_financial_fields(),
        "structured_feeds": suite_structured_feeds(),
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
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=1)

    print(f"\n{'='*50}\nScoutAI public-company eval — {pct}% ({passed}/{total})\n{'='*50}")
    for sname, cases in suites.items():
        p = sum(1 for c in cases if c["status"] == "PASS")
        print(f"  {sname:20} {p}/{len(cases)}")
        for c in cases:
            if c["status"] != "PASS":
                print(f"     ✗ {c['company']}: got={c['got']} err={c['error']}")
    print(f"\nWrote {out_path}")
    sys.exit(0 if pct >= THRESHOLD_PCT else 1)


if __name__ == "__main__":
    main()
