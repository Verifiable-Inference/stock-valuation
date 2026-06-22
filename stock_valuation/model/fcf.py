"""Compute historical unlevered free cash flow (FCFF) from annual financials.

    FCFF = EBIT * (1 - effective_tax) + D&A - CapEx - ΔNWC

Falls back to ``Operating Cash Flow - CapEx`` when income-statement components
are missing. NWC = current assets - current liabilities; ΔNWC is the year-over-
year change (an increase in NWC consumes cash).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class FcfHistory:
    table: pd.DataFrame          # per-year components and resulting fcff
    effective_tax_rate: float
    method: str                  # "fcff" or "ocf_capex"
    notes: list[str] = field(default_factory=list)


def _effective_tax_rate(annual: pd.DataFrame, fallback: float) -> tuple[float, str]:
    if "tax_expense" in annual and "pretax_income" in annual:
        tax = annual["tax_expense"]
        pretax = annual["pretax_income"]
        valid = pretax[pretax > 0]
        if not valid.empty:
            rates = (tax.loc[valid.index] / valid).clip(0, 0.6)
            rate = float(rates.mean())
            if np.isfinite(rate) and rate > 0:
                return rate, "computed from filings"
    return fallback, "fallback default"


def compute_fcf_history(
    annual: pd.DataFrame, fallback_tax_rate: float = 0.21
) -> FcfHistory:
    notes: list[str] = []
    if annual.empty:
        raise ValueError("No annual financial data available to compute FCF")

    df = annual.copy()
    tax_rate, tax_src = _effective_tax_rate(df, fallback_tax_rate)
    notes.append(f"Effective tax rate {tax_rate:.1%} ({tax_src})")

    has_fcff_inputs = all(c in df for c in ("ebit", "capex"))
    out = pd.DataFrame(index=df.index)

    if has_fcff_inputs:
        ebit = df["ebit"]
        nopat = ebit * (1 - tax_rate)
        # D&A may be absent or only reported in some years; a missing value is
        # treated as 0 so it doesn't NaN-out the whole FCFF series.
        if "dep_amort" in df:
            dep = df["dep_amort"].fillna(0.0)
        else:
            dep = pd.Series(0.0, index=df.index)
            notes.append("D&A missing -> treated as 0")
        capex = df["capex"].abs()  # reported as outflow; use magnitude

        if "assets_current" in df and "liabilities_current" in df:
            # Operating NWC excludes cash and the current portion of debt, which
            # otherwise inject large non-operating swings (e.g. short-term moves
            # in cash/financing) into ΔNWC and thus into FCF.
            op_assets = df["assets_current"]
            if "cash" in df:
                op_assets = op_assets - df["cash"].fillna(0.0)
            if "short_term_investments" in df:
                op_assets = op_assets - df["short_term_investments"].fillna(0.0)
            op_liabs = df["liabilities_current"]
            if "long_term_debt_current" in df:
                op_liabs = op_liabs - df["long_term_debt_current"].fillna(0.0)
            nwc = op_assets - op_liabs
            delta_nwc = nwc.diff()
        else:
            delta_nwc = pd.Series(0.0, index=df.index)
            notes.append("Working-capital components missing -> ΔNWC treated as 0")

        out["nopat"] = nopat
        out["dep_amort"] = dep
        out["capex"] = capex
        out["delta_nwc"] = delta_nwc.fillna(0.0)
        out["fcff"] = nopat + dep - capex - out["delta_nwc"]
        method = "fcff"
    elif "operating_cash_flow" in df and "capex" in df:
        out["operating_cash_flow"] = df["operating_cash_flow"]
        out["capex"] = df["capex"].abs()
        out["fcff"] = out["operating_cash_flow"] - out["capex"]
        method = "ocf_capex"
        notes.append("Used OCF - CapEx fallback (EBIT-based inputs unavailable)")
    else:
        raise ValueError(
            "Insufficient data for FCF: need (EBIT, CapEx) or (OperatingCashFlow, CapEx)"
        )

    # First-year ΔNWC is NaN (no prior year); drop only if it breaks the row.
    out = out.dropna(subset=["fcff"])
    if out.empty:
        raise ValueError(
            "Could not compute FCF: the required inputs (EBIT/D&A/CapEx or "
            "OCF/CapEx) never overlap in the same fiscal year. Filing history is "
            "too sparse or inconsistent for a DCF."
        )
    return FcfHistory(table=out, effective_tax_rate=tax_rate, method=method, notes=notes)
