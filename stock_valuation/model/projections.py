"""Project future free cash flows and estimate the terminal value.

Growth is seeded from the historical FCF CAGR (clamped to a sane band) and then
linearly decayed toward the perpetual terminal-growth rate across the horizon,
so the firm doesn't grow at its recent rate forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Projection:
    base_fcf: float
    years: list[int]                 # 1..n forecast period indices
    fcf: list[float]                 # projected FCF per year
    growth_rates: list[float]        # growth applied each year
    initial_growth: float
    terminal_growth: float
    notes: list[str] = field(default_factory=list)


def estimate_initial_growth(
    series: pd.Series,
    *,
    max_growth: float,
    min_growth: float,
    label: str = "FCF",
    fallback: float = 0.04,
) -> tuple[float, str]:
    """CAGR over the available positive-endpoint history, clamped to a band."""
    clean = series.dropna()
    clean = clean[clean != 0]
    if len(clean) >= 2:
        first, last = clean.iloc[0], clean.iloc[-1]
        periods = len(clean) - 1
        if first > 0 and last > 0:
            cagr = (last / first) ** (1 / periods) - 1
            clamped = float(np.clip(cagr, min_growth, max_growth))
            src = f"historical {label} CAGR {cagr:.1%}"
            if clamped != cagr:
                src += f" (clamped to {clamped:.1%})"
            return clamped, src
    return fallback, f"insufficient history -> default {fallback:.1%}"


def trailing_base_fcf(
    fcf_history: pd.Series, base_fcf_years: int
) -> tuple[float, str]:
    """Average the last ``base_fcf_years`` FCFs to dampen single-year noise
    (e.g. lumpy ΔNWC / capex). Falls back to the latest year if averaging is
    not meaningful."""
    clean = fcf_history.dropna()
    if base_fcf_years > 1 and len(clean) >= 2:
        window = clean.tail(min(base_fcf_years, len(clean)))
        return float(window.mean()), (
            f"trailing {len(window)}-year average FCF"
        )
    return float(clean.iloc[-1]), "latest-year FCF"


def normalized_base_fcf(
    fcf_history: pd.Series, revenue_history: Optional[pd.Series]
) -> tuple[float, str]:
    """Mid-cycle normalization for cyclical firms.

    Averages the FCF margin (FCF / revenue) across the full available history to
    capture *normalized* profitability, then applies that mid-cycle margin to the
    *latest* revenue to capture current scale. This avoids anchoring the base on
    a single boom or bust year.
    """
    if revenue_history is None:
        raise ValueError("Normalized base requires revenue history")
    df = pd.concat(
        {"fcf": fcf_history, "rev": revenue_history}, axis=1
    ).dropna()
    df = df[df["rev"] > 0]
    if df.empty:
        raise ValueError("No overlapping FCF/revenue years for normalization")
    # Dollar-weighted (aggregate) margin: Σ FCF / Σ revenue. Robust to a single
    # low-revenue trough year whose ratio would otherwise dominate a simple mean.
    mid_margin = float(df["fcf"].sum() / df["rev"].sum())
    latest_rev = float(df["rev"].iloc[-1])
    base = mid_margin * latest_rev
    return base, (
        f"mid-cycle FCF margin {mid_margin:.1%} (cumulative {len(df)} yrs) "
        f"× latest revenue {latest_rev/1e9:,.1f}B"
    )


def project_fcf(
    fcf_history: pd.Series,
    *,
    projection_years: int,
    terminal_growth: float,
    max_growth: float,
    min_growth: float,
    growth_override: Optional[float] = None,
    base_fcf_years: int = 3,
    base_fcf_method: str = "trailing",
    revenue_history: Optional[pd.Series] = None,
) -> Projection:
    notes: list[str] = []
    if base_fcf_method == "normalized":
        try:
            base_fcf, base_src = normalized_base_fcf(fcf_history, revenue_history)
        except ValueError as exc:
            # Revenue history unavailable (e.g. filer tags revenue unusually);
            # degrade to the trailing average rather than fail outright.
            base_fcf, base_src = trailing_base_fcf(fcf_history, base_fcf_years)
            notes.append(f"Normalized base unavailable ({exc}); used trailing average")
            base_fcf_method = "trailing"  # so growth also uses the FCF basis
    else:
        base_fcf, base_src = trailing_base_fcf(fcf_history, base_fcf_years)
    notes.append(f"Base FCF: {base_src}")
    if base_fcf <= 0:
        notes.append(
            f"Base FCF is non-positive ({base_fcf:,.0f}); valuation may be unreliable"
        )
    # Warn when a rapidly-growing company's most recent FCF is well above the
    # trailing average — the average understates current run rate in this case.
    if base_fcf_method != "normalized":
        clean_fcf = fcf_history.dropna()
        if len(clean_fcf) >= 1 and base_fcf > 0:
            latest = float(clean_fcf.iloc[-1])
            if latest > 1.5 * base_fcf:
                notes.append(
                    f"Warning: most recent FCF (${latest/1e9:,.1f}B) is "
                    f"{latest/base_fcf:.1f}× the trailing average base "
                    f"(${base_fcf/1e9:,.1f}B). The trailing average may significantly "
                    f"understate current run rate. Consider --base-fcf-years 1 to "
                    f"anchor on the most recent year instead."
                )

    if growth_override is not None:
        initial_growth = growth_override
        notes.append(f"Initial growth overridden to {growth_override:.1%}")
    elif base_fcf_method == "normalized" and revenue_history is not None:
        # For cyclicals, grow normalized FCF at the steadier revenue CAGR
        # (assuming the mid-cycle margin holds) rather than the noisy FCF CAGR.
        initial_growth, src = estimate_initial_growth(
            revenue_history, max_growth=max_growth, min_growth=min_growth,
            label="revenue",
        )
        notes.append(f"Initial growth: {src}")
    else:
        initial_growth, src = estimate_initial_growth(
            fcf_history, max_growth=max_growth, min_growth=min_growth
        )
        notes.append(f"Initial growth: {src}")

    years, fcfs, growths = [], [], []
    prev = base_fcf
    for t in range(1, projection_years + 1):
        # Linearly fade from initial growth (t=1) to terminal growth (t=n).
        if projection_years > 1:
            frac = (t - 1) / (projection_years - 1)
        else:
            frac = 1.0
        g = initial_growth + frac * (terminal_growth - initial_growth)
        prev = prev * (1 + g)
        years.append(t)
        fcfs.append(prev)
        growths.append(g)

    return Projection(
        base_fcf=base_fcf,
        years=years,
        fcf=fcfs,
        growth_rates=growths,
        initial_growth=initial_growth,
        terminal_growth=terminal_growth,
        notes=notes,
    )


def terminal_value(last_fcf: float, wacc: float, terminal_growth: float) -> float:
    """Gordon-growth terminal value: FCF_n*(1+g)/(WACC-g). Requires WACC > g."""
    if wacc <= terminal_growth:
        raise ValueError(
            f"WACC ({wacc:.2%}) must exceed terminal growth ({terminal_growth:.2%}) "
            "for a finite terminal value"
        )
    return last_fcf * (1 + terminal_growth) / (wacc - terminal_growth)
