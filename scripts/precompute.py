"""
Nightly precompute (council Phase 3 — durability).

Warms the cache for a small universe of frequently-queried companies so live requests
become cache hits. Runs in GitHub Actions (free unlimited minutes on public repos).

IMPORTANT constraint from the council red-team: GitHub Actions runners use DATACENTER
IPs, which DuckDuckGo and Yahoo Finance block just as hard as HuggingFace's. So this
job ONLY touches sources that tolerate datacenter egress:
    SEC EDGAR, Wikipedia/Wikidata, GitHub, Hacker News.
It deliberately does NOT call DDG or Yahoo — those must stay on the live path.

Output: JSON under precompute/ (uploaded as a CI artifact). In production this would be
pushed to a free HuggingFace Dataset repo (needs a write-scoped HF_TOKEN secret) which
the Space reads at boot — the persistent layer HF's ephemeral disk can't provide.
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.deadline import Deadline
from agents import structured_feeds as sf

# Small, editable universe. Extend with recently-queried tickers over time.
UNIVERSE = [
    ("Apple", "https://apple.com"), ("Microsoft", "https://microsoft.com"),
    ("Nvidia", "https://nvidia.com"), ("Amazon", "https://amazon.com"),
    ("Tesla", "https://tesla.com"), ("Alphabet", "https://abc.xyz"),
    ("Stripe", "https://stripe.com"), ("Databricks", "https://databricks.com"),
]


def main():
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "precompute")
    os.makedirs(out_dir, exist_ok=True)
    summary = {"run_at": datetime.now(timezone.utc).isoformat(), "companies": {}}

    for name, domain in UNIVERSE:
        dl = Deadline(60)
        record = {}
        # Datacenter-IP-tolerant sources only.
        for feed, fn in [
            ("company_profile", lambda: sf.fetch_company_profile(name, domain, dl)),
            ("sec_8k",          lambda: sf.fetch_sec_8k(name, dl)),
            ("github",          lambda: sf.fetch_github(name, domain, dl)),
            ("hacker_news",     lambda: sf.fetch_hackernews(name, dl)),
        ]:
            try:
                ev, status = fn()
                record[feed] = {"status": status, "n": len(ev)}
            except Exception as e:
                record[feed] = {"status": f"failed:{type(e).__name__}", "n": 0}
        summary["companies"][name] = record
        print(f"{name:12} " + "  ".join(f"{k}={v['status']}({v['n']})" for k, v in record.items()))

    with open(os.path.join(out_dir, "warm.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print(f"\nWrote {out_dir}/warm.json  ({len(UNIVERSE)} companies)")


if __name__ == "__main__":
    main()
