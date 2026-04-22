"""
SEC EDGAR Agent
---------------
Fetches official financial data from the US Securities & Exchange Commission.
Works entirely by company name — no ticker symbol required.

Data flow:
  company name
    → EDGAR company_tickers.json  (in-memory cache)
    → CIK (Central Index Key)
    → XBRL companyfacts API
    → structured annual financials

All endpoints are public and free. EDGAR requires a User-Agent identifying the caller.
"""

import logging
from typing import Optional

from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)

# EDGAR requires a descriptive User-Agent per their API policy
_HEADERS = {"User-Agent": "ScoutAI company-research-tool contact@scoutai.app"}
_BASE = "https://data.sec.gov"

# ─── In-memory cache (populated once per process) ────────────────────────────

_TICKERS_CACHE: dict = {}       # raw EDGAR JSON: {idx: {cik_str, ticker, title}}
_NAME_INDEX: dict = {}          # normalised_name → {cik, ticker, title}


def _build_name_index(raw: dict):
    """Index companies by normalised name for fast fuzzy lookup."""
    _NAME_INDEX.clear()
    for item in raw.values():
        title = item.get("title", "")
        if title:
            _NAME_INDEX[_normalise(title)] = item


def _normalise(name: str) -> str:
    """Lowercase + strip common legal suffixes for better matching."""
    n = name.lower().strip()
    for sfx in (
        " inc.", " inc", " corp.", " corp", " corporation", " ltd.", " ltd",
        " llc", " l.l.c.", " plc", " co.", " co,", " group", " holdings",
        " holding", " technologies", " technology", " solutions", " services",
        " systems", " international", " global", " limited", " ventures",
    ):
        if n.endswith(sfx):
            n = n[: -len(sfx)].strip()
    return n


def _load_tickers() -> bool:
    """Download and cache SEC company tickers list (runs once per session)."""
    if _TICKERS_CACHE:
        return True
    try:
        s = cffi_requests.Session()
        r = s.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_HEADERS,
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            _TICKERS_CACHE.update(data)
            _build_name_index(data)
            logger.info(f"EDGAR: loaded {len(data):,} company records")
            return True
    except Exception as e:
        logger.warning(f"EDGAR tickers load failed: {e}")
    return False


# ─── Company Lookup ───────────────────────────────────────────────────────────

def _match_score(query_norm: str, candidate_norm: str) -> float:
    if query_norm == candidate_norm:
        return 1.0
    if candidate_norm.startswith(query_norm):
        return 0.95
    if query_norm in candidate_norm:
        return 0.90
    if candidate_norm in query_norm:
        return 0.80
    # Word-level overlap
    q_words = [w for w in query_norm.split() if len(w) > 2]
    if not q_words:
        return 0.0
    matched = sum(1 for w in q_words if w in candidate_norm)
    return matched / len(q_words) * 0.75


def search_company(company_name: str) -> Optional[dict]:
    """
    Find a company in SEC EDGAR by name.
    Returns {cik, ticker, title} or None.
    No ticker required — pure name-based search.
    """
    if not _load_tickers():
        return None

    q_norm = _normalise(company_name)

    # Fast exact/prefix lookup in name index
    best_item = None
    best_score = 0.0

    for name_norm, item in _NAME_INDEX.items():
        score = _match_score(q_norm, name_norm)
        if score > best_score:
            best_score = score
            best_item = item

    if best_item and best_score >= 0.5:
        raw_cik = best_item.get("cik_str", "")
        cik_str = str(raw_cik).lstrip("0") or str(raw_cik)
        result = {
            "cik":    cik_str,
            "ticker": best_item.get("ticker", ""),
            "title":  best_item.get("title", ""),
            "score":  best_score,
        }
        logger.info(
            f"EDGAR: '{company_name}' → {result['title']} "
            f"(CIK {result['cik']}, ticker {result['ticker']}, score {best_score:.2f})"
        )
        return result

    logger.info(f"EDGAR: no match for '{company_name}' (best score={best_score:.2f})")
    return None


# ─── XBRL Financial Data ─────────────────────────────────────────────────────

_REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "RevenueFromContractWithCustomerNetOfTax",
    "RevenuesNetOfInterestExpense",
    "NetRevenues",
]

_NET_INCOME_CONCEPTS = [
    "NetIncomeLoss",
    "NetIncomeLossAvailableToCommonStockholdersDiluted",
    "ProfitLoss",
    "NetIncomeLossAttributableToParent",
]

_GROSS_PROFIT_CONCEPTS = [
    "GrossProfit",
]

_OPERATING_INCOME_CONCEPTS = [
    "OperatingIncomeLoss",
]

_ASSETS_CONCEPTS = ["Assets"]
_LIABILITIES_CONCEPTS = ["Liabilities"]
_EQUITY_CONCEPTS = ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
_FCF_CONCEPTS = ["NetCashProvidedByUsedInOperatingActivities"]  # proxy for FCF

_EPS_CONCEPTS = ["EarningsPerShareDiluted", "EarningsPerShareBasic"]
_SHARES_CONCEPTS = [
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
]
_CASH_CONCEPTS = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsAndShortTermInvestments",
    "CashAndCashEquivalentsAndShortTermInvestments",
]
_CURRENT_ASSETS_CONCEPTS  = ["AssetsCurrent"]
_CURRENT_LIABILITIES_CONCEPTS = ["LiabilitiesCurrent"]
_DEBT_CONCEPTS = [
    "LongTermDebtNoncurrent",
    "LongTermDebt",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
]
_CAPEX_CONCEPTS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsForCapitalImprovements",
]

# SIC code → coarse sector (covers the most common public company categories)
_SIC_SECTOR: list = [
    (range(100,   1000), "Agriculture"),
    (range(1000,  1500), "Basic Materials"),
    (range(1500,  2000), "Industrials"),
    (range(2000,  4000), "Industrials"),
    (range(4000,  4900), "Industrials"),
    (range(4800,  4900), "Communication Services"),
    (range(4900,  5000), "Utilities"),
    (range(5000,  6000), "Consumer Cyclical"),
    (range(6000,  6800), "Financial Services"),
    (range(7370,  7380), "Technology"),
    (range(7000,  8000), "Consumer Cyclical"),
    (range(3670,  3680), "Technology"),
    (range(3674,  3675), "Technology"),
    (range(8000,  8100), "Healthcare"),
    (range(8100,  9000), "Consumer Defensive"),
]


def _sic_to_sector(sic: int) -> str:
    for r, sector in _SIC_SECTOR:
        if sic in r:
            return sector
    return "N/A"


def _latest_value(units_dict: dict, concept: str,
                  forms=("10-K", "10-Q", "10-K/A", "10-Q/A"),
                  min_days: int = 0) -> Optional[float]:
    """
    Return the most recently filed value for a concept (any unit type).
    min_days: if > 0, only accept entries with (end - start) >= min_days.
    """
    from datetime import datetime
    concept_data = units_dict.get(concept, {})
    for unit_key in ("USD", "USD/shares", "shares", "pure"):
        entries = concept_data.get("units", {}).get(unit_key, [])
        best_val, best_filed, best_end = None, "", ""
        for e in entries:
            if e.get("form") not in forms:
                continue
            if min_days > 0:
                try:
                    s = datetime.strptime(e["start"], "%Y-%m-%d")
                    d = datetime.strptime(e["end"],   "%Y-%m-%d")
                    if (d - s).days < min_days:
                        continue
                except Exception:
                    pass
            filed = e.get("filed", "")
            end   = e.get("end",   "")
            if (filed, end) > (best_filed, best_end):
                v = e.get("val")
                if v is not None:
                    best_val, best_filed, best_end = float(v), filed, end
        if best_val is not None:
            return best_val
    return None


def fetch_company_metadata(cik: str) -> dict:
    """
    Fetch company metadata from EDGAR submissions API.
    Returns sic, sicDescription, name, tickers, exchanges, employees.
    """
    padded = str(cik).zfill(10)
    try:
        s = cffi_requests.Session()
        r = s.get(
            f"{_BASE}/submissions/CIK{padded}.json",
            headers=_HEADERS,
            timeout=12,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"EDGAR submissions fetch failed (CIK {cik}): {e}")
    return {}


def _annual_values(gaap: dict, concept: str) -> list[tuple[str, float]]:
    """
    Extract annual (10-K) values for a GAAP concept.
    Returns [(year_str, value_in_USD), ...] sorted by year.
    """
    try:
        entries = gaap.get(concept, {}).get("units", {}).get("USD", [])
        annual: dict[str, dict] = {}
        for e in entries:
            if e.get("form") != "10-K":
                continue
            yr = e.get("end", "")[:4]
            val = e.get("val")
            filed = e.get("filed", "")
            if yr and val is not None:
                # Keep the most recently amended version
                if yr not in annual or filed > annual[yr]["filed"]:
                    annual[yr] = {"val": float(val), "filed": filed}
        return [(yr, d["val"]) for yr, d in sorted(annual.items())]
    except Exception:
        return []


def _pick_concept(gaap: dict, concepts: list[str]) -> list[tuple[str, float]]:
    """Try each concept in priority order, return first non-empty result."""
    for c in concepts:
        vals = _annual_values(gaap, c)
        if vals:
            return vals
    return []


def _to_billions(vals: list[tuple[str, float]]) -> dict[str, Optional[float]]:
    return {yr: round(v / 1e9, 4) for yr, v in vals}


def fetch_xbrl_facts(cik: str) -> dict:
    """Fetch XBRL companyfacts JSON from EDGAR."""
    padded = cik.zfill(10)
    try:
        s = cffi_requests.Session()
        r = s.get(
            f"{_BASE}/api/xbrl/companyfacts/CIK{padded}.json",
            headers=_HEADERS,
            timeout=20,
        )
        if r.status_code == 200:
            return r.json()
        logger.warning(f"EDGAR XBRL: HTTP {r.status_code} for CIK {cik}")
    except Exception as e:
        logger.warning(f"EDGAR XBRL fetch failed (CIK {cik}): {e}")
    return {}


def parse_financials(facts: dict, company_info: dict) -> dict:
    """
    Parse XBRL facts into a structured dict compatible with financial_analyst.py
    annual data format.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    dei  = facts.get("facts", {}).get("dei", {})
    if not gaap:
        return {}

    rev_raw    = _pick_concept(gaap, _REVENUE_CONCEPTS)
    ni_raw     = _pick_concept(gaap, _NET_INCOME_CONCEPTS)
    gp_raw     = _pick_concept(gaap, _GROSS_PROFIT_CONCEPTS)
    op_raw     = _pick_concept(gaap, _OPERATING_INCOME_CONCEPTS)
    ocf_raw    = _pick_concept(gaap, _FCF_CONCEPTS)
    assets_raw = _pick_concept(gaap, _ASSETS_CONCEPTS)

    rev_by_yr   = _to_billions(rev_raw)
    ni_by_yr    = _to_billions(ni_raw)
    gp_by_yr    = _to_billions(gp_raw)
    op_by_yr    = _to_billions(op_raw)
    ocf_by_yr   = _to_billions(ocf_raw)
    assets_by_yr = _to_billions(assets_raw)

    # Union of all years, take last 5
    all_years = sorted(
        set(rev_by_yr) | set(ni_by_yr) | set(gp_by_yr) | set(op_by_yr)
    )[-5:]

    if not all_years:
        return {}

    # Build margin arrays
    def _margin(num_yr: dict, den_yr: dict, yr: str) -> Optional[float]:
        n, d = num_yr.get(yr), den_yr.get(yr)
        if n is not None and d and d != 0:
            return round(n / d * 100, 1)
        return None

    years       = all_years
    revenue     = [rev_by_yr.get(yr) for yr in years]
    net_income  = [ni_by_yr.get(yr) for yr in years]
    ocf         = [ocf_by_yr.get(yr) for yr in years]
    net_margin  = [_margin(ni_by_yr, rev_by_yr, yr) for yr in years]
    gross_margin = [_margin(gp_by_yr, rev_by_yr, yr) for yr in years]
    op_margin   = [_margin(op_by_yr, rev_by_yr, yr) for yr in years]

    # Revenue CAGR
    rev_vals = [v for v in revenue if v]
    cagr = None
    if len(rev_vals) >= 2:
        try:
            n = len(rev_vals) - 1
            cagr = round(((rev_vals[-1] / rev_vals[0]) ** (1 / n) - 1) * 100, 1)
        except Exception:
            pass

    # Latest balance sheet metrics (most recent year available)
    latest_assets = None
    for yr in reversed(years):
        if assets_by_yr.get(yr) is not None:
            latest_assets = assets_by_yr[yr]
            break

    # ── Point-in-time fields (most recent filing, quarterly OK) ──────────────
    # Annual EPS only — require full-year period (≥340 days) to exclude stub entries
    eps_latest = (
        _latest_value(gaap, "EarningsPerShareDiluted", forms=("10-K", "10-K/A"), min_days=340)
        or _latest_value(gaap, "EarningsPerShareBasic", forms=("10-K", "10-K/A"), min_days=340)
    )
    shares_latest   = None
    for c in _SHARES_CONCEPTS:
        v = _latest_value(gaap, c)
        if v and v > 1e6:   # sanity: shares should be > 1M
            shares_latest = v
            break
    cash_latest     = None
    for c in _CASH_CONCEPTS:
        v = _latest_value(gaap, c)
        if v is not None:
            cash_latest = v
            break
    cur_assets  = _latest_value(gaap, "AssetsCurrent")
    cur_liab    = _latest_value(gaap, "LiabilitiesCurrent")
    debt_latest = None
    for c in _DEBT_CONCEPTS:
        v = _latest_value(gaap, c)
        if v is not None:
            debt_latest = v
            break
    equity_latest = None
    for c in _EQUITY_CONCEPTS:
        v = _latest_value(gaap, c)
        if v is not None:
            equity_latest = v
            break
    capex_latest = None
    for c in _CAPEX_CONCEPTS:
        v = _latest_value(gaap, c)
        if v is not None:
            capex_latest = abs(v)
            break
    ocf_latest = _latest_value(gaap, "NetCashProvidedByUsedInOperatingActivities")

    # Derived ratios
    current_ratio_val = None
    if cur_assets and cur_liab and cur_liab != 0:
        current_ratio_val = round(cur_assets / cur_liab, 2)

    de_ratio_val = None
    if debt_latest is not None and equity_latest and equity_latest > 0:
        de_ratio_val = round(debt_latest / equity_latest * 100, 1)

    fcf_latest = None
    if ocf_latest is not None and capex_latest is not None:
        fcf_latest = ocf_latest - capex_latest

    # Employees — DEI taxonomy first, then submissions API
    employees_latest = _latest_value(dei, "EntityNumberOfEmployees")

    # SIC → sector/industry from EDGAR submissions API (not in companyfacts)
    meta = fetch_company_metadata(company_info.get("cik", ""))
    sic_raw  = meta.get("sic")
    sic_desc = meta.get("sicDescription", "")
    sector_val   = _sic_to_sector(int(sic_raw)) if sic_raw else "N/A"
    industry_val = sic_desc if sic_desc else "N/A"
    # Submissions API also has employee count in some filings
    if employees_latest is None:
        try:
            for filing in (meta.get("filings", {}).get("recent", {}).get("items", []) or []):
                emp = filing.get("employees")
                if emp:
                    employees_latest = float(emp)
                    break
        except Exception:
            pass

    result = {
        "source":         "SEC EDGAR",
        "cik":            company_info.get("cik", ""),
        "edgar_ticker":   company_info.get("ticker", ""),
        "edgar_name":     company_info.get("title", ""),
        # Same keys as financial_analyst annual dict — drop-in compatible
        "years":          years,
        "revenue":        revenue,
        "net_income":     net_income,
        "fcf":            [None] * len(years),
        "operating_cf":   ocf,
        "gross_margin":   gross_margin,
        "operating_margin": op_margin,
        "net_margin":     net_margin,
        "d_e_ratio":      [None] * len(years),
        "current_ratio":  [None] * len(years),
        "revenue_cagr":   cagr,
        "latest_assets_b": latest_assets,
        # Point-in-time fields for raw_data population
        "edgar_eps":           round(eps_latest, 2) if eps_latest is not None else None,
        "edgar_shares":        shares_latest,
        "edgar_cash":          cash_latest,
        "edgar_debt":          debt_latest,
        "edgar_current_ratio": current_ratio_val,
        "edgar_de_ratio":      de_ratio_val,
        "edgar_fcf":           fcf_latest,
        "edgar_employees":     int(employees_latest) if employees_latest else None,
        "edgar_sector":        sector_val,
        "edgar_industry":      industry_val,
    }

    logger.info(
        f"EDGAR parsed: {company_info.get('title')} | "
        f"years={years} | rev={revenue} | cagr={cagr}% | "
        f"eps={eps_latest} shares={shares_latest} cash={cash_latest}"
    )
    return result


# ─── Main Entry ───────────────────────────────────────────────────────────────

def get_edgar_data(company_name: str) -> dict:
    """
    Full pipeline: name → CIK → XBRL facts → structured financials.
    Returns {} if the company is not found in SEC EDGAR (e.g. private / non-US).
    """
    company_info = search_company(company_name)
    if not company_info:
        return {}

    facts = fetch_xbrl_facts(company_info["cik"])
    if not facts:
        # Still return identity info even without financials
        return {
            "source":       "SEC EDGAR",
            "cik":          company_info["cik"],
            "edgar_ticker": company_info["ticker"],
            "edgar_name":   company_info["title"],
        }

    return parse_financials(facts, company_info)
