"""
Financial Analyst Agent
-----------------------
Uses curl_cffi (Chrome impersonation) to fetch Yahoo Finance data reliably,
bypassing bot-detection and rate limits.
Falls back to stooq (via pandas_datareader) for price history if Yahoo is slow.
"""

import logging
import re
import time
import warnings
from datetime import datetime, timedelta, timezone
from typing import Optional

from curl_cffi import requests as cffi_requests
from ddgs import DDGS

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# ─── Session (Chrome impersonation) ─────────────────────────────────────────
# Mutable state in a dict — avoids `global` declarations and linter warnings.

_STATE: dict = {"session": None, "crumb": None}


def _get_session() -> cffi_requests.Session:
    if _STATE["session"] is None:
        _STATE["session"] = cffi_requests.Session(impersonate="chrome131")
        try:
            _STATE["session"].get("https://finance.yahoo.com/", timeout=8)
        except Exception:
            pass
    return _STATE["session"]


def _get_crumb() -> Optional[str]:
    if _STATE["crumb"]:
        return _STATE["crumb"]
    try:
        s = _get_session()
        r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=8)
        if r.status_code == 200 and len(r.text) > 3 and "Too Many" not in r.text:
            _STATE["crumb"] = r.text.strip()
            logger.info("Yahoo Finance crumb obtained.")
    except Exception as e:
        logger.debug(f"Crumb fetch failed: {e}")
    return _STATE["crumb"]


def _reset_crumb():
    _STATE["crumb"] = None


def _yf_get(url: str, retries: int = 3, delay: float = 2.0):
    s = _get_session()
    for attempt in range(retries):
        try:
            r = s.get(url, timeout=12)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                wait = delay * (2 ** attempt)
                logger.warning(f"429 rate limit, waiting {wait:.0f}s (attempt {attempt+1})")
                time.sleep(wait)
                _reset_crumb()
            else:
                logger.debug(f"Non-200 status {r.status_code} for {url}")
                return None
        except Exception as e:
            logger.debug(f"Request error ({attempt+1}/{retries}): {e}")
            time.sleep(delay)
    return None


# ─── Ticker Detection ────────────────────────────────────────────────────────

# Only companies we KNOW are private — everything else goes through dynamic lookup.
# This avoids false "not public" results and keeps the code independent of any list.
KNOWN_PRIVATE: set = {
    "openai", "stripe", "figma", "notion", "databricks", "anthropic",
    "github", "slack", "canva", "epic games", "spacex", "klarna",
    "linkedin", "indeed", "bamboohr", "rippling", "gusto", "fidelity",
    "mixpanel", "segment", "linode", "vultr", "darktrace",
    "bytedance", "tiktok", "shein", "revolut", "chime", "plaid",
    "instacart", "reddit", "discord", "telegram", "signal",
}


def _validate_ticker(ticker: str) -> bool:
    r = _yf_get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d",
        retries=2, delay=1.5
    )
    if not r:
        return False
    try:
        d = r.json()
        result = d.get("chart", {}).get("result")
        return bool(result and result[0].get("meta", {}).get("regularMarketPrice"))
    except Exception:
        return False


def _ddg_ticker_search(company_name: str) -> Optional[str]:
    try:
        ddgs = DDGS(timeout=15)
        query = f'"{company_name}" stock ticker symbol NYSE NASDAQ'
        results = list(ddgs.text(query, max_results=6))
        for r in results:
            text = r.get("body", "") + " " + r.get("title", "") + " " + r.get("href", "")
            for pat in [r'quote/([A-Z]{1,5})', r'\(([A-Z]{1,5})\)',
                        r'NASDAQ:\s*([A-Z]{1,5})', r'NYSE:\s*([A-Z]{1,5})']:
                m = re.search(pat, text)
                if m:
                    candidate = m.group(1)
                    if len(candidate) >= 2 and _validate_ticker(candidate):
                        return candidate
        time.sleep(0.5)
    except Exception as e:
        logger.warning(f"DDG ticker search failed: {e}")
    return None


_CORP_SUFFIXES = (
    " inc", " inc.", " corp", " corp.", " corporation", " ltd", " ltd.",
    " llc", " co.", " co,", " plc", " group", " holdings", " holding",
    " technologies", " technology", " solutions", " services", " systems",
    " international", " global", " limited", " ventures",
)


def _name_score(query: str, result_name: str) -> float:
    """
    Score 0–1 how well a YF result name matches the query.
    Uses multi-level matching so obscure / short company names work reliably.
    """
    q = query.lower().strip()
    r = result_name.lower().strip()

    # Strip corporate suffixes from result for cleaner matching
    r_clean = r
    for sfx in _CORP_SUFFIXES:
        r_clean = r_clean.replace(sfx, "")
    r_clean = r_clean.strip()

    if not q or not r_clean:
        return 0.0

    # Exact match
    if q == r_clean:
        return 1.0
    # Result starts with query (e.g. "upwork" → "upwork inc")
    if r_clean.startswith(q):
        return 0.95
    # Query contained in result
    if q in r_clean:
        return 0.90
    # Result contained in query (short tickers / acronyms)
    if r_clean in q:
        return 0.85

    # Word-level: how many meaningful query words appear in result
    q_words = [w for w in q.split() if len(w) > 2]
    if not q_words:
        return 0.5 if q[:3] in r_clean else 0.0
    matched = sum(1 for w in q_words if w in r_clean)
    return matched / len(q_words)


def _yf_search_ticker(company_name: str) -> Optional[str]:
    """
    Universal ticker finder via Yahoo Finance's autocomplete API.
    Works for any publicly traded company worldwide — no hardcoded list needed.
    """
    try:
        s = _get_session()
        query = company_name.replace(" ", "+")
        r = s.get(
            f"https://query2.finance.yahoo.com/v1/finance/search"
            f"?q={query}&lang=en-US&region=US&quotesCount=8&newsCount=0",
            timeout=8,
        )
        if r.status_code != 200:
            return None

        quotes = r.json().get("quotes", [])
        best_ticker: Optional[str] = None
        best_score: float = 0.0

        for q in quotes:
            # Only equities (skip funds, futures, crypto, forex)
            if q.get("quoteType") not in ("EQUITY",):
                continue
            ticker = q.get("symbol", "")
            if not ticker or len(ticker) > 6:
                continue
            # Skip complex instruments: warrants (ends in W), units (U), rights
            if ticker.endswith(("W", ".WS", ".RT", ".U")):
                continue
            # Prefer US-listed tickers — skip non-US exchange suffixes (.MX, .TO, .L, etc.)
            if "." in ticker:
                continue

            result_name = q.get("shortname") or q.get("longname") or ""
            score = _name_score(company_name, result_name)

            logger.debug(f"  YF candidate: {ticker} | '{result_name}' | score={score:.2f}")

            if score > best_score:
                best_score = score
                best_ticker = ticker

        # Accept if there's a confident match
        if best_ticker and best_score >= 0.5:
            if _validate_ticker(best_ticker):
                logger.info(f"YF search → '{best_ticker}' for '{company_name}' (score={best_score:.2f})")
                return best_ticker

        # Lower threshold when YF returns very few results (less ambiguity)
        if best_ticker and best_score >= 0.3 and len(quotes) <= 3:
            if _validate_ticker(best_ticker):
                logger.info(f"YF search (low-conf) → '{best_ticker}' for '{company_name}' (score={best_score:.2f})")
                return best_ticker

    except Exception as e:
        logger.debug(f"YF search ticker failed: {e}")
    return None


def find_ticker(company_name: str) -> Optional[str]:
    """
    Universal ticker resolution. Works for any company without a hardcoded list.

    Priority:
    1. Known-private set  → return None immediately
    2. Direct ticker input (all-caps, 1–5 chars)
    3. Yahoo Finance search API  → handles any public company worldwide
    4. DDG text search (last resort)
    """
    name_lower = company_name.lower().strip()

    # 1. Known-private companies — skip all lookups
    for priv in KNOWN_PRIVATE:
        if priv in name_lower or name_lower == priv:
            logger.info(f"'{company_name}' is known-private, skipping ticker lookup")
            return None

    # 2. User typed a ticker directly (e.g. "AAPL", "UPWK")
    if re.match(r'^[A-Z]{1,5}$', company_name):
        if _validate_ticker(company_name):
            return company_name

    # 3. Yahoo Finance search — primary, universal
    yf_result = _yf_search_ticker(company_name)
    if yf_result:
        return yf_result

    # 4. DDG fallback
    return _ddg_ticker_search(company_name)


# ─── Yahoo Finance Data Fetchers ─────────────────────────────────────────────

def fetch_stock_history(ticker: str) -> Optional[dict]:
    r = _yf_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1y")
    if not r:
        return _stooq_fallback(ticker)
    try:
        d = r.json()
        result = d["chart"]["result"][0]
        timestamps = result["timestamp"]
        q = result["indicators"]["quote"][0]
        valid = [
            (datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"), c, h, l)
            for ts, c, h, l in zip(timestamps, q.get("close", []),
                                   q.get("high", []), q.get("low", []))
            if c is not None
        ]
        if not valid:
            return _stooq_fallback(ticker)
        return {
            "dates":  [x[0] for x in valid],
            "closes": [round(x[1], 2) for x in valid],
            "highs":  [round(x[2], 2) for x in valid],
            "lows":   [round(x[3], 2) for x in valid],
        }
    except Exception as e:
        logger.warning(f"History parse error {ticker}: {e}")
        return _stooq_fallback(ticker)


def _stooq_fallback(ticker: str) -> Optional[dict]:
    """Stooq via pandas_datareader — no rate limits."""
    try:
        import pandas_datareader as pdr
        end = datetime.today()
        start = end - timedelta(days=365)
        df = pdr.data.DataReader(f"{ticker}.US", "stooq", start=start, end=end)
        if df.empty:
            return None
        df = df.sort_index()
        return {
            "dates":  [d.strftime("%Y-%m-%d") for d in df.index],
            "closes": [round(float(c), 2) for c in df["Close"]],
            "highs":  [round(float(h), 2) for h in df["High"]],
            "lows":   [round(float(l), 2) for l in df["Low"]],
        }
    except Exception as e:
        logger.warning(f"Stooq fallback failed for {ticker}: {e}")
        return None


def fetch_quote_summary(ticker: str) -> dict:
    crumb = _get_crumb()
    modules = "price,financialData,defaultKeyStatistics,assetProfile,summaryDetail"
    crumb_param = f"&crumb={crumb}" if crumb else ""
    for base in ["query2", "query1"]:
        r = _yf_get(f"https://{base}.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
                    f"?modules={modules}{crumb_param}")
        if r:
            try:
                return r.json()["quoteSummary"]["result"][0]
            except Exception:
                continue
    return {}


def fetch_quarterly_financials(ticker: str) -> dict:
    crumb = _get_crumb()
    crumb_param = f"&crumb={crumb}" if crumb else ""
    module = "incomeStatementHistoryQuarterly"
    for base in ["query2", "query1"]:
        r = _yf_get(f"https://{base}.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
                    f"?modules={module}{crumb_param}")
        if r:
            try:
                stmts = r.json()["quoteSummary"]["result"][0][module]["incomeStatementHistory"]
                revenue, net_income, dates = [], [], []
                for s in reversed(stmts):
                    dates.append(s.get("endDate", {}).get("fmt", "?"))
                    revenue.append(_raw_billions(s, "totalRevenue"))
                    net_income.append(_raw_billions(s, "netIncome"))
                return {
                    "revenue":    {"dates": dates, "values": revenue},
                    "net_income": {"dates": dates, "values": net_income},
                }
            except Exception as e:
                logger.debug(f"Quarterly parse error: {e}")
                continue
    return {}


def _raw_billions(stmt: dict, key: str) -> Optional[float]:
    v = stmt.get(key, {})
    if isinstance(v, dict) and v.get("raw") is not None:
        return round(float(v["raw"]) / 1e9, 4)
    return None


# ─── Parse & Format ──────────────────────────────────────────────────────────

def _safe(d: dict, *keys, default="N/A"):
    v = d
    for k in keys:
        if not isinstance(v, dict):
            return default
        v = v.get(k, default)
        if v is default or v is None:
            return default
    if isinstance(v, dict):
        return v.get("fmt") or v.get("raw") or default
    return v if v not in ("", 0, None) else default


def _pct(val) -> str:
    try:
        return f"{float(val) * 100:.1f}%"
    except Exception:
        return "N/A"


def build_raw_data(ticker: str, qs: dict) -> dict:
    pr = qs.get("price", {})
    fd = qs.get("financialData", {})
    ks = qs.get("defaultKeyStatistics", {})
    ap = qs.get("assetProfile", {})
    sd = qs.get("summaryDetail", {})
    return {
        "ticker":                 ticker,
        "company_name":           _safe(pr, "longName"),
        "sector":                 _safe(ap, "sector"),
        "industry":               _safe(ap, "industry"),
        "country":                _safe(ap, "country"),
        "employees":              _safe(ap, "fullTimeEmployees"),
        "exchange":               _safe(pr, "exchangeName"),
        # Valuation
        "market_cap":             _safe(pr, "marketCap", "fmt"),
        "market_cap_raw":         _safe(pr, "marketCap", "raw"),
        "enterprise_value":       _safe(ks, "enterpriseValue", "fmt"),
        "revenue_ttm":            _safe(fd, "totalRevenue", "fmt"),
        "gross_profit":           _safe(fd, "grossProfits", "fmt"),
        "ebitda":                 _safe(fd, "ebitda", "fmt"),
        "net_income":             _safe(ks, "netIncomeToCommon", "fmt"),
        "operating_cash_flow":    _safe(fd, "operatingCashflow", "fmt"),
        "cash":                   _safe(fd, "totalCash", "fmt"),
        "debt":                   _safe(fd, "totalDebt", "fmt"),
        # Multiples
        "pe_ratio":               _safe(sd, "trailingPE"),
        "forward_pe":             _safe(ks, "forwardPE"),
        "ev_ebitda":              _safe(ks, "enterpriseToEbitda"),
        "price_to_sales":         _safe(ks, "priceToSalesTrailing12Months"),
        "eps_trailing":           _safe(ks, "trailingEps"),
        "eps_forward":            _safe(ks, "forwardEps"),
        # Growth & Margins
        "revenue_growth_yoy":     _pct(_safe(fd, "revenueGrowth", "raw")),
        "earnings_growth_yoy":    _pct(_safe(fd, "earningsGrowth", "raw")),
        "gross_margin":           _pct(_safe(fd, "grossMargins", "raw")),
        "profit_margin":          _pct(_safe(fd, "profitMargins", "raw")),
        "operating_margin":       _pct(_safe(fd, "operatingMargins", "raw")),
        # Returns
        "roe":                    _pct(_safe(fd, "returnOnEquity", "raw")),
        "roa":                    _pct(_safe(fd, "returnOnAssets", "raw")),
        # Balance sheet health (TTM from financialData)
        "d_e_ratio":              _safe(fd, "debtToEquity"),
        "current_ratio":          _safe(fd, "currentRatio"),
        "free_cashflow":          _safe(fd, "freeCashflow", "fmt"),
        # Stock info
        "current_price":          _safe(fd, "currentPrice"),
        "52w_high":               _safe(sd, "fiftyTwoWeekHigh"),
        "52w_low":                _safe(sd, "fiftyTwoWeekLow"),
        "analyst_target":         _safe(fd, "targetMeanPrice"),
        "analyst_recommendation": _safe(fd, "recommendationKey"),
        "beta":                   _safe(sd, "beta"),
        "dividend_yield":         _pct(_safe(sd, "dividendYield", "raw")),
        # Trader-specific fields
        "peg_ratio":              _safe(ks, "pegRatio"),
        "price_to_book":          _safe(ks, "priceToBook"),
        "short_ratio":            _safe(ks, "shortRatio"),
        "week52_change":          _pct(_safe(ks, "52WeekChange", "raw")),
        "target_high":            _safe(fd, "targetHighPrice"),
        "target_low":             _safe(fd, "targetLowPrice"),
        "analyst_count":          _safe(fd, "numberOfAnalystOpinions", "raw"),
        "payout_ratio":           _pct(_safe(sd, "payoutRatio", "raw")),
        "shares_outstanding":     _safe(ks, "sharesOutstanding", "fmt"),
        "fcf_raw":                _safe(fd, "freeCashflow", "raw"),
    }


def format_public_data(rd: dict) -> str:
    return "\n".join([
        f"**Ticker:** {rd.get('ticker','N/A')} ({rd.get('exchange','N/A')})",
        f"**Sector:** {rd.get('sector','N/A')} | **Industry:** {rd.get('industry','N/A')}",
        f"**Country:** {rd.get('country','N/A')} | **Employees:** {rd.get('employees','N/A')}",
        "",
        "**Valuation:**",
        f"- Market Cap: {rd.get('market_cap','N/A')} | Enterprise Value: {rd.get('enterprise_value','N/A')}",
        f"- Revenue TTM: {rd.get('revenue_ttm','N/A')} | EBITDA: {rd.get('ebitda','N/A')}",
        f"- Net Income: {rd.get('net_income','N/A')} | Operating CF: {rd.get('operating_cash_flow','N/A')}",
        f"- Cash: {rd.get('cash','N/A')} | Debt: {rd.get('debt','N/A')}",
        "",
        "**Multiples:**",
        f"- P/E: {rd.get('pe_ratio','N/A')} | Forward P/E: {rd.get('forward_pe','N/A')}",
        f"- EV/EBITDA: {rd.get('ev_ebitda','N/A')} | P/S: {rd.get('price_to_sales','N/A')}",
        f"- EPS (TTM): {rd.get('eps_trailing','N/A')} | Forward EPS: {rd.get('eps_forward','N/A')}",
        "",
        "**Margins & Growth (YoY):**",
        f"- Gross Margin: {rd.get('gross_margin','N/A')}",
        f"- Operating Margin: {rd.get('operating_margin','N/A')}",
        f"- Net Margin: {rd.get('profit_margin','N/A')}",
        f"- Revenue Growth: {rd.get('revenue_growth_yoy','N/A')}",
        f"- Earnings Growth: {rd.get('earnings_growth_yoy','N/A')}",
        "",
        "**Returns:**",
        f"- ROE: {rd.get('roe','N/A')} | ROA: {rd.get('roa','N/A')}",
        "",
        "**Stock:**",
        f"- Price: {rd.get('current_price','N/A')} | 52W: {rd.get('52w_low','N/A')}–{rd.get('52w_high','N/A')}",
        f"- Beta: {rd.get('beta','N/A')} | Target: {rd.get('analyst_target','N/A')}",
        f"- Recommendation: {rd.get('analyst_recommendation','N/A')}",
    ])


# ─── Annual Historical Financials ────────────────────────────────────────────

def fetch_annual_financials(ticker: str) -> dict:
    """
    Fetch annual revenue + earnings via the 'earnings' module (reliable),
    plus income-statement history for any supplemental fields.
    Returns a dict with aligned lists keyed by fiscal year.

    Note: Yahoo Finance's balanceSheetHistory and cashflowStatementHistory
    modules currently return near-empty data for most tickers. TTM values
    (FCF, margins, D/E, Current Ratio) are sourced from 'financialData' in
    build_raw_data() and stored on raw_data — not duplicated here.
    """
    crumb = _get_crumb()
    crumb_param = f"&crumb={crumb}" if crumb else ""

    def _b_raw(raw_val) -> Optional[float]:
        """Convert a raw numeric value (int/float) to billions."""
        if raw_val is not None:
            try:
                return round(float(raw_val) / 1e9, 4)
            except Exception:
                pass
        return None

    # ── Primary: earnings module (financialsChart.yearly) ────────────────
    yearly_items: list = []
    for base in ["query2", "query1"]:
        r = _yf_get(
            f"https://{base}.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=earnings{crumb_param}"
        )
        if r:
            try:
                earn = r.json()["quoteSummary"]["result"][0].get("earnings", {})
                yearly_items = earn.get("financialsChart", {}).get("yearly", [])
                if yearly_items:
                    break
            except Exception:
                continue

    # ── Supplemental: incomeStatementHistory (revenue + netIncome backup) ─
    inc_by_year: dict = {}
    for base in ["query2", "query1"]:
        r2 = _yf_get(
            f"https://{base}.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=incomeStatementHistory{crumb_param}"
        )
        if r2:
            try:
                stmts = (
                    r2.json()["quoteSummary"]["result"][0]
                    .get("incomeStatementHistory", {})
                    .get("incomeStatementHistory", [])
                )
                for s in stmts:
                    yr = s.get("endDate", {}).get("fmt", "")[:4]
                    if yr:
                        inc_by_year[yr] = s
                if inc_by_year:
                    break
            except Exception:
                continue

    # Merge: earnings module is authoritative for revenue + earnings
    # Build year-keyed dicts
    earn_by_year: dict = {}
    for item in yearly_items:
        yr = str(item.get("date", ""))
        if yr:
            earn_by_year[yr] = item

    years = sorted(set(earn_by_year) | set(inc_by_year))
    if not years:
        return {}

    def _b_stmt(stmt, key) -> Optional[float]:
        v = stmt.get(key, {})
        if isinstance(v, dict) and v.get("raw") is not None and v["raw"] != 0:
            return round(float(v["raw"]) / 1e9, 4)
        return None

    result: dict = {
        "years":     years,
        "revenue":   [],
        "net_income":[],
        "fcf":       [],      # filled with None (historical unavailable from API)
        "operating_cf": [],   # filled with None
        "gross_margin":     [],
        "operating_margin": [],
        "net_margin":       [],
        "d_e_ratio":    [],
        "current_ratio": [],
    }

    for yr in years:
        earn = earn_by_year.get(yr, {})
        inc  = inc_by_year.get(yr, {})

        # Revenue: earnings module > income statement
        rev_raw = earn.get("revenue", {}).get("raw") if earn else None
        if not rev_raw:
            rev_raw = inc.get("totalRevenue", {}).get("raw") if inc else None
        rev = _b_raw(rev_raw)

        # Net income: earnings module (called 'earnings') > income statement
        ni_raw = earn.get("earnings", {}).get("raw") if earn else None
        if not ni_raw:
            ni_raw = (
                inc.get("netIncomeApplicableToCommonShares", {}).get("raw")
                or inc.get("netIncome", {}).get("raw")
            ) if inc else None
        ni = _b_raw(ni_raw)

        # Net margin
        ni_m = round(ni / rev * 100, 1) if (ni is not None and rev) else None

        result["revenue"].append(rev)
        result["net_income"].append(ni)
        result["fcf"].append(None)          # historical cashflow unavailable from YF API
        result["operating_cf"].append(None)
        result["gross_margin"].append(None)     # TTM only, in raw_data
        result["operating_margin"].append(None) # TTM only, in raw_data
        result["net_margin"].append(ni_m)
        result["d_e_ratio"].append(None)        # TTM only, in raw_data
        result["current_ratio"].append(None)    # TTM only, in raw_data

    # Revenue CAGR
    rev_vals = [v for v in result["revenue"] if v]
    if len(rev_vals) >= 2:
        n = len(rev_vals) - 1
        try:
            cagr = ((rev_vals[-1] / rev_vals[0]) ** (1 / n) - 1) * 100
            result["revenue_cagr"] = round(cagr, 1)
        except Exception:
            result["revenue_cagr"] = None
    else:
        result["revenue_cagr"] = None

    logger.info(f"Annual financials fetched for {ticker}: {len(years)} years "
                f"(revenue CAGR: {result.get('revenue_cagr')}%)")
    return result


# ─── Competitor Intelligence ──────────────────────────────────────────────────

# Sector → curated peer tickers (top publicly traded competitors)
_SECTOR_PEERS: dict = {
    "Technology": {
        "Semiconductors": ["AMD", "INTC", "QCOM", "TSM", "AVGO", "ARM"],
        "Software—Application": ["MSFT", "ADBE", "CRM", "NOW", "WDAY"],
        "Software—Infrastructure": ["ORCL", "IBM", "MSFT", "VMW", "SNOW"],
        "Consumer Electronics": ["AAPL", "SONY", "SSNLF", "MSFT"],
        "Internet Content & Information": ["GOOGL", "META", "SNAP", "PINS"],
        "default": ["AAPL", "MSFT", "GOOGL", "META", "AMZN"],
    },
    "Communication Services": {
        "default": ["GOOGL", "META", "NFLX", "DIS", "SPOT", "SNAP"],
    },
    "Consumer Cyclical": {
        "default": ["AMZN", "TSLA", "NKE", "SBUX", "MCD"],
    },
    "Financial Services": {
        "default": ["JPM", "BAC", "GS", "MS", "V", "MA"],
    },
    "Healthcare": {
        "default": ["JNJ", "PFE", "ABBV", "MRK", "UNH"],
    },
    "Industrials": {
        "default": ["GE", "HON", "BA", "CAT", "MMM"],
    },
    "Energy": {
        "default": ["XOM", "CVX", "COP", "BP", "SHEL"],
    },
    "default": ["MSFT", "AAPL", "GOOGL", "AMZN", "META"],
}


def _get_sector_peers(ticker: str, sector: str, industry: str) -> list:
    """Return curated peer tickers based on sector/industry."""
    sector_map = _SECTOR_PEERS.get(sector, _SECTOR_PEERS["default"])
    if isinstance(sector_map, dict):
        peers = sector_map.get(industry, sector_map.get("default", []))
    else:
        peers = sector_map
    return [t for t in peers if t.upper() != ticker.upper()]


def _fetch_competitor_metrics(t: str) -> Optional[dict]:
    """Fetch key metrics for a competitor ticker."""
    crumb = _get_crumb()
    crumb_param = f"&crumb={crumb}" if crumb else ""
    r = _yf_get(
        f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{t}"
        f"?modules=price,financialData,defaultKeyStatistics{crumb_param}",
        retries=2, delay=1.0,
    )
    if not r:
        return None
    try:
        qs = r.json()["quoteSummary"]["result"][0]
    except Exception:
        return None

    pr = qs.get("price", {})
    fd = qs.get("financialData", {})
    ks = qs.get("defaultKeyStatistics", {})

    name = _safe(pr, "longName") or t
    if name == "N/A":
        name = t
    pe = _safe(ks, "trailingPE")
    if pe == "N/A":
        pe = _safe(ks, "forwardPE")
    return {
        "name":           name,
        "ticker":         t,
        "market_cap":     _safe(pr, "marketCap", "fmt"),
        "revenue":        _safe(fd, "totalRevenue", "fmt"),
        "gross_margin":   _pct(_safe(fd, "grossMargins", "raw")),
        "net_margin":     _pct(_safe(fd, "profitMargins", "raw")),
        "pe_ratio":       str(pe),
        "roe":            _pct(_safe(fd, "returnOnEquity", "raw")),
        "revenue_growth": _pct(_safe(fd, "revenueGrowth", "raw")),
    }


def find_and_fetch_competitors(company_name: str, ticker: str, sector: str,
                               industry: str = "") -> list:
    """
    Find up to 4 competitor tickers and fetch their metrics.

    Priority:
    1. Yahoo Finance 'recommendationsbyticker' API (fast, no crumb)
    2. Sector/industry-based curated peer list (reliable fallback)
    3. DDG search → /quote/TICKER URL pattern extraction
    Returns up to 4 competitor dicts.
    """
    if not ticker:
        return []

    competitors: list = []
    found_tickers: set = {ticker.upper()}

    def _add_from_list(tickers: list, label: str):
        for t in tickers:
            if len(competitors) >= 4:
                break
            if t.upper() in found_tickers:
                continue
            found_tickers.add(t.upper())
            metrics = _fetch_competitor_metrics(t)
            if metrics:
                competitors.append(metrics)
                time.sleep(0.25)
        if competitors:
            logger.info(f"Competitors via {label}: {[c['ticker'] for c in competitors]}")

    # ── 1. Yahoo Finance recommendations (short timeout) ────────────────────
    try:
        r = _yf_get(
            f"https://query2.finance.yahoo.com/v6/finance/recommendationsbyticker/{ticker}",
            retries=1, delay=1.0,
        )
        if r and r.status_code == 200:
            items = r.json().get("finance", {}).get("result", [{}])[0].get("recommendedSymbols", [])
            rec_tickers = [x["symbol"] for x in items[:6]]
            logger.info(f"YF recommended for {ticker}: {rec_tickers}")
            _add_from_list(rec_tickers, "YF recommendations")
    except Exception as e:
        logger.debug(f"YF recommendations failed: {e}")

    # ── 2. Sector/industry-based peer map ───────────────────────────────────
    if len(competitors) < 3:
        peer_tickers = _get_sector_peers(ticker, sector, industry)
        _add_from_list(peer_tickers, f"sector peers ({sector}/{industry})")

    # ── 3. DDG fallback: search → extract /quote/TICKER URL patterns ────────
    if len(competitors) < 2:
        try:
            ddgs = DDGS(timeout=15)
            query = f"{company_name} stock competitors peers {sector} publicly traded"
            results = list(ddgs.text(query, max_results=5))
            time.sleep(0.5)
            candidate_tickers: list = []
            for res in results:
                text = res.get("body", "") + " " + res.get("href", "")
                for m in re.finditer(r'/quote/([A-Z]{1,5})\b', text):
                    t = m.group(1)
                    if t not in found_tickers and len(t) >= 2:
                        candidate_tickers.append(t)
            seen: set = set()
            candidate_tickers = [t for t in candidate_tickers
                                 if not (t in seen or seen.add(t))]  # type: ignore
            valid_candidates = [t for t in candidate_tickers[:8] if _validate_ticker(t)]
            _add_from_list(valid_candidates, "DDG URL extraction")
        except Exception as e:
            logger.debug(f"DDG competitor fallback failed: {e}")

    logger.info(f"Total competitors found: {len(competitors)} → "
                f"{[c['ticker'] for c in competitors]}")
    return competitors


# ─── Private Company Fallback ────────────────────────────────────────────────

def search_private_financials(company_name: str) -> str:
    try:
        ddgs = DDGS(timeout=15)
        queries = [
            f"{company_name} total funding raised valuation 2024 2025",
            f"{company_name} Crunchbase funding rounds investors ARR",
            f"{company_name} annual revenue growth 2024",
        ]
        lines = ["**Private Company — Financial Data (public sources):**", ""]
        seen: set = set()
        for query in queries:
            try:
                for r in ddgs.text(query, max_results=3):
                    title = r.get("title", "").strip()
                    body = r.get("body", "").strip()[:400]
                    if body and title not in seen:
                        seen.add(title)
                        lines += [f"**{title}**", body, ""]
                time.sleep(0.8)
            except Exception:
                continue
        return "\n".join(lines) if len(lines) > 3 else "Financial data not publicly available."
    except Exception as e:
        logger.warning(f"Private financial search failed: {e}")
        return "Financial data could not be retrieved."


# ─── News Fetcher ────────────────────────────────────────────────────────────

# Source → badge background colour
_SOURCE_COLOURS: dict = {
    "Reuters": "#DC2626", "Bloomberg": "#1D3461", "Wall Street Journal": "#004276",
    "WSJ": "#004276", "Financial Times": "#CC5500", "CNBC": "#003B7A",
    "Forbes": "#CC0000", "TechCrunch": "#0A0A0A", "The Verge": "#FA4B2A",
    "Motley Fool": "#2E7D32", "Seeking Alpha": "#1DA462", "Investopedia": "#003B7A",
    "Business Insider": "#1A73E8", "Yahoo Finance": "#6001D2",
    "MarketWatch": "#FF5B00", "Barron's": "#B71C1C", "CNBC": "#003B7A",
    "AP": "#CC0000", "Associated Press": "#CC0000", "Guardian": "#005689",
    "Axios": "#FF4136", "Fortune": "#0C2461",
}


def _source_colour(publisher: str) -> str:
    return _SOURCE_COLOURS.get(publisher, "#6B7280")


def fetch_recent_news(company_name: str, ticker: Optional[str] = None) -> list:
    """
    Fetch up to 10 recent news items.
    Primary  : Yahoo Finance /v1/finance/search (public companies, by ticker)
    Secondary: Yahoo Finance /v1/finance/search (by company name)
    Fallback : DuckDuckGo news search
    Returns list of dicts: title, publisher, url, date, thumbnail, source_colour
    """
    items: list = []
    seen_titles: set = set()

    def _add(raw_items: list):
        for item in raw_items:
            title = item.get("title", "").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            ts   = item.get("providerPublishTime") or item.get("date_ts", 0)
            date = ""
            if ts:
                try:
                    date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%b %d, %Y")
                except Exception:
                    pass
            # Thumbnail: prefer 140x140 tag
            thumb = ""
            for res in (item.get("thumbnail") or {}).get("resolutions", []):
                if res.get("tag") == "140x140":
                    thumb = res.get("url", "")
                    break
            if not thumb:
                for res in (item.get("thumbnail") or {}).get("resolutions", []):
                    thumb = res.get("url", "")
                    break
            pub = item.get("publisher") or item.get("source") or ""
            items.append({
                "title":        title,
                "publisher":    pub,
                "url":          item.get("link") or item.get("url", "#"),
                "date":         date or item.get("date", "")[:10],
                "thumbnail":    thumb,
                "source_colour": _source_colour(pub),
            })
            if len(items) >= 10:
                break

    crumb = _get_crumb()
    crumb_param = f"&crumb={crumb}" if crumb else ""

    # 1. Yahoo Finance by ticker
    if ticker and len(items) < 10:
        r = _yf_get(
            f"https://query2.finance.yahoo.com/v1/finance/search"
            f"?q={ticker}&lang=en-US&region=US&newsCount=10&type=news{crumb_param}"
        )
        if r:
            try:
                _add(r.json().get("news", []))
            except Exception:
                pass

    # 2. Yahoo Finance by company name (helps private companies)
    if len(items) < 8:
        query = company_name.replace(" ", "+")
        r2 = _yf_get(
            f"https://query2.finance.yahoo.com/v1/finance/search"
            f"?q={query}&lang=en-US&region=US&newsCount=10&type=news{crumb_param}"
        )
        if r2:
            try:
                _add(r2.json().get("news", []))
            except Exception:
                pass

    # 3. DuckDuckGo news fallback
    if len(items) < 5:
        try:
            ddgs = DDGS(timeout=15)
            ddg_query = f"{company_name} company news deals partnerships 2025 2026"
            raw = list(ddgs.news(ddg_query, max_results=10))
            for r in raw:
                if len(items) >= 10:
                    break
                title = r.get("title", "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                pub = r.get("source", "")
                items.append({
                    "title":        title,
                    "publisher":    pub,
                    "url":          r.get("url", "#"),
                    "date":         r.get("date", "")[:10],
                    "thumbnail":    r.get("image", ""),
                    "source_colour": _source_colour(pub),
                })
        except Exception as e:
            logger.debug(f"DDG news fallback failed: {e}")

    logger.info(f"News fetched: {len(items)} items for '{company_name}'")
    return items[:10]


# ─── EDGAR merge helper ───────────────────────────────────────────────────────

def _merge_annual(yf_annual: dict, edgar_annual: dict) -> dict:
    """
    Merge Yahoo Finance annual data with SEC EDGAR annual data.
    EDGAR is authoritative for historical revenue/net_income (official SEC filings).
    YF fills in TTM margins and D/E ratio.
    """
    if not edgar_annual:
        return yf_annual
    if not yf_annual or not yf_annual.get("years"):
        return edgar_annual

    # Prefer whichever source has more years of data
    yf_years  = len(yf_annual.get("years", []))
    edg_years = len(edgar_annual.get("years", []))

    if edg_years >= yf_years:
        merged = dict(edgar_annual)
        # Supplement YF TTM values where EDGAR has None
        for key in ("gross_margin", "operating_margin", "d_e_ratio", "current_ratio"):
            yf_vals = yf_annual.get(key, [])
            edg_vals = merged.get(key, [])
            if yf_vals and all(v is None for v in edg_vals):
                merged[key] = yf_vals
        return merged
    else:
        return yf_annual


# ─── yfinance Fallback (HuggingFace / rate-limited environments) ──────────────

def _fmt_large(val) -> str:
    """Format raw number to human-readable string (4.90B, 142.3M, etc.)."""
    try:
        v = float(val)
        if abs(v) >= 1e12:
            return f"{v/1e12:.2f}T"
        if abs(v) >= 1e9:
            return f"{v/1e9:.2f}B"
        if abs(v) >= 1e6:
            return f"{v/1e6:.2f}M"
        return f"{v:,.0f}"
    except Exception:
        return "N/A"


def _pct_str(val) -> str:
    try:
        return f"{float(val) * 100:.1f}%"
    except Exception:
        return "N/A"


def _build_raw_data_from_yf(ticker: str) -> dict:
    """
    Fallback 1: use yfinance .info when curl_cffi quoteSummary is blocked.
    yfinance handles crumb/cookie auth differently and often works on HF.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        if not info or (info.get("trailingPE") is None and info.get("marketCap") is None):
            logger.warning(f"yfinance returned empty info for {ticker}")
            return {}

        logger.info(f"yfinance fallback succeeded for {ticker}: {len(info)} fields")

        def _v(key, default="N/A"):
            v = info.get(key)
            return v if v is not None and v != "" else default

        return {
            "ticker":                 ticker,
            "company_name":           _v("longName") or _v("shortName"),
            "sector":                 _v("sector"),
            "industry":               _v("industry"),
            "country":                _v("country"),
            "employees":              _v("fullTimeEmployees"),
            "exchange":               _v("exchange"),
            "market_cap":             _fmt_large(_v("marketCap", None)),
            "market_cap_raw":         _v("marketCap"),
            "enterprise_value":       _fmt_large(_v("enterpriseValue", None)),
            "revenue_ttm":            _fmt_large(_v("totalRevenue", None)),
            "gross_profit":           _fmt_large(_v("grossProfits", None)),
            "ebitda":                 _fmt_large(_v("ebitda", None)),
            "net_income":             _fmt_large(_v("netIncomeToCommon", None)),
            "operating_cash_flow":    _fmt_large(_v("operatingCashflow", None)),
            "cash":                   _fmt_large(_v("totalCash", None)),
            "debt":                   _fmt_large(_v("totalDebt", None)),
            "pe_ratio":               _v("trailingPE"),
            "forward_pe":             _v("forwardPE"),
            "ev_ebitda":              _v("enterpriseToEbitda"),
            "price_to_sales":         _v("priceToSalesTrailing12Months"),
            "eps_trailing":           _v("trailingEps"),
            "eps_forward":            _v("forwardEps"),
            "revenue_growth_yoy":     _pct_str(_v("revenueGrowth", None)),
            "earnings_growth_yoy":    _pct_str(_v("earningsGrowth", None)),
            "gross_margin":           _pct_str(_v("grossMargins", None)),
            "profit_margin":          _pct_str(_v("profitMargins", None)),
            "operating_margin":       _pct_str(_v("operatingMargins", None)),
            "roe":                    _pct_str(_v("returnOnEquity", None)),
            "roa":                    _pct_str(_v("returnOnAssets", None)),
            "d_e_ratio":              _v("debtToEquity"),
            "current_ratio":          _v("currentRatio"),
            "free_cashflow":          _fmt_large(_v("freeCashflow", None)),
            "current_price":          _v("currentPrice") or _v("regularMarketPrice"),
            "52w_high":               _v("fiftyTwoWeekHigh"),
            "52w_low":                _v("fiftyTwoWeekLow"),
            "analyst_target":         _v("targetMeanPrice"),
            "analyst_recommendation": _v("recommendationKey"),
            "beta":                   _v("beta"),
            "dividend_yield":         _pct_str(_v("dividendYield", None)),
            "peg_ratio":              _v("pegRatio"),
            "price_to_book":          _v("priceToBook"),
            "short_ratio":            _v("shortRatio"),
            "week52_change":          _pct_str(_v("52WeekChange", None)),
            "target_high":            _v("targetHighPrice"),
            "target_low":             _v("targetLowPrice"),
            "analyst_count":          _v("numberOfAnalystOpinions"),
            "payout_ratio":           _pct_str(_v("payoutRatio", None)),
            "shares_outstanding":     _fmt_large(_v("sharesOutstanding", None)),
            "fcf_raw":                _v("freeCashflow"),
        }
    except Exception as e:
        logger.warning(f"yfinance fallback failed for {ticker}: {e}")
        return {}


def _build_raw_data_from_v8_edgar(ticker: str, edgar_annual: dict) -> dict:
    """
    Fallback 2: v8 chart (no crumb) + SEC EDGAR annual data.
    Always works on HuggingFace — neither source requires Yahoo Finance auth.
    Covers: current price, 52w range, revenue, margins from official filings.
    """
    # v8 chart — works without crumb or cookies
    meta: dict = {}
    r = _yf_get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d",
        retries=2, delay=1.0,
    )
    if r:
        try:
            meta = r.json()["chart"]["result"][0]["meta"]
        except Exception:
            pass

    price    = meta.get("regularMarketPrice", "N/A")
    w52_high = meta.get("fiftyTwoWeekHigh", "N/A")
    w52_low  = meta.get("fiftyTwoWeekLow", "N/A")
    co_name  = meta.get("longName") or meta.get("shortName", "N/A")
    exchange = meta.get("fullExchangeName", meta.get("exchangeName", "N/A"))

    # Latest EDGAR annual values (most recent year)
    def _last(lst):
        if not lst:
            return None
        vals = [v for v in lst if v is not None]
        return vals[-1] if vals else None

    rev_b  = _last(edgar_annual.get("revenue", []))
    ni_b   = _last(edgar_annual.get("net_income", []))
    ocf_b  = _last(edgar_annual.get("operating_cf", []))
    gm     = _last(edgar_annual.get("gross_margin", []))
    om     = _last(edgar_annual.get("operating_margin", []))
    nm     = _last(edgar_annual.get("net_margin", []))
    cagr   = edgar_annual.get("revenue_cagr")

    def _b(val):
        return _fmt_large(val * 1e9) if val is not None else "N/A"

    def _pct_val(val):
        return f"{val:.1f}%" if val is not None else "N/A"

    logger.info(f"v8+EDGAR fallback for {ticker}: price={price}, rev={rev_b}B, gm={gm}%")

    return {
        "ticker":                 ticker,
        "company_name":           co_name,
        "sector":                 edgar_annual.get("edgar_name", "N/A"),
        "industry":               "N/A",
        "country":                "United States",
        "employees":              "N/A",
        "exchange":               exchange,
        "market_cap":             "N/A",
        "market_cap_raw":         "N/A",
        "enterprise_value":       "N/A",
        "revenue_ttm":            _b(rev_b),
        "gross_profit":           "N/A",
        "ebitda":                 "N/A",
        "net_income":             _b(ni_b),
        "operating_cash_flow":    _b(ocf_b),
        "cash":                   "N/A",
        "debt":                   "N/A",
        "pe_ratio":               "N/A",
        "forward_pe":             "N/A",
        "ev_ebitda":              "N/A",
        "price_to_sales":         "N/A",
        "eps_trailing":           "N/A",
        "eps_forward":            "N/A",
        "revenue_growth_yoy":     _pct_val(cagr) if cagr else "N/A",
        "earnings_growth_yoy":    "N/A",
        "gross_margin":           _pct_val(gm),
        "profit_margin":          _pct_val(nm),
        "operating_margin":       _pct_val(om),
        "roe":                    "N/A",
        "roa":                    "N/A",
        "d_e_ratio":              "N/A",
        "current_ratio":          "N/A",
        "free_cashflow":          "N/A",
        "current_price":          price,
        "52w_high":               w52_high,
        "52w_low":                w52_low,
        "analyst_target":         "N/A",
        "analyst_recommendation": "N/A",
        "beta":                   "N/A",
        "dividend_yield":         "N/A",
        "peg_ratio":              "N/A",
        "price_to_book":          "N/A",
        "short_ratio":            "N/A",
        "week52_change":          "N/A",
        "target_high":            "N/A",
        "target_low":             "N/A",
        "analyst_count":          "N/A",
        "payout_ratio":           "N/A",
        "shares_outstanding":     "N/A",
        "fcf_raw":                "N/A",
    }


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def get_financial_data(company_name: str) -> dict:
    """
    Fetch financial data for any company.

    Sources (run concurrently):
    1. Yahoo Finance  — market price, valuation multiples, TTM metrics, stock history
    2. SEC EDGAR      — official annual revenue/net income from 10-K filings (US companies)

    EDGAR works purely by company name — no ticker required.
    If YF ticker lookup fails but EDGAR finds the company, we still return rich data.
    """
    from agents.sec_edgar import get_edgar_data

    if not company_name:
        return {"is_public": False, "combined_text": "No company name provided.", "news_items": []}

    # ── Sequential: YF ticker lookup first, then EDGAR ─────────────────────
    # (Avoid nested ThreadPoolExecutor — this function is already called from
    #  graph.py's parallel executor, and nesting causes curl_cffi session issues.)
    ticker       = find_ticker(company_name)
    edgar_annual = get_edgar_data(company_name)

    # If YF couldn't find a ticker but EDGAR has one, use it
    if not ticker and edgar_annual.get("edgar_ticker"):
        candidate = edgar_annual["edgar_ticker"]
        if _validate_ticker(candidate):
            ticker = candidate
            logger.info(f"Ticker resolved via EDGAR: {ticker}")

    logger.info(f"Ticker='{ticker}' | EDGAR='{edgar_annual.get('edgar_name', 'not found')}'")

    # ── Case A: public company (have ticker) ───────────────────────────────
    if ticker:
        qs         = fetch_quote_summary(ticker)
        time.sleep(0.4)
        hist       = fetch_stock_history(ticker)
        time.sleep(0.4)
        quarterly  = fetch_quarterly_financials(ticker)
        time.sleep(0.3)
        yf_annual  = fetch_annual_financials(ticker)
        time.sleep(0.3)
        news_items = fetch_recent_news(company_name, ticker)

        if qs:
            raw_data = build_raw_data(ticker, qs)
        else:
            logger.warning(f"quoteSummary blocked for {ticker} — trying yfinance fallback")
            raw_data = _build_raw_data_from_yf(ticker)
            if not raw_data.get("market_cap") or raw_data.get("market_cap") == "N/A":
                logger.warning(f"yfinance also failed for {ticker} — using v8+EDGAR fallback")
                raw_data = _build_raw_data_from_v8_edgar(ticker, edgar_annual)
        formatted = format_public_data(raw_data) if raw_data.get("revenue_ttm", "N/A") != "N/A" else (
            f"Ticker: {ticker}. Metrics temporarily unavailable."
        )

        # Merge YF + EDGAR annual data — EDGAR wins on historical depth
        annual = _merge_annual(yf_annual, edgar_annual)

        # Competitor detection
        sector      = raw_data.get("sector", "")
        industry    = raw_data.get("industry", "")
        competitors = find_and_fetch_competitors(company_name, ticker, sector, industry)

        logger.info(
            f"Financial data: qs={bool(qs)}, hist={bool(hist)}, "
            f"quarterly={bool(quarterly)}, annual_years={len(annual.get('years', []))}, "
            f"edgar_years={len(edgar_annual.get('years', []))}, "
            f"news={len(news_items)}, competitors={len(competitors)}"
        )
        return {
            "is_public":     True,
            "ticker":        ticker,
            "raw_data":      raw_data,
            "stock_history": hist,
            "quarterly":     quarterly,
            "annual":        annual,
            "combined_text": formatted,
            "news_items":    news_items,
            "competitors":   competitors,
        }

    # ── Case B: EDGAR-only (US public, but YF ticker lookup failed) ────────
    if edgar_annual.get("years"):
        news_items = fetch_recent_news(company_name)
        edgar_name = edgar_annual.get("edgar_name", company_name)
        rev_latest = edgar_annual["revenue"][-1] if edgar_annual.get("revenue") else None
        ni_latest  = edgar_annual["net_income"][-1] if edgar_annual.get("net_income") else None
        yr_last    = edgar_annual["years"][-1]
        yr_first   = edgar_annual["years"][0]
        cagr       = edgar_annual.get("revenue_cagr", "N/A")
        parts = [f"**{edgar_name}** (SEC EDGAR — US public company)\n\n"]
        if rev_latest is not None:
            parts.append(f"**Annual Revenue ({yr_last}):** ${rev_latest:.2f}B\n")
        if ni_latest is not None:
            parts.append(f"**Net Income ({yr_last}):** ${ni_latest:.2f}B\n")
        parts.append(f"**Revenue CAGR ({yr_first}–{yr_last}):** {cagr}%\n")
        parts.append("\n*Note: Real-time price and valuation data unavailable — YF ticker not resolved.*")
        summary = "".join(parts)
        return {
            "is_public":     True,
            "ticker":        edgar_annual.get("edgar_ticker") or None,
            "raw_data":      {"ticker": edgar_annual.get("edgar_ticker", ""), "company_name": edgar_name},
            "stock_history": None,
            "quarterly":     {},
            "annual":        edgar_annual,
            "combined_text": summary,
            "news_items":    news_items,
            "competitors":   [],
        }

    # ── Case C: private / non-US, no financial data found ─────────────────
    news_items = fetch_recent_news(company_name)
    return {
        "is_public":     False,
        "ticker":        None,
        "raw_data":      {},
        "stock_history": None,
        "quarterly":     {},
        "annual":        {},
        "combined_text": search_private_financials(company_name),
        "news_items":    news_items,
        "competitors":   [],
    }
