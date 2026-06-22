"""Assemble the DCF: discount projected FCF + terminal value to enterprise
value, bridge to equity value, derive intrinsic per-share value, and build a
WACC x terminal-growth sensitivity grid.

    PV = Σ FCF_t / (1+r)^t  +  TV / (1+r)^n      (the reference formula)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .projections import Projection, terminal_value


@dataclass
class DcfResult:
    enterprise_value: float
    pv_explicit: float          # PV of explicit-period FCFs
    pv_terminal: float          # PV of terminal value
    terminal_value_undiscounted: float
    equity_value: float
    intrinsic_per_share: float
    shares: float
    total_debt: float
    cash: float
    wacc: float
    terminal_growth: float
    discounted_fcf: list[float]
    current_price: Optional[float]
    upside: Optional[float]     # fractional upside vs current price
    sensitivity: "Sensitivity"
    notes: list[str] = field(default_factory=list)


@dataclass
class Sensitivity:
    wacc_grid: list[float]
    growth_grid: list[float]
    per_share: list[list[float]]   # rows=wacc, cols=growth


def _present_value(
    fcf: list[float], wacc: float, terminal_growth: float
) -> tuple[float, float, float, float, list[float]]:
    n = len(fcf)
    discounted = [cf / (1 + wacc) ** (t + 1) for t, cf in enumerate(fcf)]
    pv_explicit = float(np.sum(discounted))
    tv = terminal_value(fcf[-1], wacc, terminal_growth)
    pv_terminal = tv / (1 + wacc) ** n
    ev = pv_explicit + pv_terminal
    return ev, pv_explicit, pv_terminal, tv, discounted


def _per_share_for(
    fcf: list[float],
    wacc: float,
    terminal_growth: float,
    total_debt: float,
    cash: float,
    shares: float,
) -> float:
    ev, _, _, _, _ = _present_value(fcf, wacc, terminal_growth)
    equity = ev - total_debt + cash
    return equity / shares if shares else float("nan")


def run_dcf(
    projection: Projection,
    *,
    wacc: float,
    terminal_growth: float,
    total_debt: float,
    cash: float,
    shares: float,
    current_price: Optional[float] = None,
) -> DcfResult:
    notes: list[str] = []
    if not shares or shares <= 0:
        raise ValueError("Shares outstanding required to compute per-share value")

    ev, pv_explicit, pv_terminal, tv, discounted = _present_value(
        projection.fcf, wacc, terminal_growth
    )
    equity_value = ev - total_debt + cash
    intrinsic = equity_value / shares

    upside = None
    if current_price:
        upside = intrinsic / current_price - 1

    sensitivity = build_sensitivity(
        projection.fcf,
        base_wacc=wacc,
        base_growth=terminal_growth,
        total_debt=total_debt,
        cash=cash,
        shares=shares,
    )

    return DcfResult(
        enterprise_value=ev,
        pv_explicit=pv_explicit,
        pv_terminal=pv_terminal,
        terminal_value_undiscounted=tv,
        equity_value=equity_value,
        intrinsic_per_share=intrinsic,
        shares=shares,
        total_debt=total_debt,
        cash=cash,
        wacc=wacc,
        terminal_growth=terminal_growth,
        discounted_fcf=discounted,
        current_price=current_price,
        upside=upside,
        sensitivity=sensitivity,
        notes=notes,
    )


def build_sensitivity(
    fcf: list[float],
    *,
    base_wacc: float,
    base_growth: float,
    total_debt: float,
    cash: float,
    shares: float,
    wacc_steps: tuple[float, ...] = (-0.02, -0.01, 0.0, 0.01, 0.02),
    growth_steps: tuple[float, ...] = (-0.01, -0.005, 0.0, 0.005, 0.01),
) -> Sensitivity:
    wacc_grid = [round(base_wacc + s, 4) for s in wacc_steps]
    growth_grid = [round(base_growth + s, 4) for s in growth_steps]
    matrix: list[list[float]] = []
    for w in wacc_grid:
        row = []
        for g in growth_grid:
            if w <= g:
                row.append(float("nan"))
            else:
                row.append(
                    _per_share_for(fcf, w, g, total_debt, cash, shares)
                )
        matrix.append(row)
    return Sensitivity(wacc_grid=wacc_grid, growth_grid=growth_grid, per_share=matrix)
