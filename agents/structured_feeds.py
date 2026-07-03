"""
Structured Keyless Feeds
------------------------
Council Phase 2: ScoutAI *searched* (6 flaky DuckDuckGo text queries, one of which
literally searched for the word "Wikipedia") for data it could *fetch structured*.
This module replaces most of that with keyless, primary-source feeds. Every function
returns `(evidence: list[Evidence], status: str)` where status is one of
`ok | empty | failed:<reason>` so the pipeline can tell "no data" from "source down"
(a distinction the old DDG path erased — an outage rendered identically to "no news").

Grounded during the council (live-tested, all keyless):
  * Wikipedia REST summary          en.wikipedia.org/api/rest_v1/page/summary/{title}
  * Wikidata SPARQL                 query.wikidata.org/sparql
  * SEC EDGAR full-text search      efts.sec.gov/LATEST/search-index
  * Google News RSS                 news.google.com/rss/search  (needs a browser UA)
  * GitHub unauth API               api.github.com  (60 req/hr)
  * Hacker News Algolia             hn.algolia.com/api/v1/search

Deliberately NOT used:
  * Stooq        — now behind an anti-bot JS challenge (refuted in grounding).
  * GDELT        — throttled to 1 req/5s and flaky from shared IPs; nightly-only.
"""

import logging
from typing import Optional
from urllib.parse import quote, urlparse

# Google News RSS is untrusted external XML — parse it with defusedxml to block
# XXE / billion-laughs attacks. Fall back to stdlib only if defusedxml is absent,
# and in that case refuse to parse (return no items) rather than parse unsafely.
try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
    from xml.etree.ElementTree import ParseError as _XMLParseError
    _XML_SAFE = True
except ImportError:  # pragma: no cover
    from xml.etree.ElementTree import ParseError as _XMLParseError
    _xml_fromstring = None
    _XML_SAFE = False

from curl_cffi import requests as cffi_requests

from agents import cache
from agents.deadline import Deadline, as_deadline
from agents.evidence import Evidence

logger = logging.getLogger(__name__)

_UA = "ScoutAI company-research-tool contact@scoutai.app"
_SEC_UA = {"User-Agent": _UA}


def _session() -> cffi_requests.Session:
    # Chrome impersonation gives us a real browser UA (Google News RSS needs one).
    return cffi_requests.Session(impersonate="chrome131")


def _get(url: str, dl: Deadline, timeout: float = 8.0, headers: Optional[dict] = None):
    """Deadline-aware GET. Returns the response or None (never raises)."""
    if dl.expired():
        return None
    try:
        s = _session()
        return s.get(url, timeout=dl.clamp(timeout), headers=headers)
    except Exception as e:
        logger.debug(f"GET failed {url}: {e}")
        return None


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return ""


def _norm_name(s: str) -> str:
    """Lowercase, alphanumeric-only — for punctuation-insensitive name matching."""
    return "".join(c for c in s.lower() if c.isalnum())


# ─── Entity resolution (the red-team's required disambiguation gate) ──────────

def resolve_entity(company_name: str, domain: str, dl: Deadline) -> Optional[dict]:
    """
    Resolve a free-text company name to a Wikidata entity, then VERIFY it is the
    right company before trusting it — otherwise "Apple" or a common startup name
    silently resolves to the wrong entity (confident wrong-company data is worse
    than "No results found"). Verification: Wikidata official website (P856) host
    must match the company's own domain. If we can't verify, we return the entity
    but mark verified=False so callers can down-weight it.

    Returns {qid, title, verified, wikipedia_url, facts} or None.
    """
    if not company_name:
        return None
    company_host = _host(domain)

    def _lookup(title: str) -> Optional[dict]:
        r = _get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}", dl)
        if not r or r.status_code != 200:
            return None
        try:
            data = r.json()
        except Exception:
            return None
        if data.get("type") == "disambiguation" or not data.get("wikibase_item"):
            return None
        return {
            "qid": data["wikibase_item"],
            "title": data.get("title", company_name),
            "extract": data.get("extract", ""),
            "wikipedia_url": (data.get("content_urls", {}).get("desktop", {}) or {}).get("page", ""),
        }

    # Company-biased title candidates so a bare name like "Apple" reaches Apple Inc.,
    # not the fruit. Order matters: disambiguated titles are tried before the bare name.
    candidates = [f"{company_name} (company)", company_name,
                  f"{company_name} Inc.", f"{company_name} Corporation"]

    best_unverified = None
    seen_qids = set()
    for title in candidates:
        if dl.expired():
            break
        ent = cache.cached("wikipedia", title.lower(), 7 * 86400, lambda t=title: _lookup(t))
        if not ent or ent["qid"] in seen_qids:
            continue
        seen_qids.add(ent["qid"])
        facts = _wikidata_facts(ent["qid"], dl)
        ent["facts"] = facts
        official = facts.get("official_website", "")

        # Strong verification: Wikidata official website matches the company's domain.
        if company_host and official and _host(official) == company_host:
            ent["verified"] = True
            return ent
        # Reject: we had a domain to check AND Wikidata lists a *different* site → wrong entity.
        if company_host and official and _host(official) != company_host:
            logger.info(f"Rejected '{title}' for {company_name}: site {official!r} != {company_host!r}")
            continue
        # Org-ness signal: real companies expose CEO/founder/employees/industry; the
        # fruit does not. Keep the best org-like candidate as an unverified fallback.
        org_signal = sum(1 for k in ("ceo", "founder", "employees", "industry") if facts.get(k))
        if org_signal and best_unverified is None:
            ent["verified"] = False
            best_unverified = ent

    return best_unverified


def _wikidata_facts(qid: str, dl: Deadline) -> dict:
    """Fetch structured facts for a QID via SPARQL. Returns {} on any failure."""
    def _produce():
        query = f"""SELECT ?officialWebsite ?inception ?employees ?ceoLabel ?founderLabel ?hqLabel ?industryLabel WHERE {{
  OPTIONAL {{ wd:{qid} wdt:P856 ?officialWebsite. }}
  OPTIONAL {{ wd:{qid} wdt:P571 ?inception. }}
  OPTIONAL {{ wd:{qid} wdt:P1128 ?employees. }}
  OPTIONAL {{ wd:{qid} wdt:P169 ?ceo. }}
  OPTIONAL {{ wd:{qid} wdt:P112 ?founder. }}
  OPTIONAL {{ wd:{qid} wdt:P159 ?hq. }}
  OPTIONAL {{ wd:{qid} wdt:P452 ?industry. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} LIMIT 1"""
        url = f"https://query.wikidata.org/sparql?format=json&query={quote(query)}"
        r = _get(url, dl, headers={"User-Agent": _UA, "Accept": "application/sparql-results+json"})
        if not r or r.status_code != 200:
            return None
        try:
            binds = r.json().get("results", {}).get("bindings", [])
        except Exception:
            return None
        if not binds:
            return {}
        b = binds[0]
        val = lambda k: b.get(k, {}).get("value", "")
        return {
            "official_website": val("officialWebsite"),
            "inception": val("inception")[:10],
            "employees": val("employees"),
            "ceo": val("ceoLabel"),
            "founder": val("founderLabel"),
            "hq": val("hqLabel"),
            "industry": val("industryLabel"),
        }

    return cache.cached("wikidata", qid, 7 * 86400, _produce) or {}


# ─── Feed: company profile (Wikipedia + Wikidata) → Company Snapshot ──────────

def fetch_company_profile(company_name: str, domain: str, dl: Optional[Deadline] = None):
    dl = as_deadline(dl, 20)
    ent = resolve_entity(company_name, domain, dl)
    if not ent:
        return [], "empty"
    tier = "C" if ent.get("verified") else "D"   # unverified match is only as good as self-report
    conf = "verified via official website" if ent.get("verified") else "UNVERIFIED name match"
    ev: list[Evidence] = []
    if ent.get("extract"):
        ev.append(Evidence(
            text=f"{ent['title']} ({conf}): {ent['extract']}",
            source_type="Wikipedia", tier=tier, source_url=ent.get("wikipedia_url", ""),
        ))
    f = ent.get("facts", {})
    facts_bits = []
    for label, key in [("Founded", "inception"), ("HQ", "hq"), ("Employees", "employees"),
                       ("CEO", "ceo"), ("Founder", "founder"), ("Industry", "industry")]:
        if f.get(key):
            facts_bits.append(f"{label}: {f[key]}")
    if facts_bits:
        ev.append(Evidence(
            text=f"Wikidata structured facts ({conf}) — " + "; ".join(facts_bits),
            source_type="Wikidata", tier=tier,
            source_url=f"https://www.wikidata.org/wiki/{ent['qid']}",
        ))
    return ev, ("ok" if ev else "empty")


# ─── Feed: SEC 8-K material events (full-text search) → Recent News ───────────

def fetch_sec_8k(company_name: str, dl: Optional[Deadline] = None):
    """Most recent 8-K material-event filings — legally-mandated, primary-source news."""
    dl = as_deadline(dl, 15)

    def _produce():
        q = quote(f'"{company_name}"')
        url = f"https://efts.sec.gov/LATEST/search-index?q={q}&forms=8-K"
        r = _get(url, dl, headers=_SEC_UA)
        if not r or r.status_code != 200:
            return None
        try:
            hits = r.json().get("hits", {}).get("hits", [])
        except Exception:
            return None
        target = _norm_name(company_name)
        rows = []
        for h in hits:
            src = h.get("_source", {})
            names = src.get("display_names", [])
            # Keep only filings actually filed BY this company — full-text search
            # matches the string anywhere in a document, including third parties.
            # Match on the filer name before the "(" (ticker/CIK), punctuation-insensitive.
            filer_core = names[0].split("(")[0] if names else ""
            if not target or target not in _norm_name(filer_core):
                continue
            _id = h.get("_id", "")
            cik = (src.get("ciks") or ["0"])[0]
            accession, _, doc = _id.partition(":")
            filing_url = ""
            if accession and cik:
                filing_url = (f"https://www.sec.gov/Archives/edgar/data/"
                              f"{int(cik)}/{accession.replace('-', '')}/{doc}")
            rows.append({
                "date": src.get("file_date", ""),
                "filer": (names[0].split("(")[0].strip() if names else company_name),
                "url": filing_url,
            })
        # Most recent material events first (hits arrive relevance-sorted).
        rows.sort(key=lambda x: x["date"], reverse=True)
        return rows[:6]

    hits = cache.cached("sec8k", company_name.lower(), 6 * 3600, _produce)
    if hits is None:
        return [], "failed:sec_unreachable"
    if not hits:
        return [], "empty"
    ev = [Evidence(
        text=f"SEC 8-K material-event filing by {h['filer']} filed {h['date']}",
        source_type="SEC 8-K", tier="A", source_url=h["url"], published_at=h["date"],
    ) for h in hits]
    return ev, "ok"


# ─── Feed: Google News RSS → Recent News ─────────────────────────────────────

def fetch_google_news(company_name: str, dl: Optional[Deadline] = None):
    """
    Fresh dated headlines. NOTE: the RSS <link> is an opaque news.google.com redirect
    (base64 decoding was disabled by Google in 2024), so we cite the PUBLISHER name and
    publish date from the item metadata, never the redirect URL — otherwise every
    citation would point at Google-redirect garbage instead of a real source.
    """
    dl = as_deadline(dl, 12)

    def _produce():
        q = quote(company_name)
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        if not _XML_SAFE:
            logger.warning("defusedxml not installed — skipping Google News RSS to avoid unsafe XML parsing")
            return None
        r = _get(url, dl)
        if not r or r.status_code != 200 or not r.text.strip().startswith("<?xml"):
            return None
        try:
            root = _xml_fromstring(r.text)
        except _XMLParseError:
            return None
        items = []
        for item in root.findall(".//item")[:8]:
            title = (item.findtext("title") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            source_el = item.find("{http://search.yahoo.com/mrss/}credit") or item.find("source")
            publisher = (source_el.text if source_el is not None and source_el.text else "").strip()
            if title:
                items.append({"title": title, "pubDate": pub, "publisher": publisher})
        return items

    items = cache.cached("gnews", company_name.lower(), 3 * 3600, _produce)
    if items is None:
        return [], "failed:gnews_unreachable"
    if not items:
        return [], "empty"
    ev = []
    for it in items:
        pub = it.get("publisher") or "news"
        # published_at kept human (RFC-822 from RSS); citation points at the publisher,
        # not the un-decodable Google redirect link.
        ev.append(Evidence(
            text=f"{it['title']} — {pub}",
            source_type=f"Google News ({pub})", tier="C",
            source_url="", published_at=it.get("pubDate", ""),
        ))
    return ev, "ok"


# ─── Feed: GitHub org (engineering-traction signal) → Business Model ──────────

def fetch_github(company_name: str, domain: str, dl: Optional[Deadline] = None):
    """Public-repo footprint as an engineering-traction signal (esp. for startups)."""
    dl = as_deadline(dl, 10)
    # Guess the org slug from the domain first (more reliable than the display name).
    host = _host(domain)
    candidates = []
    if host:
        candidates.append(host.split(".")[0])
    slug = "".join(c for c in company_name.lower() if c.isalnum())
    if slug and slug not in candidates:
        candidates.append(slug)

    def _produce():
        for org in candidates:
            r = _get(f"https://api.github.com/orgs/{quote(org)}", dl,
                     headers={"User-Agent": _UA, "Accept": "application/vnd.github+json"})
            if r is not None and r.status_code == 200:
                try:
                    d = r.json()
                except Exception:
                    continue
                return {
                    "login": d.get("login", org),
                    "public_repos": d.get("public_repos", 0),
                    "followers": d.get("followers", 0),
                    "url": d.get("html_url", f"https://github.com/{org}"),
                    "name": d.get("name", ""),
                }
        return None

    d = cache.cached("github", (host or slug), 7 * 86400, _produce)
    if not d:
        return [], "empty"
    ev = [Evidence(
        text=(f"GitHub org @{d['login']} — {d['public_repos']} public repos, "
              f"{d['followers']} followers (engineering-traction signal)"),
        source_type="GitHub", tier="E", source_url=d["url"],
    )]
    return ev, "ok"


# ─── Feed: Hacker News Algolia (tech-community sentiment) → News/Competitive ──

def fetch_hackernews(company_name: str, dl: Optional[Deadline] = None):
    dl = as_deadline(dl, 10)

    def _produce():
        q = quote(company_name)
        url = f"https://hn.algolia.com/api/v1/search?query={q}&tags=story&hitsPerPage=5"
        r = _get(url, dl)
        if not r or r.status_code != 200:
            return None
        try:
            hits = r.json().get("hits", [])
        except Exception:
            return None
        out = []
        for h in hits:
            title = h.get("title") or h.get("story_title")
            if not title:
                continue
            out.append({
                "title": title,
                "points": h.get("points", 0),
                "comments": h.get("num_comments", 0),
                "date": (h.get("created_at") or "")[:10],
                "url": f"https://news.ycombinator.com/item?id={h.get('objectID', '')}",
            })
        return out

    hits = cache.cached("hackernews", company_name.lower(), 6 * 3600, _produce)
    if hits is None:
        return [], "failed:hn_unreachable"
    if not hits:
        return [], "empty"
    ev = [Evidence(
        text=f"HN discussion: {h['title']} ({h['points']} points, {h['comments']} comments)",
        source_type="Hacker News", tier="E", source_url=h["url"], published_at=h["date"],
    ) for h in hits]
    return ev, "ok"
