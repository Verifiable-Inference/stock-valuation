"""Unit tests for the DCF math, FCF assembly, WACC, and projections."""

import math

import pandas as pd
import pytest

from stock_valuation.model.dcf import run_dcf
from stock_valuation.model.fcf import compute_fcf_history
from stock_valuation.model.projections import (
    project_fcf,
    terminal_value,
)
from stock_valuation.model.wacc import compute_wacc


def test_terminal_value_closed_form():
    # FCF=100, WACC=10%, g=2% -> 100*1.02/0.08 = 1275
    assert terminal_value(100.0, 0.10, 0.02) == pytest.approx(1275.0)


def test_terminal_value_requires_wacc_above_growth():
    with pytest.raises(ValueError):
        terminal_value(100.0, 0.02, 0.03)


def test_dcf_flat_fcf_matches_perpetuity_intuition():
    # Flat FCF of 100, WACC 10%, terminal growth 0% over 5 years.
    proj = project_fcf(
        pd.Series([100.0, 100.0, 100.0], index=[2021, 2022, 2023]),
        projection_years=5,
        terminal_growth=0.0,
        max_growth=0.15,
        min_growth=-0.05,
        growth_override=0.0,  # force flat
    )
    res = run_dcf(
        proj,
        wacc=0.10,
        terminal_growth=0.0,
        total_debt=0.0,
        cash=0.0,
        shares=10.0,
        current_price=None,
    )
    # Closed form: EV = sum_{t=1..5} 100/1.1^t + (100/0.1)/1.1^5
    expected_explicit = sum(100 / 1.1 ** t for t in range(1, 6))
    expected_tv_pv = (100 / 0.10) / 1.1 ** 5
    assert res.pv_explicit == pytest.approx(expected_explicit)
    assert res.pv_terminal == pytest.approx(expected_tv_pv)
    assert res.enterprise_value == pytest.approx(expected_explicit + expected_tv_pv)
    assert res.intrinsic_per_share == pytest.approx(res.equity_value / 10.0)


def test_dcf_equity_bridge_subtracts_debt_adds_cash():
    proj = project_fcf(
        pd.Series([100.0, 100.0], index=[2022, 2023]),
        projection_years=3,
        terminal_growth=0.0,
        max_growth=0.15,
        min_growth=-0.05,
        growth_override=0.0,
    )
    res = run_dcf(
        proj, wacc=0.10, terminal_growth=0.0,
        total_debt=500.0, cash=200.0, shares=100.0, current_price=50.0,
    )
    assert res.equity_value == pytest.approx(res.enterprise_value - 500.0 + 200.0)
    assert res.upside == pytest.approx(res.intrinsic_per_share / 50.0 - 1)


def test_fcff_assembly():
    annual = pd.DataFrame(
        {
            "ebit": [1000.0, 1100.0],
            "dep_amort": [200.0, 220.0],
            "capex": [300.0, 330.0],
            "assets_current": [5000.0, 5400.0],
            "liabilities_current": [3000.0, 3200.0],
            "tax_expense": [210.0, 231.0],
            "pretax_income": [1000.0, 1100.0],
        },
        index=[2022, 2023],
    )
    hist = compute_fcf_history(annual, fallback_tax_rate=0.21)
    assert hist.method == "fcff"
    assert hist.effective_tax_rate == pytest.approx(0.21, abs=1e-6)
    # 2023: NOPAT=1100*0.79=869; +220 -330 -ΔNWC
    # NWC: 2022=2000, 2023=2200 -> ΔNWC=200
    expected_2023 = 1100 * (1 - 0.21) + 220 - 330 - 200
    assert hist.table.loc[2023, "fcff"] == pytest.approx(expected_2023)


def test_fcff_fallback_to_ocf_minus_capex():
    annual = pd.DataFrame(
        {"operating_cash_flow": [800.0, 900.0], "capex": [300.0, 350.0]},
        index=[2022, 2023],
    )
    hist = compute_fcf_history(annual)
    assert hist.method == "ocf_capex"
    assert hist.table.loc[2023, "fcff"] == pytest.approx(550.0)


def test_wacc_capm_and_weights():
    annual = pd.DataFrame(
        {
            "long_term_debt": [1000.0],
            "interest_expense": [50.0],
            "cash": [200.0],
        },
        index=[2023],
    )
    res = compute_wacc(
        annual,
        market_cap=3000.0,
        beta=1.2,
        risk_free=0.04,
        equity_risk_premium=0.05,
        tax_rate=0.21,
    )
    assert res.cost_of_equity == pytest.approx(0.04 + 1.2 * 0.05)  # 0.10
    # Rd = 50/1000 = 5%; after tax = 5% * 0.79
    assert res.cost_of_debt_after_tax == pytest.approx(0.05 * 0.79)
    # weights: E=3000, D=1000, V=4000
    assert res.equity_weight == pytest.approx(0.75)
    assert res.debt_weight == pytest.approx(0.25)
    expected = 0.75 * 0.10 + 0.25 * 0.05 * 0.79
    assert res.wacc == pytest.approx(expected)


def test_wacc_falls_back_to_cost_of_equity_when_market_cap_missing():
    # With no market cap, WACC must approximate cost of equity, NOT collapse to
    # the (much lower) after-tax cost of debt even when the firm carries debt.
    annual = pd.DataFrame(
        {"long_term_debt": [1000.0], "interest_expense": [50.0]},
        index=[2023],
    )
    res = compute_wacc(
        annual,
        market_cap=None,
        beta=1.2,
        risk_free=0.04,
        equity_risk_premium=0.05,
        tax_rate=0.21,
    )
    assert res.equity_weight == pytest.approx(1.0)
    assert res.debt_weight == pytest.approx(0.0)
    assert res.wacc == pytest.approx(res.cost_of_equity)  # 0.10, not ~0.04


def test_wacc_cash_addback_excludes_noncurrent_investments():
    # The realizable cash add-back should include cash + current marketable
    # securities, but NOT noncurrent investments (illiquid / equity-method).
    annual = pd.DataFrame(
        {
            "cash": [200.0],
            "short_term_investments": [100.0],
            "long_term_investments": [500.0],
        },
        index=[2023],
    )
    res = compute_wacc(
        annual,
        market_cap=3000.0,
        beta=1.0,
        risk_free=0.04,
        equity_risk_premium=0.05,
        tax_rate=0.21,
    )
    assert res.cash == pytest.approx(300.0)  # 200 cash + 100 current securities
    assert res.marketable_securities == pytest.approx(100.0)
    assert res.noncurrent_investments == pytest.approx(500.0)  # tracked, excluded


def test_projection_growth_decay():
    proj = project_fcf(
        pd.Series([100.0], index=[2023]),
        projection_years=5,
        terminal_growth=0.02,
        max_growth=0.15,
        min_growth=-0.05,
        growth_override=0.10,
    )
    # First year grows at 10%, last year fades to terminal 2%.
    assert proj.growth_rates[0] == pytest.approx(0.10)
    assert proj.growth_rates[-1] == pytest.approx(0.02)
    assert proj.fcf[0] == pytest.approx(110.0)


def test_normalized_base_rescues_cyclical():
    # Cyclical FCF (boom/bust) with steadily growing revenue. Trailing-3yr avg
    # catches the trough; normalized uses mid-cycle margin x latest revenue.
    fcf = pd.Series([8.0, 9.0, 7.0, -5.0, 2.0], index=[2021, 2022, 2023, 2024, 2025])
    rev = pd.Series([50.0, 55.0, 60.0, 40.0, 70.0], index=[2021, 2022, 2023, 2024, 2025])

    trailing = project_fcf(
        fcf, revenue_history=rev, base_fcf_method="trailing",
        projection_years=5, terminal_growth=0.02, max_growth=0.15, min_growth=-0.05,
    )
    normalized = project_fcf(
        fcf, revenue_history=rev, base_fcf_method="normalized",
        projection_years=5, terminal_growth=0.02, max_growth=0.15, min_growth=-0.05,
    )
    # Dollar-weighted mid-cycle margin = sum(fcf)/sum(rev), x latest revenue (70).
    expected = (fcf.sum() / rev.sum()) * 70.0
    assert normalized.base_fcf == pytest.approx(expected)
    # Trailing 3yr avg ((7-5+2)/3=1.33) is dragged down by the trough; normalized
    # recovers a mid-cycle figure that is clearly higher.
    assert normalized.base_fcf > trailing.base_fcf
    # Growth should track revenue CAGR (positive), not the noisy FCF series.
    assert normalized.initial_growth > 0


def test_sensitivity_grid_has_nan_when_wacc_le_growth():
    proj = project_fcf(
        pd.Series([100.0, 100.0], index=[2022, 2023]),
        projection_years=3, terminal_growth=0.02,
        max_growth=0.15, min_growth=-0.05, growth_override=0.0,
    )
    res = run_dcf(
        proj, wacc=0.03, terminal_growth=0.02,
        total_debt=0.0, cash=0.0, shares=10.0,
    )
    flat = [v for row in res.sensitivity.per_share for v in row]
    assert any(math.isnan(v) for v in flat)
