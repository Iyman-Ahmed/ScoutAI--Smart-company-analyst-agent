"""Source provenance shared by the financial prompt and dashboard."""
def format_financial_status(is_public, ticker, sources):
    if not is_public:
        return "🔒 private company / no public financial data found"
    yahoo = sources.get("yahoo_finance", "unknown")
    sec = sources.get("sec_edgar") == "ok"
    if yahoo == "ok":
        return f"📈 {ticker or ''} · Yahoo Finance" + (" + SEC EDGAR" if sec else "")
    if yahoo.startswith("partial"):
        return "⚠️ Yahoo Finance partially available — some market data or fundamentals unavailable" + ("; SEC filings available" if sec else "")
    return "⚠️ Yahoo Finance unavailable" + (" — financial figures from SEC filings only" if sec else " — financial data unavailable") + ("; stock history from Stooq" if sources.get("stooq") == "ok" else "")
