"""Market data via yfinance: price, shares, beta, market cap, risk-free proxy.

EDGAR is authoritative for fundamentals; yfinance is unofficial and only used
for market inputs the filings don't carry. Every field degrades gracefully.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MarketData:
    ticker: str
    price: Optional[float] = None
    shares_outstanding: Optional[float] = None
    beta: Optional[float] = None
    market_cap: Optional[float] = None
    risk_free_rate: Optional[float] = None
    notes: list[str] = field(default_factory=list)


def _safe_get(info: dict, *keys):
    for key in keys:
        val = info.get(key)
        if val is not None:
            return val
    return None


def fetch_risk_free_rate() -> Optional[float]:
    """10-year US Treasury yield via the ^TNX index (quoted x100)."""
    import yfinance as yf

    try:
        hist = yf.Ticker("^TNX").history(period="5d")
        if not hist.empty:
            return float(hist["Close"].dropna().iloc[-1]) / 100.0
    except Exception as exc:  # noqa: BLE001 - network/parse robustness
        logger.warning("Could not fetch ^TNX risk-free rate: %s", exc)
    return None


def fetch_fx_rate(currency: str) -> Optional[float]:
    """USD per one unit of `currency` (e.g. MXN -> ~0.055), via Yahoo's
    `<CCY>USD=X` pair which quotes USD per unit directly. Returns None on
    failure; returns 1.0 for USD."""
    if not currency or currency.upper() == "USD":
        return 1.0
    import yfinance as yf

    try:
        hist = yf.Ticker(f"{currency.upper()}USD=X").history(period="5d")
        if not hist.empty:
            rate = float(hist["Close"].dropna().iloc[-1])
            if rate > 0:
                return rate
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch FX rate for %s: %s", currency, exc)
    return None


def fetch_market_data(ticker: str) -> MarketData:
    import yfinance as yf

    md = MarketData(ticker=ticker.upper())
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception as exc:  # noqa: BLE001
        md.notes.append(f"yfinance .info failed: {exc}")
        info = {}

    md.price = _safe_get(info, "currentPrice", "regularMarketPrice", "previousClose")
    if md.price is None:
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if not hist.empty:
                md.price = float(hist["Close"].dropna().iloc[-1])
        except Exception as exc:  # noqa: BLE001
            md.notes.append(f"price history fallback failed: {exc}")

    md.shares_outstanding = _safe_get(info, "sharesOutstanding", "impliedSharesOutstanding")
    md.beta = _safe_get(info, "beta")
    md.market_cap = _safe_get(info, "marketCap")
    if md.market_cap is None and md.price and md.shares_outstanding:
        md.market_cap = md.price * md.shares_outstanding

    md.risk_free_rate = fetch_risk_free_rate()

    for label, value in (
        ("price", md.price),
        ("shares_outstanding", md.shares_outstanding),
        ("beta", md.beta),
    ):
        if value is None:
            md.notes.append(f"market data missing: {label}")
    return md
