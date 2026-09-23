"""
Web Scraper Agent
-----------------
Crawls a company website and extracts content from key pages.
Handles both static HTML and JavaScript-rendered pages.
"""

import time
import logging
from urllib.parse import urljoin, urlparse
from typing import Optional

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

from config import (
    MAX_PAGES_TO_SCRAPE,
    MAX_CONTENT_LENGTH,
    REQUEST_TIMEOUT,
    REQUEST_DELAY,
    RELEVANT_PAGE_KEYWORDS,
)

logger = logging.getLogger(__name__)

# curl_cffi handles real Chrome TLS fingerprint — no need to set headers manually
HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Check visible challenge language, not metadata such as <meta name="robots">.
_BLOCK_SIGNALS = [
    "verify you are human", "verify that you are human", "are you a human",
    "are you human", "checking your browser", "please wait while we check",
    "access denied", "complete the captcha", "security verification",
]


def _is_blocked(text: str) -> bool:
    soup = BeautifulSoup(text, "lxml")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    for tag in soup.find_all(["script", "style", "meta", "noscript"]):
        tag.decompose()
    visible = soup.get_text(" ", strip=True).lower()[:4000]
    return (title in {"just a moment...", "access denied", "attention required! | cloudflare"}
            or any(signal in visible for signal in _BLOCK_SIGNALS))


def format_scrape_status(pages: int, source_status: str) -> str:
    if pages > 0:
        return f"✅ Analyzed **{pages} pages**"
    if "blocked" in source_status:
        reason = "site blocked automated access"
    elif "timeout" in source_status:
        reason = "website request timed out"
    else:
        reason = "website unavailable or no readable content"
    return f"⚠️ Website: **0 pages read** — {reason}"


# Tags to strip (noise)
NOISE_TAGS = ["script", "style", "noscript", "svg", "img", "video",
               "iframe", "form", "nav", "footer", "header", "aside",
               "cookie", "advertisement"]

SEMANTIC_TAGS = ["main", "article", "section", "p", "h1", "h2", "h3",
                  "h4", "li", "td", "th", "blockquote", "figcaption"]


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/")


def _get_base_domain(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _is_same_domain(url: str, base: str) -> bool:
    return urlparse(url).netloc == urlparse(base).netloc


def _clean_text(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()

    # Try to get main content area first
    main_content = (
        soup.find("main")
        or soup.find(id="main")
        or soup.find(id="content")
        or soup.find(class_="content")
        or soup.find(class_="main-content")
        or soup.body
        or soup
    )

    lines = []
    for el in main_content.find_all(SEMANTIC_TAGS):
        text = el.get_text(separator=" ", strip=True)
        if text and len(text) > 20:  # skip tiny fragments
            lines.append(text)

    raw = "\n".join(lines)
    # Collapse whitespace
    import re
    raw = re.sub(r"\s{3,}", "\n\n", raw)
    return raw[:MAX_CONTENT_LENGTH]


def _fetch_page(url: str, session: cffi_requests.Session, failures: Optional[list] = None) -> Optional[dict]:
    failures = failures if failures is not None else []
    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            logger.warning(f"HTTP {resp.status_code} for {url}")
            failures.append("blocked_http_" + str(resp.status_code) if resp.status_code in (401, 403, 429) else "http_" + str(resp.status_code))
            return None
        # A challenge is an unavailable source; do not attempt to bypass it.
        if _is_blocked(resp.text):
            logger.warning("Website challenge at %s (HTTP %s)", url, resp.status_code)
            failures.append("blocked_challenge")
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        title = soup.title.string.strip() if soup.title and soup.title.string else url
        text = _clean_text(soup)
        return {"url": resp.url, "title": title, "content": text}
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        failures.append("timeout" if "timeout" in str(e).lower() else type(e).__name__)
        return None


def _extract_internal_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base_domain = _get_base_domain(base_url)
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        full_url = urljoin(base_url, href).split("?")[0].split("#")[0].rstrip("/")
        if _is_same_domain(full_url, base_domain):
            links.add(full_url)
    return list(links)


def _score_page_relevance(url: str) -> int:
    path = urlparse(url).path.lower()
    score = 0
    for kw in RELEVANT_PAGE_KEYWORDS:
        if kw in path:
            score += 2
    # Prefer shorter paths (less nested pages)
    depth = path.count("/")
    score -= depth
    return score


def _try_playwright_fallback(url: str) -> Optional[str]:
    """Try JS rendering with Playwright if requests gave very little content."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.goto(url, timeout=20000, wait_until="networkidle")
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        logger.warning(f"Playwright fallback failed: {e}")
        return None


def _ddg_find_website(company_name: str) -> Optional[str]:
    """Search DuckDuckGo to find a company's official website URL."""
    try:
        from ddgs import DDGS
        ddgs = DDGS(timeout=15)
        results = list(ddgs.text(f"{company_name} official website", max_results=5))
        for r in results:
            href = r.get("href", "")
            # Skip news aggregators and social media
            if href and not any(x in href for x in [
                "wikipedia", "linkedin", "twitter", "facebook",
                "instagram", "youtube", "crunchbase", "bloomberg",
                "techcrunch", "wsj", "forbes", "reuters",
            ]):
                # Search results often point to a product/drivers subpage.
                # Start the company crawl at the discovered origin instead.
                parsed = urlparse(href)
                if parsed.scheme in ("https", "http") and parsed.netloc:
                    return f"{parsed.scheme}://{parsed.netloc}"
    except Exception as e:
        logger.debug(f"DDG website search failed: {e}")
    return None


def scrape_website(url: str, deadline=None) -> dict:
    """
    Main entry point for the web scraper agent.
    Accepts either a real URL (https://nvidia.com) or a guessed slug URL
    (https://www.nvidia.com) — will fall back to DDG search to find the real site.
    Returns a dict with:
      - company_name: guessed from title/meta
      - pages: list of {url, title, content}
      - combined_text: all page text joined

    `deadline` (agents.deadline.Deadline) is checked cooperatively in the page-crawl
    loop so a slow site can't blow the pipeline's overall budget; partial pages are
    still returned.
    """
    from agents.deadline import as_deadline
    dl = as_deadline(deadline, 90)
    url = _normalize_url(url)
    session = cffi_requests.Session(impersonate="chrome131")
    scraped = []
    visited = set()
    failures = []

    # --- Step 1: Scrape homepage ---
    home_data = _fetch_page(url, session, failures)
    if not home_data:
        # Try with trailing slash
        home_data = _fetch_page(url + "/", session, failures)
    if not home_data:
        # Guessed URL didn't work — try to find the real website via DDG
        # Extract company name from the guessed URL slug (www.COMPANY.com)
        from urllib.parse import urlparse as _up
        slug = _up(url).netloc.replace("www.", "").split(".")[0]
        real_url = _ddg_find_website(slug)
        if real_url:
            logger.info(f"DDG found website for '{slug}': {real_url}")
            url = real_url
            home_data = _fetch_page(url, session, failures)
    if not home_data:
        return {"company_name": "", "pages": [], "combined_text": "", "pages_scraped": 0,
                "source_status": "failed:" + (failures[-1] if failures else "unavailable")}

    scraped.append(home_data)
    visited.add(home_data["url"])

    # Extract company name from homepage title
    company_name = home_data["title"].split("|")[0].split("-")[0].split("–")[0].strip()

    # Check if JS rendering needed (too little content on homepage)
    if len(home_data["content"]) < 300:
        html_fallback = _try_playwright_fallback(url)
        if html_fallback and not _is_blocked(html_fallback):
            soup = BeautifulSoup(html_fallback, "lxml")
            home_data["content"] = _clean_text(soup)
            scraped[0] = home_data

    # Do not count an empty HTML shell as a successfully read page.
    if not home_data["content"].strip():
        return {"company_name": company_name, "pages": [], "combined_text": "",
                "pages_scraped": 0, "source_status": "failed:no_readable_content"}

    # --- Step 2: Get all internal links ---
    home_resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    all_links = _extract_internal_links(home_resp.text, url)

    # Also try sitemap
    sitemap_links = _try_sitemap(url, session)
    all_links = list(set(all_links + sitemap_links))

    # Sort by relevance score
    all_links.sort(key=_score_page_relevance, reverse=True)

    # --- Step 3: Scrape top relevant pages ---
    for page_url in all_links:
        if len(scraped) >= MAX_PAGES_TO_SCRAPE:
            break
        if dl.expired():
            logger.info(f"Web scraper hit deadline after {len(scraped)} pages; returning partial.")
            break
        if page_url in visited:
            continue
        # Skip file downloads
        ext = urlparse(page_url).path.split(".")[-1].lower()
        if ext in ["pdf", "png", "jpg", "jpeg", "gif", "zip", "svg", "mp4", "mp3"]:
            continue

        time.sleep(REQUEST_DELAY)
        page_data = _fetch_page(page_url, session)
        if page_data and len(page_data["content"]) > 100:
            scraped.append(page_data)
            visited.add(page_url)

    # --- Step 4: Combine all content ---
    combined = []
    for p in scraped:
        combined.append(f"### [{p['title']}]({p['url']})\n{p['content']}")
    combined_text = "\n\n---\n\n".join(combined)

    return {
        "company_name": company_name,
        "pages": scraped,
        "combined_text": combined_text,
        "pages_scraped": len(scraped),
        "source_status": "ok",
    }


def _try_sitemap(base_url: str, session: cffi_requests.Session) -> list[str]:
    """Try common sitemap URLs to get more page links."""
    sitemap_urls = [
        f"{_get_base_domain(base_url)}/sitemap.xml",
        f"{_get_base_domain(base_url)}/sitemap_index.xml",
    ]
    links = []
    for sm_url in sitemap_urls:
        try:
            resp = session.get(sm_url, headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                for loc in soup.find_all("loc"):
                    lurl = loc.get_text().strip().rstrip("/")
                    if _is_same_domain(lurl, base_url):
                        links.append(lurl)
                if links:
                    break
        except Exception:
            pass
    return links[:50]
