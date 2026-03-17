"""
ScoutAI — Smart Company Analyst Agent
================================================
Gradio UI entry point.
"""

import os
import re
import logging
import tempfile
from datetime import datetime
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates

import gradio as gr

from graph import run_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─── Chart Builders ───────────────────────────────────────────────────────────

# Violet-first palette — optimised for dark background
PURPLE = "#A78BFA"   # primary (revenue, stock line) — lighter violet for dark bg
VIOLET = "#C4B5FD"   # secondary accent
TEAL   = "#34D399"   # positive / income — bright green
CORAL  = "#F87171"   # negative / loss — bright red
AMBER  = "#FCD34D"   # third accent (margins) — bright amber
GRAY   = "#94A3B8"   # muted text
BG     = "#0F172A"   # dark navy background
GRID   = "#1E293B"   # dark grid lines

# Aliases kept so existing code that references BLUE/GREEN/RED still compiles
BLUE  = PURPLE
GREEN = TEAL
RED   = CORAL


def _empty_fig(msg: str):
    fig, ax = plt.subplots(figsize=(10, 3.5))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.text(0.5, 0.5, msg, ha="center", va="center",
            transform=ax.transAxes, fontsize=13, color=GRAY,
            style="italic")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([]); ax.set_yticks([])
    plt.tight_layout()
    return fig


def build_stock_chart(raw_financial: dict, company_name: str):
    hist = raw_financial.get("stock_history")
    ticker = raw_financial.get("ticker", "")
    if not hist or not hist.get("dates"):
        return _empty_fig("No stock price data\n(private company or data unavailable)")

    dates = [datetime.strptime(d, "%Y-%m-%d") for d in hist["dates"]]
    closes = hist["closes"]
    current = closes[-1]
    start = closes[0]
    change_pct = ((current - start) / start * 100) if start else 0
    # Violet for neutral/up; coral for down
    color = PURPLE if change_pct >= 0 else CORAL

    fig, ax = plt.subplots(figsize=(10, 3.8))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    ax.plot(dates, closes, color=color, linewidth=2.0, zorder=3)
    ax.fill_between(dates, closes, min(closes), alpha=0.07, color=color)

    # 52-week high / low annotations
    hi, lo = max(closes), min(closes)
    hi_idx, lo_idx = closes.index(hi), closes.index(lo)
    ax.annotate(f"52W High\n${hi:,.2f}", xy=(dates[hi_idx], hi),
                xytext=(0, 14), textcoords="offset points",
                ha="center", fontsize=8, color=TEAL,
                arrowprops=dict(arrowstyle="-", color=TEAL, lw=0.8))
    ax.annotate(f"52W Low\n${lo:,.2f}", xy=(dates[lo_idx], lo),
                xytext=(0, -22), textcoords="offset points",
                ha="center", fontsize=8, color=CORAL,
                arrowprops=dict(arrowstyle="-", color=CORAL, lw=0.8))

    sign = "+" if change_pct >= 0 else ""
    ax.set_title(
        f"{company_name}  ({ticker})   ${current:,.2f}   {sign}{change_pct:.1f}% YTD",
        fontsize=13, fontweight="bold", pad=12, loc="left", color="#F1F5F9"
    )
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.xticks(rotation=0, fontsize=9, color="#94A3B8")
    plt.yticks(fontsize=9, color="#94A3B8")
    ax.grid(True, alpha=0.25, linestyle=":", color="#334155")
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#334155")
    ax.spines["bottom"].set_color("#334155")
    plt.tight_layout(pad=1.2)
    return fig


def build_revenue_chart(raw_financial: dict, company_name: str):
    """Annual revenue + net income bars (5yr). Falls back to quarterly if no annual data."""
    annual = raw_financial.get("annual", {})

    # Prefer annual data
    if annual and annual.get("years") and any(v for v in annual.get("revenue", []) if v):
        years     = annual["years"]
        rev_vals  = [v if v is not None else 0 for v in annual["revenue"]]
        ni_vals   = [v if v is not None else 0 for v in annual.get("net_income", [])]
        cagr      = annual.get("revenue_cagr")
        use_annual = True
        label_x   = years
        title_sfx = "Annual Financials (USD Billions)"
    else:
        # Fallback: quarterly
        q   = raw_financial.get("quarterly", {})
        rev = q.get("revenue")
        ni  = q.get("net_income")
        if not rev or not rev.get("dates"):
            return _empty_fig("No financial data\n(private company or data unavailable)")

        pairs = [(d, v) for d, v in zip(rev["dates"], rev["values"]) if v is not None]
        pairs.sort(key=lambda x: x[0])
        label_x   = [p[0][:7] for p in pairs]
        rev_vals  = [p[1] for p in pairs]
        if ni and ni.get("dates"):
            ni_map = {d[:7]: v for d, v in zip(ni["dates"], ni["values"]) if v is not None}
            ni_vals = [ni_map.get(d, 0) or 0 for d in label_x]
        else:
            ni_vals = []
        cagr       = None
        use_annual = False
        title_sfx  = "Quarterly Financials (USD Billions)"

    x     = list(range(len(label_x)))
    width = 0.38
    max_rev = max((v for v in rev_vals if v), default=1)

    fig, ax = plt.subplots(figsize=(10, 5.2))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    bars_rev = ax.bar([i - width / 2 for i in x], rev_vals,
                      width=width, color=PURPLE, alpha=0.88, label="Revenue ($B)", zorder=3)
    if ni_vals and any(ni_vals):
        ni_colors = [TEAL if v >= 0 else CORAL for v in ni_vals]
        bars_ni = ax.bar([i + width / 2 for i in x], ni_vals,
                         width=width, color=ni_colors, alpha=0.80,
                         label="Net Income ($B)", zorder=3)
        # Net income value labels inside bars
        for bar, val in zip(bars_ni, ni_vals):
            if val and abs(val) > max_rev * 0.04:
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() / 2,
                        f"${val:.1f}B", ha="center", va="center",
                        fontsize=7.5, color="white", fontweight="600")

    # Revenue value labels + YoY growth on a single combined label above bar
    for idx, (bar, val) in enumerate(zip(bars_rev, rev_vals)):
        if not val:
            continue
        # YoY growth for label
        growth_str = ""
        if idx > 0 and rev_vals[idx - 1]:
            g = (val - rev_vals[idx - 1]) / abs(rev_vals[idx - 1]) * 100
            s = "+" if g >= 0 else ""
            growth_str = f"\n{s}{g:.0f}%"
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_rev * 0.015,
                f"${val:.1f}B{growth_str}",
                ha="center", va="bottom",
                fontsize=8.5, color=PURPLE, fontweight="700",
                multialignment="center",
                bbox=dict(boxstyle="round,pad=0.2", facecolor="#1E293B",
                          edgecolor="#334155", alpha=0.90))

    # Reserve head-room for labels + CAGR badge
    ax.set_ylim(0, max_rev * 1.45)

    # CAGR badge (annual only)
    if use_annual and cagr is not None:
        sign = "+" if cagr >= 0 else ""
        ax.text(0.99, 0.99,
                f"Revenue CAGR  {sign}{cagr:.1f}%",
                transform=ax.transAxes, fontsize=10, fontweight="bold",
                color=TEAL if cagr >= 0 else CORAL,
                va="top", ha="right",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#0D2818" if cagr >= 0 else "#2D0A0A",
                          edgecolor=TEAL if cagr >= 0 else CORAL, linewidth=1.2))

    ax.set_xticks(x)
    ax.set_xticklabels(label_x, fontsize=9.5, color="#CBD5E1")
    plt.yticks(fontsize=9, color="#94A3B8")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:.0f}B"))
    ax.set_title(f"{company_name} — {title_sfx}",
                 fontsize=13, fontweight="bold", pad=14, loc="left", color="#F1F5F9")
    ax.legend(fontsize=9, framealpha=0.3, edgecolor="#334155",
              facecolor="#1E293B", labelcolor="#E2E8F0", loc="upper left")
    ax.grid(True, alpha=0.25, linestyle=":", axis="y", color="#334155")
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#334155")
    ax.spines["bottom"].set_color("#334155")
    plt.tight_layout(pad=1.4)
    return fig


_PRIVATE_NOTICE = (
    "<div style='margin:8px 0 12px;padding:10px 16px;background:#2D1B00;"
    "border-left:4px solid #F97316;border-radius:6px;font-size:13px;color:#FED7AA'>"
    "🔒 <strong>Private / Pre-IPO company</strong> — public financial metrics are not available. "
    "Funding, valuation, and revenue estimates may appear in the report where discoverable."
    "</div>"
)


def build_metrics_html(raw_financial: dict) -> str:
    rd = raw_financial.get("raw_data", {})
    if not rd:
        return _PRIVATE_NOTICE

    def card(label, value, sub="", highlight=False):
        if highlight:
            bg = "#2D1B69"; border = "#7C3AED"; val_color = "#C4B5FD"
            label_color = "#A78BFA"
        else:
            bg = "#1E293B"; border = "#334155"; val_color = "#F1F5F9"
            label_color = "#94A3B8"
        sub_html = (
            f"<div style='font-size:11px;color:#64748B;margin-top:3px'>{sub}</div>"
            if sub else ""
        )
        return (
            f"<div style='background:{bg};border:1.5px solid {border};border-radius:12px;"
            f"padding:16px 18px;min-width:120px;flex:1;box-shadow:0 2px 8px rgba(0,0,0,0.3)'>"
            f"<div style='font-size:10px;color:{label_color};font-weight:700;letter-spacing:.7px;"
            f"text-transform:uppercase'>{label}</div>"
            f"<div style='font-size:20px;font-weight:800;color:{val_color};margin-top:5px;"
            f"letter-spacing:-0.3px'>{value}</div>"
            f"{sub_html}</div>"
        )

    def rec_badge(rec):
        colors = {
            "strong_buy": "#10B981", "buy": "#34D399", "hold": "#F59E0B",
            "underperform": "#F97316", "sell": "#EF4444",
        }
        bg    = colors.get(str(rec).lower().replace(" ", "_"), "#6B7280")
        label = str(rec).replace("_", " ").title()
        return (
            f"<span style='background:{bg};color:white;padding:3px 10px;"
            f"border-radius:20px;font-size:12px;font-weight:600'>{label}</span>"
        )

    rec         = rd.get("analyst_recommendation", "N/A")
    rec_display = rec_badge(rec) if rec != "N/A" else "N/A"
    emp         = rd.get("employees", "N/A")
    emp_str     = f"{int(emp):,}" if isinstance(emp, (int, float)) else str(emp)

    row1 = [
        card("Market Cap",      rd.get("market_cap", "N/A"),      highlight=True),
        card("Revenue (TTM)",   rd.get("revenue_ttm", "N/A")),
        card("Net Income",      rd.get("net_income", "N/A")),
        card("EBITDA",          rd.get("ebitda", "N/A")),
    ]
    row2 = [
        card("EPS (TTM)",       str(rd.get("eps_trailing", "N/A")),  "Earnings / share"),
        card("Enterprise Value",rd.get("enterprise_value", "N/A")),
        card("EV / EBITDA",     str(rd.get("ev_ebitda", "N/A")),     "Multiple"),
        card("Price / Sales",   str(rd.get("price_to_sales", "N/A")),"P/S TTM"),
    ]
    row3 = [
        card("Revenue Growth",  rd.get("revenue_growth_yoy", "N/A"), "YoY"),
        card("ROE",             rd.get("roe", "N/A"),                "Return on Equity"),
        card("Gross Margin",    rd.get("gross_margin", "N/A")),
        card("Operating Margin",rd.get("operating_margin", "N/A")),
    ]
    row4 = [
        card("P/E Ratio",       str(rd.get("pe_ratio", "N/A"))),
        card("Forward P/E",     str(rd.get("forward_pe", "N/A"))),
        card("52W Range",       f"${rd.get('52w_low','?')} – ${rd.get('52w_high','?')}"),
        card("Analyst Target",  f"${rd.get('analyst_target', 'N/A')}"),
    ]

    def row_html(cards):
        return f"<div style='display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px'>{''.join(cards)}</div>"

    return f"""
    <div style='padding:8px 0'>
      {row_html(row1)}
      {row_html(row2)}
      {row_html(row3)}
      {row_html(row4)}
      <div style='margin-top:12px;padding:12px 18px;background:#1E293B;
                  border:1.5px solid #334155;border-radius:10px;
                  font-size:13px;color:#CBD5E1;display:flex;flex-wrap:wrap;gap:20px;
                  align-items:center'>
        <span><strong style='color:#A78BFA'>Analyst:</strong>&nbsp;{rec_display}</span>
        <span><strong style='color:#E2E8F0'>Sector:</strong>&nbsp;{rd.get('sector','N/A')}</span>
        <span><strong style='color:#E2E8F0'>Industry:</strong>&nbsp;{rd.get('industry','N/A')}</span>
        <span><strong style='color:#E2E8F0'>Employees:</strong>&nbsp;{emp_str}</span>
        <span><strong style='color:#E2E8F0'>Beta:</strong>&nbsp;{rd.get('beta','N/A')}</span>
      </div>
    </div>
    """


# ─── FCF + Cash Flow Chart ────────────────────────────────────────────────────

ORANGE = AMBER  # backward-compat alias


def build_fcf_chart(raw_financial: dict, company_name: str):
    """
    Shows Cash Flow trend.
    Primary  : annual FCF + Op CF history (if available from API).
    Fallback : TTM snapshot bars from raw_data (FCF + Op CF single values).
    """
    annual  = raw_financial.get("annual", {})
    rd      = raw_financial.get("raw_data", {})
    years   = annual.get("years", [])
    op_cf   = annual.get("operating_cf", [])
    fcf     = annual.get("fcf", [])

    valid = [(yr, o, f) for yr, o, f in zip(years, op_cf, fcf)
             if o is not None or f is not None]

    # ── Fallback: TTM snapshot from raw_data ─────────────────────────────────
    if not valid:
        def _parse_billions(s) -> Optional[float]:
            if not s or s == "N/A":
                return None
            try:
                s = str(s).replace(",", "").strip()
                mul = 1e12 if s.endswith("T") else (1e9 if s.endswith("B") else
                      1e6 if s.endswith("M") else 1.0)
                return round(float(s.rstrip("TMBKk")) * mul / 1e9, 2)
            except Exception:
                return None

        fcf_ttm = _parse_billions(rd.get("free_cashflow"))
        cf_ttm  = _parse_billions(rd.get("operating_cash_flow"))

        if fcf_ttm is None and cf_ttm is None:
            return _empty_fig("No cash flow data available\n(private company or data unavailable)")

        fig, ax = plt.subplots(figsize=(10, 3.6))
        fig.patch.set_facecolor(BG); ax.set_facecolor(BG)

        labels = []
        vals   = []
        colors = []
        if cf_ttm is not None:
            labels.append("Operating CF"); vals.append(cf_ttm); colors.append(PURPLE)
        if fcf_ttm is not None:
            labels.append("Free Cash Flow"); vals.append(fcf_ttm)
            colors.append(TEAL if fcf_ttm >= 0 else CORAL)

        bars = ax.bar(labels, vals, color=colors, alpha=0.85, width=0.4, zorder=3)
        for bar, val in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + (max(vals)*0.02 if vals else 0.1),
                    f"${val:.1f}B", ha="center", va="bottom", fontsize=11,
                    fontweight="bold", color="#E2E8F0")
        ax.axhline(0, color="#475569", linewidth=1.0, linestyle="--")
        ax.set_title(f"{company_name} — Cash Flow Snapshot (TTM, $B)",
                     fontsize=13, fontweight="bold", pad=12, loc="left", color="#F1F5F9")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:.1f}B"))
        plt.yticks(fontsize=9, color="#94A3B8")
        ax.grid(True, alpha=0.25, linestyle=":", axis="y", color="#334155")
        for sp in ["top", "right"]:
            ax.spines[sp].set_visible(False)
        ax.spines["left"].set_color("#334155")
        ax.spines["bottom"].set_color("#334155")
        plt.tight_layout(pad=1.2)
        return fig

    # ── Primary: multi-year trend ────────────────────────────────────────────
    yrs      = [v[0] for v in valid]
    op_vals  = [v[1] if v[1] is not None else 0 for v in valid]
    fcf_vals = [v[2] if v[2] is not None else 0 for v in valid]
    x        = list(range(len(yrs)))

    fig, ax = plt.subplots(figsize=(10, 3.6))
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)

    ax.plot(x, op_vals, color=PURPLE, linewidth=2.2, marker="o", markersize=6,
            label="Operating Cash Flow ($B)", zorder=3)
    ax.plot(x, fcf_vals, color=TEAL, linewidth=2.2, marker="s", markersize=6,
            label="Free Cash Flow ($B)", zorder=3)
    ax.fill_between(x, fcf_vals, 0, where=[v >= 0 for v in fcf_vals],
                    color=TEAL, alpha=0.08, interpolate=True)
    ax.fill_between(x, fcf_vals, 0, where=[v < 0 for v in fcf_vals],
                    color=CORAL, alpha=0.10, interpolate=True)
    ax.axhline(0, color="#D1D5DB", linewidth=1.0, linestyle="--")
    for xi, val in zip(x, fcf_vals):
        ax.annotate(f"${val:.1f}B", (xi, val),
                    textcoords="offset points", xytext=(0, 10),
                    ha="center", fontsize=7.5, color=TEAL if val >= 0 else CORAL,
                    fontweight="bold")
    ax.set_title(f"{company_name} — Cash Flow Trends (Annual, $B)",
                 fontsize=13, fontweight="bold", pad=12, loc="left", color="#F1F5F9")
    ax.set_xticks(x); ax.set_xticklabels(yrs, fontsize=9, color="#CBD5E1")
    plt.yticks(fontsize=9, color="#94A3B8")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:.1f}B"))
    ax.legend(fontsize=9, framealpha=0.3, edgecolor="#334155",
              facecolor="#1E293B", labelcolor="#E2E8F0")
    ax.grid(True, alpha=0.25, linestyle=":", axis="y", color="#334155")
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#334155"); ax.spines["bottom"].set_color("#334155")
    plt.tight_layout(pad=1.2)
    return fig


def build_margin_chart(raw_financial: dict, company_name: str):
    """
    Shows margin trends.
    Primary  : annual historical margins (net margin derived from earnings data).
    Fallback : TTM snapshot bars from raw_data (gross / op / net margin).
    """
    annual = raw_financial.get("annual", {})
    rd     = raw_financial.get("raw_data", {})
    years  = annual.get("years", [])
    nm     = annual.get("net_margin", [])   # only reliable historical margin

    # Check if we have meaningful annual net margin data
    has_annual_nm = any(v is not None for v in nm)

    # ── Fallback: TTM snapshot bars ──────────────────────────────────────────
    if not has_annual_nm:
        def _pct_val(s) -> Optional[float]:
            if not s or s == "N/A":
                return None
            try:
                return float(str(s).replace("%", "").strip())
            except Exception:
                return None

        gm_v = _pct_val(rd.get("gross_margin"))
        om_v = _pct_val(rd.get("operating_margin"))
        nm_v = _pct_val(rd.get("profit_margin"))

        if gm_v is None and om_v is None and nm_v is None:
            return _empty_fig("No margin data available\n(private company or data unavailable)")

        labels, vals, colors = [], [], []
        if gm_v is not None:
            labels.append("Gross Margin"); vals.append(gm_v); colors.append(PURPLE)
        if om_v is not None:
            labels.append("Operating Margin"); vals.append(om_v); colors.append(AMBER)
        if nm_v is not None:
            labels.append("Net Margin"); vals.append(nm_v); colors.append(TEAL)

        fig, ax = plt.subplots(figsize=(10, 3.6))
        fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
        bars = ax.bar(labels, vals, color=colors, alpha=0.85, width=0.4, zorder=3)
        for bar, val in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                    f"{val:.1f}%", ha="center", va="bottom", fontsize=11,
                    fontweight="bold", color="#E2E8F0")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
        plt.yticks(fontsize=9, color="#94A3B8")
        ax.set_title(f"{company_name} — Margin Snapshot (TTM)",
                     fontsize=13, fontweight="bold", pad=12, loc="left", color="#F1F5F9")
        ax.grid(True, alpha=0.25, linestyle=":", axis="y", color="#334155")
        for sp in ["top", "right"]:
            ax.spines[sp].set_visible(False)
        ax.spines["left"].set_color("#334155"); ax.spines["bottom"].set_color("#334155")
        plt.tight_layout(pad=1.2)
        return fig

    # ── Primary: annual net margin trend + TTM annotations ──────────────────
    x = list(range(len(years)))

    def _clean(vals):
        return [v if v is not None else float("nan") for v in vals]

    fig, ax = plt.subplots(figsize=(10, 3.6))
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)

    ax.plot(x, _clean(nm), color=TEAL, linewidth=2.2, marker="^", markersize=6,
            label="Net Margin % (annual)", zorder=3)

    # Overlay TTM margin lines if available
    for key, color, label in [
        ("gross_margin", PURPLE, "Gross Margin % (TTM)"),
        ("operating_margin", AMBER, "Operating Margin % (TTM)"),
    ]:
        ttm_raw = rd.get(key, "")
        try:
            ttm_val = float(str(ttm_raw).replace("%", "").strip())
            ax.axhline(ttm_val, color=color, linewidth=1.5, linestyle="--",
                       alpha=0.6, label=label)
        except Exception:
            pass

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_xticks(x); ax.set_xticklabels(years, fontsize=9, color="#CBD5E1")
    plt.yticks(fontsize=9, color="#94A3B8")
    ax.set_title(f"{company_name} — Margin Trends",
                 fontsize=13, fontweight="bold", pad=12, loc="left", color="#F1F5F9")
    ax.legend(fontsize=9, framealpha=0.3, edgecolor="#334155",
              facecolor="#1E293B", labelcolor="#E2E8F0")
    ax.grid(True, alpha=0.25, linestyle=":", color="#334155")
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#334155"); ax.spines["bottom"].set_color("#334155")
    plt.tight_layout(pad=1.2)
    return fig


# ─── Balance Sheet Health HTML ────────────────────────────────────────────────

def build_health_html(raw_financial: dict) -> str:
    rd      = raw_financial.get("raw_data", {})
    is_pub  = raw_financial.get("is_public", False)

    if not is_pub:
        return (
            "<div style='padding:8px 0'>"
            "<h3 style='font-size:15px;font-weight:700;color:#F1F5F9;margin:0 0 10px 0;"
            "padding-bottom:8px;border-bottom:2px solid #334155'>🏦 Balance Sheet Health</h3>"
            "<div style='padding:14px 16px;background:#2D1B00;border-left:4px solid #F97316;"
            "border-radius:6px;font-size:13px;color:#FED7AA'>"
            "🔒 Balance sheet data unavailable — private company or non-US listing."
            "</div></div>"
        )

    # Use TTM values from raw_data (financialData module — most reliable)
    d_e_raw = rd.get("d_e_ratio")     # raw float from Yahoo Finance financialData
    cr_raw  = rd.get("current_ratio") # raw float from Yahoo Finance financialData

    def _to_float(v) -> Optional[float]:
        """Handle both plain floats and strings like '7.25%'."""
        try:
            return float(str(v).replace("%", "").strip())
        except (TypeError, ValueError):
            return None

    d_e = _to_float(d_e_raw)
    cr  = _to_float(cr_raw)

    def _health_card(label, value_str, good_cond, sub="", no_data_msg=""):
        if value_str is None:
            disp  = no_data_msg or "Not reported"
            color = "#64748B"; bg = "#1E293B"; border = "#334155"
        elif good_cond:
            disp  = value_str
            color = "#34D399"; bg = "#052E1A"; border = "#065F46"
        else:
            disp  = value_str
            color = "#F87171"; bg = "#2D0A0A"; border = "#7F1D1D"
        sub_html = (f"<div style='font-size:10px;color:{color};opacity:0.75;margin-top:2px'>{sub}</div>"
                    if sub else "")
        return (
            f"<div style='background:{bg};border:1px solid {border};border-radius:10px;"
            f"padding:14px 18px;min-width:120px;flex:1'>"
            f"<div style='font-size:10px;color:#64748B;font-weight:700;letter-spacing:.6px;"
            f"text-transform:uppercase'>{label}</div>"
            f"<div style='font-size:18px;font-weight:700;color:{color};margin-top:3px'>{disp}</div>"
            f"{sub_html}</div>"
        )

    def _plain_card(label, value, sub=""):
        disp = value if (value and value not in ("N/A", "")) else "Not reported"
        return (
            f"<div style='background:#1E293B;border:1px solid #334155;border-radius:10px;"
            f"padding:14px 18px;min-width:120px;flex:1'>"
            f"<div style='font-size:10px;color:#64748B;font-weight:700;letter-spacing:.6px;"
            f"text-transform:uppercase'>{label}</div>"
            f"<div style='font-size:18px;font-weight:700;color:#E2E8F0;margin-top:3px'>{disp}</div>"
            + (f"<div style='font-size:10px;color:#64748B;margin-top:2px'>{sub}</div>" if sub else "")
            + "</div>"
        )

    # D/E: Yahoo Finance fmt returns "7.25%" — _to_float strips %, giving 7.25
    # That value is in percent-of-equity form, divide by 100 for the actual ratio
    if d_e is not None:
        d_e_ratio = round(d_e / 100, 2)           # e.g. 7.25% → 0.07x
        d_e_str   = f"{d_e_ratio:.2f}x"
        d_e_good  = d_e_ratio < 1.5
    else:
        d_e_str  = None
        d_e_good = False

    cr_str   = f"{cr:.2f}x" if cr is not None else None
    cr_good  = cr is not None and cr > 1.5

    row = [
        _health_card("Debt / Equity (TTM)",  d_e_str, d_e_good, "< 1.5x is healthy",
                     "Not reported"),
        _health_card("Current Ratio (TTM)",  cr_str,  cr_good,  "> 1.5x is healthy",
                     "Not reported"),
        _plain_card("Free Cash Flow (TTM)",  rd.get("free_cashflow"),       "FCF"),
        _plain_card("Operating CF (TTM)",    rd.get("operating_cash_flow"), "Cash from operations"),
        _plain_card("Cash & Equivalents",    rd.get("cash"),                "Liquidity"),
    ]

    return (
        "<div style='padding:8px 0'>"
        "<h3 style='font-size:15px;font-weight:700;color:#F1F5F9;margin:0 0 10px 0;"
        "padding-bottom:8px;border-bottom:2px solid #334155'>🏦 Balance Sheet Health</h3>"
        f"<div style='display:flex;flex-wrap:wrap;gap:10px'>{''.join(row)}</div>"
        "</div>"
    )


# ─── Trader Scorecard ─────────────────────────────────────────────────────────

def build_trader_scorecard(raw_financial: dict) -> str:
    rd     = raw_financial.get("raw_data", {})
    is_pub = raw_financial.get("is_public", False)

    if not is_pub or not rd:
        return (
            "<div style='padding:8px 0'>"
            "<h3 style='font-size:15px;font-weight:700;color:#F1F5F9;margin:0 0 10px 0;"
            "padding-bottom:8px;border-bottom:2px solid #334155'>📊 Trader Scorecard</h3>"
            "<div style='padding:14px 16px;background:#1A1200;border-left:4px solid #F97316;"
            "border-radius:6px;font-size:13px;color:#FED7AA'>"
            "🔒 Trader scorecard requires public market data (listed companies only). "
            "See the AI report below for qualitative insights."
            "</div></div>"
        )

    def _float(val):
        if val is None:
            return None
        try:
            s = str(val).replace(",", "").replace("$", "").replace("%", "").strip()
            return float(s) if s and s != "N/A" else None
        except Exception:
            return None

    pe        = _float(rd.get("pe_ratio"))
    peg       = _float(rd.get("peg_ratio"))
    pb        = _float(rd.get("price_to_book"))
    ev_ebitda = _float(rd.get("ev_ebitda"))
    short_r   = _float(rd.get("short_ratio"))
    w52_chg   = _float(rd.get("week52_change"))
    tgt_hi    = _float(rd.get("target_high"))
    tgt_lo    = _float(rd.get("target_low"))
    tgt_mean  = _float(rd.get("analyst_target"))
    price     = _float(rd.get("current_price"))
    anl_count = _float(rd.get("analyst_count"))
    payout    = _float(rd.get("payout_ratio"))
    div_yield = rd.get("dividend_yield", "N/A")
    beta      = _float(rd.get("beta"))
    rec_key   = str(rd.get("analyst_recommendation", "")).lower()
    eps_ttm   = rd.get("eps_ttm", "N/A")
    eps_fwd   = rd.get("eps_forward", "N/A")

    upside_pct = ((tgt_mean - price) / price * 100) if price and tgt_mean and price > 0 else None

    # ── Valuation signal ─────────────────────────────────────────────────
    val_score = 0
    if pe  is not None: val_score += 2 if pe < 15 else (1 if pe < 25 else (-1 if pe > 40 else 0))
    if peg is not None: val_score += 2 if peg < 1 else (1 if peg < 2 else (-1 if peg > 3 else 0))
    if val_score >= 3:   val_label, val_color, val_bg = "Undervalued",   "#4ADE80", "#052E16"
    elif val_score >= 1: val_label, val_color, val_bg = "Fairly Valued", "#FCD34D", "#1C1917"
    else:                val_label, val_color, val_bg = "Expensive",     "#F87171", "#2D0A0A"

    # ── Momentum signal ──────────────────────────────────────────────────
    if w52_chg is not None:
        if w52_chg > 20:    mom_label, mom_color, mom_bg = "Strong Bull", "#4ADE80", "#052E16"
        elif w52_chg > 5:   mom_label, mom_color, mom_bg = "Bullish",     "#86EFAC", "#064E3B"
        elif w52_chg < -20: mom_label, mom_color, mom_bg = "Strong Bear", "#F87171", "#2D0A0A"
        elif w52_chg < -5:  mom_label, mom_color, mom_bg = "Bearish",     "#FCA5A5", "#450A0A"
        else:               mom_label, mom_color, mom_bg = "Neutral",     "#FCD34D", "#1C1917"
    else:
        mom_label, mom_color, mom_bg = "N/A", "#94A3B8", "#1E293B"

    # ── Analyst verdict ──────────────────────────────────────────────────
    rec_map = {
        "strong_buy":   ("Strong Buy",    "#4ADE80", "#052E16"),
        "buy":          ("Buy",           "#86EFAC", "#064E3B"),
        "hold":         ("Hold",          "#FCD34D", "#1C1917"),
        "underperform": ("Underperform",  "#FCA5A5", "#450A0A"),
        "sell":         ("Sell",          "#F87171", "#2D0A0A"),
        "strong_sell":  ("Strong Sell",   "#EF4444", "#450A0A"),
    }
    rec_label, rec_color, rec_bg = rec_map.get(rec_key, ("N/A", "#94A3B8", "#1E293B"))

    # ── Overall signal ───────────────────────────────────────────────────
    sig = 0
    if val_score > 0: sig += 1
    if w52_chg and w52_chg > 0: sig += 1
    if rec_key in ("strong_buy", "buy"):   sig += 2
    if rec_key in ("sell", "strong_sell"): sig -= 2
    if upside_pct and upside_pct > 15: sig += 1
    if short_r  and short_r > 5:       sig -= 1
    if peg and peg > 3:                sig -= 1

    if sig >= 3:   sig_label, sig_color, sig_bg = "🟢 BULLISH", "#4ADE80", "#052E16"
    elif sig >= 1: sig_label, sig_color, sig_bg = "🟡 NEUTRAL", "#FCD34D", "#1C1917"
    else:          sig_label, sig_color, sig_bg = "🔴 BEARISH", "#F87171", "#2D0A0A"

    # ── Signal chips ─────────────────────────────────────────────────────
    def _chip(label, value, color, bg):
        return (
            f"<div style='background:{bg};border:1.5px solid {color}40;border-radius:10px;"
            f"padding:14px 16px;min-width:130px;flex:1;text-align:center'>"
            f"<div style='font-size:10px;font-weight:700;color:#64748B;letter-spacing:.8px;"
            f"text-transform:uppercase;margin-bottom:6px'>{label}</div>"
            f"<div style='font-size:15px;font-weight:800;color:{color}'>{value}</div>"
            f"</div>"
        )

    chips_html = (
        _chip("Overall Signal",   sig_label,  sig_color,  sig_bg)  +
        _chip("Valuation",        val_label,  val_color,  val_bg)  +
        _chip("52W Momentum",     mom_label,  mom_color,  mom_bg)  +
        _chip("Analyst Verdict",  rec_label,  rec_color,  rec_bg)
    )

    # ── Key trade metric cards ────────────────────────────────────────────
    def _fmt(val, spec="{:.2f}", suffix=""):
        if val is None: return "N/A"
        try: return spec.format(val) + suffix
        except Exception: return str(val)

    def _trade_card(label, value, note="", color="#94A3B8"):
        return (
            f"<div style='background:#1E293B;border:1.5px solid #334155;border-radius:10px;"
            f"padding:12px 14px;min-width:130px;flex:1'>"
            f"<div style='font-size:10px;font-weight:700;color:#64748B;letter-spacing:.7px;"
            f"text-transform:uppercase;margin-bottom:5px'>{label}</div>"
            f"<div style='font-size:18px;font-weight:800;color:{color};margin-bottom:3px'>{value}</div>"
            f"<div style='font-size:10.5px;color:#475569'>{note}</div>"
            f"</div>"
        )

    peg_c    = ("#4ADE80" if peg and peg < 1 else "#FCD34D" if peg and peg < 2 else "#F87171") if peg else "#94A3B8"
    short_c  = ("#F87171" if short_r and short_r > 5 else "#FCD34D" if short_r and short_r > 2 else "#4ADE80") if short_r else "#94A3B8"
    mom_c    = ("#4ADE80" if w52_chg and w52_chg > 0 else "#F87171") if w52_chg is not None else "#94A3B8"
    up_c     = ("#4ADE80" if upside_pct and upside_pct > 15 else "#F87171" if upside_pct and upside_pct < -10 else "#FCD34D") if upside_pct is not None else "#94A3B8"
    pb_c     = ("#4ADE80" if pb and pb < 3 else "#FCD34D" if pb and pb < 6 else "#F87171") if pb else "#94A3B8"
    beta_c   = ("#4ADE80" if beta and 0.5 <= beta <= 1.2 else "#FCD34D" if beta and beta <= 2 else "#F87171") if beta else "#94A3B8"
    eveb_c   = ("#4ADE80" if ev_ebitda and ev_ebitda < 15 else "#FCD34D" if ev_ebitda and ev_ebitda < 25 else "#F87171") if ev_ebitda else "#94A3B8"

    tgt_range  = f"${tgt_lo:.0f}–${tgt_hi:.0f}" if tgt_lo and tgt_hi else "N/A"
    upside_str = _fmt(upside_pct, "{:+.1f}", "%") if upside_pct is not None else "N/A"

    row1 = (
        _trade_card("PEG Ratio",        _fmt(peg),             "< 1 = growth underpriced",     peg_c)  +
        _trade_card("Short Ratio",       _fmt(short_r, "{:.1f}"), "> 5 = heavy short pressure", short_c)+
        _trade_card("52W Return",        _fmt(w52_chg, "{:+.1f}", "%"), "vs 1 year ago",        mom_c)  +
        _trade_card("Upside to Target",  upside_str,            tgt_range,                      up_c)
    )
    row2 = (
        _trade_card("Price / Book",      _fmt(pb, "{:.1f}"),    "< 3 = value territory",        pb_c)   +
        _trade_card("EV / EBITDA",       _fmt(ev_ebitda, "{:.1f}"), "< 15 = cheap",             eveb_c) +
        _trade_card("Beta",              _fmt(beta),            "market sensitivity",            beta_c) +
        _trade_card("Analysts",          _fmt(anl_count, "{:.0f}"), rec_label,                  rec_color)
    )
    row3 = (
        _trade_card("EPS (TTM)",         str(eps_ttm),          "trailing 12m earnings/share",  "#94A3B8") +
        _trade_card("EPS (Forward)",     str(eps_fwd),          "next 12m estimate",            "#94A3B8") +
        _trade_card("Dividend Yield",    str(div_yield),        "annual % yield",               "#94A3B8") +
        _trade_card("Payout Ratio",      _fmt(payout, "{:.0f}", "%"), "% earnings paid as div",  "#94A3B8")
    )

    # ── Bullish / Risk flags ──────────────────────────────────────────────
    greens, risks = [], []
    if peg and peg < 1:           greens.append(f"PEG {peg:.2f} — growing faster than it costs")
    if upside_pct and upside_pct > 20: greens.append(f"{upside_pct:.1f}% upside to analyst consensus target")
    if rec_key in ("strong_buy", "buy"):
        greens.append(f"Analyst consensus: {rec_label} ({int(anl_count) if anl_count else '?'} analysts)")
    if short_r and short_r < 2:   greens.append(f"Low short interest ({short_r:.1f} days to cover)")
    if w52_chg and w52_chg > 30:  greens.append(f"Strong 12-month momentum (+{w52_chg:.1f}%)")

    if short_r and short_r > 5:   risks.append(f"High short interest ({short_r:.1f} days to cover) — bearish pressure")
    if peg and peg > 3:           risks.append(f"Stretched valuation — PEG {peg:.2f} implies growth disappointment risk")
    if beta and beta > 2:         risks.append(f"High volatility — Beta {beta:.2f}, expect wide price swings")
    if upside_pct and upside_pct < -15: risks.append(f"Trading {abs(upside_pct):.1f}% above analyst mean target")
    if w52_chg and w52_chg < -30: risks.append(f"Significant drawdown — down {abs(w52_chg):.0f}% over past year")
    if payout and payout > 90:    risks.append(f"Payout ratio {payout:.0f}% — dividend sustainability risk")

    notes_html = ""
    if greens or risks:
        g_html = r_html = ""
        if greens:
            items = "".join(f"<li style='margin:3px 0;font-size:12.5px;color:#A7F3D0'>✅ {g}</li>" for g in greens)
            g_html = (
                f"<div style='flex:1;min-width:200px;background:#052E16;border:1px solid #14532D;"
                f"border-radius:8px;padding:12px 14px'>"
                f"<div style='font-size:11px;font-weight:700;color:#4ADE80;text-transform:uppercase;"
                f"letter-spacing:.6px;margin-bottom:8px'>Bullish Signals</div>"
                f"<ul style='margin:0;padding-left:16px'>{items}</ul></div>"
            )
        if risks:
            items = "".join(f"<li style='margin:3px 0;font-size:12.5px;color:#FCA5A5'>⚠️ {r}</li>" for r in risks)
            r_html = (
                f"<div style='flex:1;min-width:200px;background:#2D0A0A;border:1px solid #450A0A;"
                f"border-radius:8px;padding:12px 14px'>"
                f"<div style='font-size:11px;font-weight:700;color:#F87171;text-transform:uppercase;"
                f"letter-spacing:.6px;margin-bottom:8px'>Risk Flags</div>"
                f"<ul style='margin:0;padding-left:16px'>{items}</ul></div>"
            )
        notes_html = f"<div style='display:flex;gap:14px;flex-wrap:wrap;margin-top:14px'>{g_html}{r_html}</div>"

    return (
        "<div style='padding:8px 0'>"
        "<h3 style='font-size:15px;font-weight:700;color:#F1F5F9;margin:0 0 12px 0;"
        "padding-bottom:8px;border-bottom:2px solid #334155'>📊 Trader Scorecard</h3>"
        f"<div style='display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px'>{chips_html}</div>"
        "<div style='font-size:10.5px;font-weight:700;color:#64748B;text-transform:uppercase;"
        "letter-spacing:.7px;margin:0 0 8px'>Key Trade Metrics</div>"
        f"<div style='display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px'>{row1}</div>"
        f"<div style='display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px'>{row2}</div>"
        f"<div style='display:flex;flex-wrap:wrap;gap:10px'>{row3}</div>"
        f"{notes_html}"
        "<div style='font-size:10px;color:#334155;margin-top:12px;text-align:right'>"
        "Not financial advice · For informational purposes only</div>"
        "</div>"
    )


# ─── Competitor Comparison Table ──────────────────────────────────────────────

def build_competitor_table_html(raw_financial: dict, company_name: str) -> str:
    rd          = raw_financial.get("raw_data", {})
    competitors = raw_financial.get("competitors", [])
    is_pub      = raw_financial.get("is_public", False)

    if not is_pub or not rd:
        return (
            "<div style='padding:8px 0'>"
            "<h3 style='font-size:15px;font-weight:700;color:#F1F5F9;margin:0 0 10px 0;"
            "padding-bottom:8px;border-bottom:2px solid #334155'>⚔️ Competitor Comparison</h3>"
            "<div style='padding:14px 16px;background:#2D1B00;border-left:4px solid #F97316;"
            "border-radius:6px;font-size:13px;color:#FED7AA'>"
            "🔒 Competitor comparison is only available for publicly traded companies. "
            "Qualitative competitive analysis is available in the <em>Full AI Report</em> section below."
            "</div></div>"
        )

    ticker = rd.get("ticker", "")

    def _row(name, tk, mktcap, rev, gm, nm, pe, roe, rev_growth, highlight=False):
        if highlight:
            bg   = "#2D1B69"
            fw   = "700"
            bord = "border-left:3px solid #A78BFA;"
        else:
            bg   = "#1E293B"
            fw   = "500"
            bord = "border-left:3px solid transparent;"
        base = (f"padding:9px 12px;border-bottom:1px solid #334155;"
                f"color:#E2E8F0;font-size:13px;background:{bg};font-weight:{fw};")
        cells = [name, tk, mktcap, rev, gm, nm, pe, roe, rev_growth]
        tds_parts = []
        for i, c in enumerate(cells):
            cell_bord = bord if i == 0 else ""
            tds_parts.append(f"<td style='{base}{cell_bord}'>{c}</td>")
        return "<tr>" + "".join(tds_parts) + "</tr>"

    head_cols = ["Company", "Ticker", "Market Cap", "Revenue", "Gross Margin",
                 "Net Margin", "P/E", "ROE", "Rev Growth"]
    header = "".join(
        f"<th style='padding:9px 12px;background:#0F172A;font-size:10.5px;font-weight:700;"
        f"color:#94A3B8;text-transform:uppercase;letter-spacing:.6px;"
        f"border-bottom:2px solid #4C1D95;text-align:left;white-space:nowrap'>{h}</th>"
        for h in head_cols
    )

    rows = [_row(
        rd.get("company_name", company_name), ticker,
        rd.get("market_cap", "N/A"), rd.get("revenue_ttm", "N/A"),
        rd.get("gross_margin", "N/A"), rd.get("profit_margin", "N/A"),
        str(rd.get("pe_ratio", "N/A")), rd.get("roe", "N/A"),
        rd.get("revenue_growth_yoy", "N/A"),
        highlight=True,
    )]

    for c in competitors:
        rows.append(_row(
            c.get("name", "N/A"), c.get("ticker", ""),
            c.get("market_cap", "N/A"), c.get("revenue", "N/A"),
            c.get("gross_margin", "N/A"), c.get("net_margin", "N/A"),
            c.get("pe_ratio", "N/A"), c.get("roe", "N/A"),
            c.get("revenue_growth", "N/A"),
        ))

    if not competitors:
        rows.append(
            "<tr><td colspan='9' style='padding:14px 12px;text-align:center;color:#64748B;"
            "font-size:12px;font-style:italic;background:#1E293B'>"
            "Yahoo Finance peer data not available for this ticker — "
            "see the <strong>Full AI Report</strong> section below for qualitative analysis."
            "</td></tr>"
        )

    return (
        "<div style='padding:8px 0;overflow-x:auto'>"
        "<h3 style='font-size:15px;font-weight:700;color:#F1F5F9;margin:0 0 10px 0;"
        "padding-bottom:8px;border-bottom:2px solid #334155'>⚔️ Competitor Comparison</h3>"
        f"<table style='width:100%;border-collapse:collapse;font-size:13px'>"
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )


# ─── News Card Builder ────────────────────────────────────────────────────────

def build_news_html(news_items: list) -> str:
    if not news_items:
        return ""

    cards = []
    for item in news_items:
        thumb = item.get("thumbnail", "")
        thumb_html = (
            f"<img src='{thumb}' style='width:80px;height:60px;object-fit:cover;"
            f"border-radius:6px;flex-shrink:0;background:#E5E7EB' "
            f"onerror=\"this.style.display='none'\">"
            if thumb else ""
        )
        badge_color = item.get("source_colour", "#6B7280")
        publisher   = item.get("publisher", "")
        date        = item.get("date", "")
        title       = item.get("title", "No title")
        url         = item.get("url", "#")

        badge = (
            f"<span style='background:{badge_color};color:#fff;padding:2px 9px;"
            f"border-radius:20px;font-size:10px;font-weight:700;letter-spacing:.3px'>"
            f"{publisher}</span>"
            if publisher else ""
        )
        date_str = (
            f"<span style='color:#9CA3AF;font-size:11px;margin-left:8px'>{date}</span>"
            if date else ""
        )
        cards.append(f"""
        <a href='{url}' target='_blank' rel='noopener noreferrer'
           style='text-decoration:none;color:inherit;display:block'>
          <div style='display:flex;gap:14px;padding:14px 16px;
                      background:#1E293B;border:1.5px solid #334155;border-radius:12px;
                      margin-bottom:10px;transition:border-color 0.15s,box-shadow 0.15s;
                      box-shadow:0 2px 8px rgba(0,0,0,0.3)'>
            {thumb_html}
            <div style='flex:1;min-width:0'>
              <div style='font-size:13.5px;font-weight:600;color:#E2E8F0;line-height:1.5;
                          margin-bottom:8px;overflow:hidden;display:-webkit-box;
                          -webkit-line-clamp:2;-webkit-box-orient:vertical'>{title}</div>
              <div style='display:flex;align-items:center;flex-wrap:wrap;gap:6px'>
                {badge}{date_str}
              </div>
            </div>
          </div>
        </a>""")

    return f"""
    <div style='padding:8px 0'>
      <h3 style='font-size:15px;font-weight:700;color:#F1F5F9;margin:0 0 12px 0;
                  padding-bottom:8px;border-bottom:2px solid #334155'>
        📰 Recent News &amp; Deals
      </h3>
      {''.join(cards)}
    </div>
    """


# ─── Report Helpers ───────────────────────────────────────────────────────────

def _extract_section(report: str, header: str) -> str:
    pattern = rf"## {re.escape(header)}.*?(?=\n## |\Z)"
    match = re.search(pattern, report, re.DOTALL | re.IGNORECASE)
    return match.group(0).strip() if match else f"## {header}\n_Not available._"


def _save_report(report: str, company_name: str) -> str:
    safe = re.sub(r"[^\w\s-]", "", company_name).strip().replace(" ", "_")
    path = os.path.join(tempfile.gettempdir(),
                        f"ScoutAI_{safe}_{datetime.now().strftime('%Y%m%d')}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    return path


def _validate_url(url: str):
    url = url.strip()
    if not url:
        return "Please enter a website URL."
    if not re.match(r"^(https?://)?([\w\-]+\.)+[\w]{2,}", url):
        return "Please enter a valid website URL (e.g. https://stripe.com)."
    return None


# ─── Main Pipeline Function ───────────────────────────────────────────────────

def analyze_company(url: str, groq_api_key: str, progress=gr.Progress(track_tqdm=True)):
    """
    Outputs (13 total, matching _outputs list):
      0  company_header_md    1  status_md
      2  stock_plot           3  revenue_plot (annual)
      4  metrics_html_out     5  trends_plot (FCF)
      6  margin_plot          7  health_html_out
      8  comp_html_out        9  news_html_out
      10 trader_scorecard_html  11 full_report_md
      12 download_btn
    """
    EMPTY = ("", "", None, None, "", None, None, "", "", "", "", "", None)
    #         0    1   2     3    4   5     6    7   8   9  10  11  12

    url_err = _validate_url(url)
    if url_err:
        yield (f"**Error:** {url_err}",) + EMPTY[1:]
        return

    key = groq_api_key.strip() or os.getenv("GROQ_API_KEY", "")
    if not key:
        yield ("**Error:** Please enter your Groq API key — free at [console.groq.com](https://console.groq.com)",) + EMPTY[1:]
        return

    # --- Loading state --- (must match _outputs length = 13)
    yield (
        "",                                                                    # 0 header
        "⏳ Agents running — scraping website, searching web, pulling financials...",  # 1 status
        None, None,                                                            # 2-3 plots
        "", None, None, "", "", "",                                            # 4-9 HTML/plots
        "", "*Analyzing — full report loading...*",                            # 10-11
        None,                                                                  # 12 download
    )
    progress(0.05, desc="Starting agents...")

    try:
        state = run_pipeline(url=url, groq_api_key=key)
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        yield (f"**Pipeline Error:** {e}",) + EMPTY[1:]
        return

    progress(0.85, desc="Building charts...")

    report       = state.get("final_report", "")
    company_name = state.get("company_name", "Company")
    pages        = state.get("pages_scraped", 0)
    raw_fin      = state.get("raw_financial", {})
    ticker       = raw_fin.get("ticker") or ""
    is_public    = raw_fin.get("is_public", False)
    errors       = state.get("errors", [])
    news_items   = state.get("news_items", [])

    # Build charts + HTML widgets
    stock_fig        = build_stock_chart(raw_fin, company_name)
    revenue_fig      = build_revenue_chart(raw_fin, company_name)
    fcf_fig          = build_fcf_chart(raw_fin, company_name)
    margin_fig       = build_margin_chart(raw_fin, company_name)
    metrics_html     = build_metrics_html(raw_fin)
    health_html      = build_health_html(raw_fin)
    comp_html        = build_competitor_table_html(raw_fin, company_name)
    news_html        = build_news_html(news_items)
    trader_scorecard = build_trader_scorecard(raw_fin)

    # Status line — show data source clearly
    annual_source = raw_fin.get("annual", {}).get("source", "")
    if is_public and ticker:
        fin_status = f"📈 {ticker} · Yahoo Finance"
        if annual_source == "SEC EDGAR":
            fin_status += " + SEC EDGAR"
    elif is_public and annual_source == "SEC EDGAR":
        fin_status = "📋 SEC EDGAR (historical filings)"
    else:
        fin_status = "🔒 private company"
    err_note = f" · ⚠️ {len(errors)} warning(s)" if errors else ""
    status = f"✅ Analyzed **{pages} pages** · {fin_status}{err_note}"

    # Company header
    company_header = f"# {company_name} — Full AI Report"

    # Save download
    download_path = _save_report(report, company_name) if report else None

    progress(1.0, desc="Done!")

    yield (
        company_header,   # 0
        status,           # 1
        stock_fig,        # 2
        revenue_fig,      # 3
        metrics_html,     # 4
        fcf_fig,          # 5
        margin_fig,       # 6
        health_html,      # 7
        comp_html,        # 8
        news_html,        # 9
        trader_scorecard, # 10
        report,           # 11  ← full LLM report
        download_path,    # 12
    )


# ─── CSS ─────────────────────────────────────────────────────────────────────

CSS = """
/* ── Base ─────────────────────────────────────────────────────────────────── */
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
       background: #0F172A !important; color: #E2E8F0 !important; }
.gradio-container { max-width: 1200px !important; margin: auto !important;
                    padding: 0 24px !important; background: #0F172A !important; }

/* ── Title ───────────────────────────────────────────────────────────────── */
#title-html { padding: 32px 0 24px; border-bottom: 1px solid #1E293B; margin-bottom: 20px; }
#title-html h1 { text-align:center; font-size:2.4rem; font-weight:900; margin-bottom:6px;
                 background: linear-gradient(135deg, #7C3AED 0%, #A78BFA 50%, #C4B5FD 100%);
                 -webkit-background-clip: text; -webkit-text-fill-color: transparent;
                 background-clip: text; letter-spacing: -0.5px; }
#title-html p  { text-align:center; color:#64748B; font-size:0.9rem; margin-top:0; }

/* ── Company header ──────────────────────────────────────────────────────── */
#company-header { margin-top: 16px; }
#company-header h1, #company-header h2, #company-header h3 {
                     font-size:1.6rem; font-weight:800; color:#F1F5F9 !important;
                     border-bottom: 3px solid #7C3AED; padding-bottom:10px; margin-bottom:4px; }
#status-bar { font-size:0.85rem; color:#94A3B8; padding:4px 0 10px; }
#status-bar p { color:#94A3B8 !important; }

/* ── Inputs ──────────────────────────────────────────────────────────────── */
.input-row { align-items: flex-end !important; gap: 12px !important; }
.input-row label { color: #94A3B8 !important; }
.input-row input, .input-row textarea {
    border: 1.5px solid #334155 !important; border-radius: 10px !important;
    background: #1E293B !important; color: #E2E8F0 !important;
    font-size: 14px !important; }
.input-row input::placeholder, .input-row textarea::placeholder { color: #475569 !important; }
.input-row input:focus, .input-row textarea:focus {
    border-color: #7C3AED !important; box-shadow: 0 0 0 3px rgba(124,58,237,0.15) !important; }
#analyze-btn { min-height: 48px !important; border-radius: 10px !important;
               background: linear-gradient(135deg, #6D28D9, #8B5CF6) !important;
               border: none !important; font-weight: 700 !important; color: white !important;
               font-size: 15px !important; letter-spacing: 0.2px !important;
               box-shadow: 0 2px 8px rgba(109,40,217,0.4) !important; }
#analyze-btn:hover { background: linear-gradient(135deg, #5B21B6, #7C3AED) !important;
                     box-shadow: 0 4px 12px rgba(109,40,217,0.5) !important; }

/* ── Tabs ────────────────────────────────────────────────────────────────── */
.tab-content { padding-top: 16px; }
.tabs { background: #0F172A !important; }
.tabs > .tab-nav { background: #0F172A !important; border-bottom: 1px solid #1E293B !important; }
.tabs > .tab-nav > button { font-weight: 600 !important; color: #64748B !important;
                             background: transparent !important;
                             border-bottom: 2px solid transparent !important; }
.tabs > .tab-nav > button.selected { color: #A78BFA !important;
                                     border-bottom-color: #7C3AED !important; }
.tabs > .tab-nav > button:hover { color: #94A3B8 !important; }

/* ── Tab content — Markdown text ─────────────────────────────────────────── */
.tab-content p, .tab-content li, .tab-content span { color: #CBD5E1 !important; }
.tab-content h1, .tab-content h2, .tab-content h3, .tab-content h4 { color: #F1F5F9 !important; }
.tab-content strong, .tab-content b { color: #E2E8F0 !important; }
.tab-content a { color: #A78BFA !important; }
.tab-content code { background: #1E293B !important; color: #C4B5FD !important;
                    border: 1px solid #334155 !important; border-radius: 4px !important; }
.tab-content blockquote { border-left: 3px solid #4C1D95 !important;
                           color: #94A3B8 !important; }
.tab-content hr { border-color: #1E293B !important; }
.tab-content table { border-collapse: collapse !important; width: 100% !important; }
.tab-content th { background: #1E293B !important; color: #94A3B8 !important;
                  border: 1px solid #334155 !important; padding: 8px 12px !important; }
.tab-content td { color: #CBD5E1 !important; border: 1px solid #1E293B !important;
                  padding: 8px 12px !important; }

/* ── Markdown outside tab-content (header, status) ──────────────────────── */
.gradio-container .prose p { color: #CBD5E1 !important; }
.gradio-container .prose h1,
.gradio-container .prose h2,
.gradio-container .prose h3 { color: #F1F5F9 !important; }

/* ── Plots ───────────────────────────────────────────────────────────────── */
.gr-plot > .label { display: none !important; }
.gr-plot { border: 1px solid #1E293B !important; border-radius: 12px !important;
           overflow: hidden !important; background: #0F172A !important; }

/* ── HTML sections ───────────────────────────────────────────────────────── */
.gradio-html { color: #E2E8F0 !important; background: #0F172A !important; }
.gradio-html a div:hover { opacity: 0.85; transition: opacity 0.15s ease; }

/* ── Gradio label text (input field labels) ──────────────────────────────── */
.gradio-container label { color: #94A3B8 !important; }
.gradio-container .label-wrap span { color: #94A3B8 !important; }

/* ── Examples section ────────────────────────────────────────────────────── */
.gradio-container .examples { background: #0F172A !important; color: #94A3B8 !important; }
.gradio-container .examples td { color: #CBD5E1 !important;
                                  background: #1E293B !important;
                                  border: 1px solid #334155 !important; }
.gradio-container .examples th { color: #64748B !important;
                                  background: #0F172A !important; }
.gradio-container .examples button { color: #A78BFA !important;
                                      background: #1E293B !important;
                                      border: 1px solid #334155 !important; }

/* ── Download btn ────────────────────────────────────────────────────────── */
.gr-download-btn { border: 1.5px solid #334155 !important; border-radius: 8px !important;
                   color: #94A3B8 !important; background: #1E293B !important; }
.gr-download-btn:hover { border-color: #7C3AED !important; color: #A78BFA !important; }

/* ── Footer text ─────────────────────────────────────────────────────────── */
.gradio-container > div:last-child { color: #475569 !important; }
"""

TITLE_HTML = """
<div id='title-html'>
  <h1>🔍 ScoutAI</h1>
  <p>Smart company analyst agent &mdash; drop a URL &rarr; get a full intelligence report with live financial charts</p>
  <p style='color:#9CA3AF;font-size:0.78rem'>
    Powered by Groq &middot; LangGraph &middot; Yahoo Finance &middot; DuckDuckGo
  </p>
</div>
"""

# Hide API key input when GROQ_API_KEY is pre-set (e.g. HuggingFace Spaces secret)
_KEY_PRECONFIGURED = bool(os.getenv("GROQ_API_KEY", ""))

EXAMPLES = [
    ["https://apple.com",     ""],
    ["https://nvidia.com",    ""],
    ["https://shopify.com",   ""],
    ["https://stripe.com",    ""],
    ["https://openai.com",    ""],
]


# ─── Gradio Blocks UI ────────────────────────────────────────────────────────

with gr.Blocks(css=CSS, title="ScoutAI — Smart Company Analyst Agent") as demo:

    gr.HTML(TITLE_HTML)

    # ── Input row ──────────────────────────────────────────────────────────
    with gr.Row(elem_classes="input-row"):
        url_input = gr.Textbox(
            label="Company Website URL",
            placeholder="https://nvidia.com",
            scale=5,
        )
        api_key_input = gr.Textbox(
            label="Groq API Key",
            placeholder="gsk_... (free at console.groq.com)",
            type="password",
            scale=3,
            visible=not _KEY_PRECONFIGURED,
        )
        analyze_btn = gr.Button("🔍 Analyze", variant="primary", scale=1, elem_id="analyze-btn")

    # ── Company header + status ────────────────────────────────────────────
    company_header_md = gr.Markdown(value="", elem_id="company-header")
    status_md = gr.Markdown(value="", elem_id="status-bar")

    # ── Single-page Full AI Report ─────────────────────────────────────────
    # Row 1: 12 metric cards
    metrics_html_out = gr.HTML(value="")

    # Row 2: Stock chart + Revenue chart side by side
    with gr.Row():
        stock_plot   = gr.Plot(label="", show_label=False)
        revenue_plot = gr.Plot(label="", show_label=False)

    # Row 3: FCF chart + Margin expansion chart side by side
    with gr.Row():
        trends_plot = gr.Plot(label="", show_label=False)
        margin_plot = gr.Plot(label="", show_label=False)

    # Trader Scorecard (signal chips + key trade metrics)
    trader_scorecard_out = gr.HTML(value="")

    # Balance sheet health + competitor table
    health_html_out = gr.HTML(value="")
    comp_html_out   = gr.HTML(value="")

    # News cards
    news_html_out = gr.HTML(value="")

    # Full LLM report
    gr.HTML(
        "<div style='margin:24px 0 8px;padding-bottom:10px;border-bottom:2px solid #334155'>"
        "<span style='font-size:15px;font-weight:700;color:#F1F5F9'>📄 Full AI Intelligence Report</span>"
        "</div>"
    )
    full_report_md = gr.Markdown(value="", elem_classes="tab-content")

    # ── Download ───────────────────────────────────────────────────────────
    with gr.Row():
        download_btn = gr.DownloadButton(
            label="⬇ Download Full Report (.md)",
            variant="secondary",
            visible=True,
        )

    # ── Examples ───────────────────────────────────────────────────────────
    gr.Examples(
        examples=EXAMPLES,
        inputs=[url_input, api_key_input],
        label="Quick examples" if _KEY_PRECONFIGURED else "Quick examples (add your Groq key first)",
    )

    gr.HTML("""
    <div style='text-align:center;margin-top:32px;padding:20px 0 8px;
                border-top:1px solid #1E293B'>
      <div style='font-size:0.85rem;font-weight:600;color:#CBD5E1;margin-bottom:8px'>
        Developed by <span style='color:#A78BFA'>Iyman Ahmed</span>
      </div>
      <div style='display:flex;justify-content:center;align-items:center;gap:20px;
                  flex-wrap:wrap;font-size:0.78rem;margin-bottom:10px'>
        <a href='https://github.com/Iyman-Ahmed/ScoutAI--Smart-company-analyst-agent'
           target='_blank' rel='noopener'
           style='color:#64748B;text-decoration:none;display:flex;align-items:center;gap:5px'>
          <svg width='14' height='14' viewBox='0 0 24 24' fill='#64748B'>
            <path d='M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z'/>
          </svg>
          GitHub Repo
        </a>
        <a href='https://iymanahmed.tech' target='_blank' rel='noopener'
           style='color:#64748B;text-decoration:none;display:flex;align-items:center;gap:5px'>
          <svg width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='#64748B' stroke-width='2'>
            <circle cx='12' cy='12' r='10'/><line x1='2' y1='12' x2='22' y2='12'/>
            <path d='M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z'/>
          </svg>
          iymanahmed.tech
        </a>
        <a href='mailto:iyman12393@gmail.com'
           style='color:#64748B;text-decoration:none;display:flex;align-items:center;gap:5px'>
          <svg width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='#64748B' stroke-width='2'>
            <path d='M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z'/>
            <polyline points='22,6 12,13 2,6'/>
          </svg>
          iyman12393@gmail.com
        </a>
      </div>
      <div style='font-size:0.72rem;color:#334155'>
        &copy; 2025 Iyman Ahmed &nbsp;&middot;&nbsp; ScoutAI &nbsp;&middot;&nbsp;
        Data from public sources &nbsp;&middot;&nbsp; Not financial advice
      </div>
    </div>
    """)

    # ── Wire outputs ───────────────────────────────────────────────────────
    _outputs = [
        company_header_md,   # 0
        status_md,           # 1
        stock_plot,          # 2
        revenue_plot,        # 3
        metrics_html_out,    # 4
        trends_plot,         # 5  FCF chart
        margin_plot,         # 6  Margin expansion
        health_html_out,     # 7  Balance sheet health
        comp_html_out,       # 8  Competitor table
        news_html_out,       # 9  News cards
        trader_scorecard_out,# 10 Trader scorecard
        full_report_md,      # 11 Full LLM report
        download_btn,        # 12
    ]

    analyze_btn.click(fn=analyze_company, inputs=[url_input, api_key_input], outputs=_outputs)
    url_input.submit(fn=analyze_company,  inputs=[url_input, api_key_input], outputs=_outputs)


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_api=False,
    )
