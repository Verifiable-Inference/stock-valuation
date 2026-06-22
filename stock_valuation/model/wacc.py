"""Weighted Average Cost of Capital (WACC) — the discount rate r in the formula.

    Re   = Rf + beta * ERP                       (CAPM cost of equity)
    Rd   = interest_expense / total_debt          (cost of debt)
    WACC = E/V * Re + D/V * Rd * (1 - tax)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class WaccResult:
    wacc: float
    cost_of_equity: float
    cost_of_debt_after_tax: float
    equity_weight: float
    debt_weight: float
    risk_free: float
    beta: float
    equity_risk_premium: float
    total_debt: float
    cash: float                 # realizable add-back: cash & equivalents + current marketable securities
    cash_equivalents: float = 0.0
    marketable_securities: float = 0.0   # current marketable securities included in `cash`
    noncurrent_investments: float = 0.0  # excluded from `cash` (not reliably realizable)
    notes: list[str] = field(default_factory=list)


def _latest(annual: pd.DataFrame, col: str, default: float = 0.0) -> float:
    if col in annual and not annual[col].dropna().empty:
        return float(annual[col].dropna().iloc[-1])
    return default


def compute_wacc(
    annual: pd.DataFrame,
    *,
    market_cap: Optional[float],
    beta: Optional[float],
    risk_free: Optional[float],
    equity_risk_premium: float,
    tax_rate: float,
    fallback_cost_of_debt: float = 0.05,
    fallback_beta: float = 1.0,
    risk_free_override: Optional[float] = None,
    beta_override: Optional[float] = None,
    wacc_override: Optional[float] = None,
) -> WaccResult:
    notes: list[str] = []

    rf = risk_free_override if risk_free_override is not None else risk_free
    if rf is None:
        rf = 0.04
        notes.append("Risk-free rate unavailable -> defaulted to 4.0%")

    b = beta_override if beta_override is not None else beta
    if b is None:
        b = fallback_beta
        notes.append(f"Beta unavailable -> defaulted to {fallback_beta}")

    cost_of_equity = rf + b * equity_risk_premium

    total_debt = _latest(annual, "long_term_debt") + _latest(
        annual, "long_term_debt_current"
    )
    # Net-cash add-back: only assets that are reliably realizable for
    # shareholders — cash & equivalents plus *current* marketable securities.
    # Noncurrent investments are deliberately excluded: that line routinely
    # mixes in equity-method stakes and strategic/illiquid holdings that are
    # not liquidatable cash, so adding them dollar-for-dollar would inflate
    # equity value. They are tracked separately for transparency.
    cash_equivalents = _latest(annual, "cash")
    securities = _latest(annual, "short_term_investments")
    noncurrent_investments = _latest(annual, "long_term_investments")
    cash = cash_equivalents + securities
    if securities > 0:
        notes.append(
            f"Cash add-back includes {securities/1e9:,.1f}B current marketable "
            f"securities + {cash_equivalents/1e9:,.1f}B cash & equivalents"
        )
    if noncurrent_investments > 0:
        notes.append(
            f"Excluded {noncurrent_investments/1e9:,.1f}B noncurrent investments "
            f"from the cash add-back (may include illiquid / equity-method stakes)"
        )

    interest = _latest(annual, "interest_expense")
    if total_debt > 0 and interest > 0:
        cost_of_debt = min(interest / total_debt, 0.25)
    else:
        cost_of_debt = fallback_cost_of_debt
        if total_debt > 0:
            notes.append("Interest expense missing -> used fallback cost of debt")
    cost_of_debt_after_tax = cost_of_debt * (1 - tax_rate)

    equity = market_cap if market_cap and market_cap > 0 else 0.0
    if equity == 0.0:
        # Without an equity value we cannot weight equity vs. debt. Approximate
        # WACC with the (unlevered) cost of equity rather than mixing in the
        # cost of debt, which would otherwise collapse the discount rate toward
        # the low after-tax debt rate and grossly overvalue a leveraged firm.
        notes.append("Market cap unavailable -> WACC approximated as cost of equity")
        equity_weight, debt_weight = 1.0, 0.0
    else:
        v = equity + total_debt
        equity_weight = equity / v
        debt_weight = total_debt / v

    if wacc_override is not None:
        wacc = wacc_override
        notes.append(f"WACC overridden to {wacc_override:.2%}")
    else:
        wacc = equity_weight * cost_of_equity + debt_weight * cost_of_debt_after_tax

    return WaccResult(
        wacc=wacc,
        cost_of_equity=cost_of_equity,
        cost_of_debt_after_tax=cost_of_debt_after_tax,
        equity_weight=equity_weight,
        debt_weight=debt_weight,
        risk_free=rf,
        beta=b,
        equity_risk_premium=equity_risk_premium,
        total_debt=total_debt,
        cash=cash,
        cash_equivalents=cash_equivalents,
        marketable_securities=securities,
        noncurrent_investments=noncurrent_investments,
        notes=notes,
    )
