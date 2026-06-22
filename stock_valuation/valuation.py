"""Orchestrate a full DCF valuation for a single ticker, end to end."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .config import Assumptions
from .data.cache import JsonCache
from .data.edgar import CompanyFinancials, EdgarClient
from .data.market import MarketData, fetch_fx_rate, fetch_market_data
from .model.dcf import DcfResult, run_dcf
from .model.fcf import FcfHistory, compute_fcf_history
from .model.projections import Projection, project_fcf
from .model.wacc import WaccResult, compute_wacc

logger = logging.getLogger(__name__)


@dataclass
class ValuationResult:
    ticker: str
    assumptions: Assumptions
    financials: CompanyFinancials
    market: MarketData
    fcf_history: FcfHistory
    wacc: WaccResult
    projection: Projection
    dcf: DcfResult

    def all_notes(self) -> list[str]:
        notes: list[str] = []
        notes += [f"[data] {n}" for n in self.financials.notes]
        notes += [f"[market] {n}" for n in self.market.notes]
        notes += [f"[fcf] {n}" for n in self.fcf_history.notes]
        notes += [f"[wacc] {n}" for n in self.wacc.notes]
        notes += [f"[projection] {n}" for n in self.projection.notes]
        notes += [f"[dcf] {n}" for n in self.dcf.notes]
        return notes


def value_stock(
    ticker: str,
    assumptions: Assumptions,
    *,
    edgar: Optional[EdgarClient] = None,
) -> ValuationResult:
    """Run the full pipeline. Raises on unrecoverable data gaps."""
    edgar = edgar or EdgarClient()

    financials = edgar.get_financials(ticker, history_years=assumptions.history_years)
    if financials.annual.empty:
        raise ValueError(f"No usable EDGAR financial data for {ticker}")

    # Foreign filers report in their local currency; convert all monetary figures
    # to USD so the (USD-based) market data, WACC and per-share value are
    # consistent. Using the current spot rate for all years is a deliberate
    # simplification: it preserves growth rates (a constant scalar) and only
    # rescales absolute levels.
    if financials.currency != "USD":
        fx = fetch_fx_rate(financials.currency)
        if not fx:
            raise ValueError(
                f"Could not obtain {financials.currency}->USD FX rate to value "
                f"{ticker.upper()} (reports in {financials.currency})."
            )
        money_cols = [c for c in financials.annual.columns if c != "shares_outstanding"]
        financials.annual[money_cols] = financials.annual[money_cols] * fx
        financials.notes.append(
            f"Converted {financials.currency}->USD at spot {fx:.4f} (all years)"
        )

    market = fetch_market_data(ticker)

    # Shares: prefer market data, fall back to latest filing.
    shares = market.shares_outstanding
    if not shares and "shares_outstanding" in financials.annual:
        col = financials.annual["shares_outstanding"].dropna()
        if not col.empty:
            shares = float(col.iloc[-1])
    if not shares:
        raise ValueError(f"Shares outstanding unavailable for {ticker}")

    fcf_history = compute_fcf_history(
        financials.annual, fallback_tax_rate=assumptions.fallback_tax_rate
    )

    tax_rate = (
        assumptions.tax_rate_override
        if assumptions.tax_rate_override is not None
        else fcf_history.effective_tax_rate
    )

    wacc = compute_wacc(
        financials.annual,
        market_cap=market.market_cap,
        beta=market.beta if assumptions.beta_override is None else assumptions.beta_override,
        risk_free=market.risk_free_rate,
        equity_risk_premium=assumptions.equity_risk_premium,
        tax_rate=tax_rate,
        fallback_cost_of_debt=assumptions.fallback_cost_of_debt,
        fallback_beta=assumptions.fallback_beta,
        risk_free_override=assumptions.risk_free_override,
        beta_override=assumptions.beta_override,
        wacc_override=assumptions.wacc_override,
    )

    revenue_history = (
        financials.annual["revenue"] if "revenue" in financials.annual else None
    )
    projection = project_fcf(
        fcf_history.table["fcff"],
        projection_years=assumptions.projection_years,
        terminal_growth=assumptions.terminal_growth,
        max_growth=assumptions.max_growth,
        min_growth=assumptions.min_growth,
        growth_override=assumptions.growth_override,
        base_fcf_years=assumptions.base_fcf_years,
        base_fcf_method=assumptions.base_fcf_method,
        revenue_history=revenue_history,
    )

    # A standard unlevered DCF is only meaningful for firms with positive,
    # reasonably stable cash generation. Refuse rather than emit a misleading
    # negative "valuation".
    if projection.base_fcf <= 0:
        raise ValueError(
            f"DCF not applicable: normalized base FCF is negative "
            f"(${projection.base_fcf / 1e9:,.1f}B). {ticker.upper()} is FCF-negative "
            f"or deeply cyclical, so a standard FCFF DCF cannot value it."
        )

    # The Gordon terminal value is only finite (and positive) when the discount
    # rate exceeds the perpetual growth rate. Refuse with a clear message rather
    # than letting terminal_value() raise deep in the DCF.
    if wacc.wacc <= assumptions.terminal_growth:
        raise ValueError(
            f"WACC ({wacc.wacc:.2%}) does not exceed terminal growth "
            f"({assumptions.terminal_growth:.2%}) for {ticker.upper()}, so the "
            f"Gordon terminal value is undefined. Lower --terminal-growth or "
            f"raise the discount rate (e.g. --wacc)."
        )

    dcf = run_dcf(
        projection,
        wacc=wacc.wacc,
        terminal_growth=assumptions.terminal_growth,
        total_debt=wacc.total_debt,
        cash=wacc.cash,
        shares=shares,
        current_price=market.price,
    )

    if dcf.equity_value <= 0:
        raise ValueError(
            f"DCF implies negative equity value (${dcf.equity_value / 1e9:,.1f}B): "
            f"discounted cash flows plus cash & investments "
            f"(${dcf.cash / 1e9:,.1f}B) do not cover total debt "
            f"(${dcf.total_debt / 1e9:,.1f}B)."
        )

    return ValuationResult(
        ticker=ticker.upper(),
        assumptions=assumptions,
        financials=financials,
        market=market,
        fcf_history=fcf_history,
        wacc=wacc,
        projection=projection,
        dcf=dcf,
    )
