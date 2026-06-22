"""Render matplotlib charts to base64-encoded PNGs for self-contained HTML."""

from __future__ import annotations

import base64
import io
import math
from typing import TYPE_CHECKING, Optional

import matplotlib

matplotlib.use("Agg")  # headless backend
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

if TYPE_CHECKING:  # avoid import cycle at runtime
    from ..valuation import ValuationResult

_ACCENT = "#2563eb"
_ACCENT2 = "#16a34a"
_NEG = "#dc2626"
_GRID = "#e5e7eb"


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")


def _style(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _billions(x: float) -> float:
    return x / 1e9


def history_chart(result: "ValuationResult") -> str:
    """Historical revenue (bars) and FCF (line), in $B."""
    annual = result.financials.annual
    fcf_table = result.fcf_history.table
    fig, ax = plt.subplots(figsize=(7, 3.6))
    years = [int(y) for y in fcf_table.index]

    if "revenue" in annual:
        rev = annual["revenue"].reindex(fcf_table.index)
        ax.bar(years, [_billions(v) for v in rev], color=_ACCENT, alpha=0.55, label="Revenue")
    ax.plot(
        years,
        [_billions(v) for v in fcf_table["fcff"]],
        color=_ACCENT2,
        marker="o",
        linewidth=2,
        label="Free cash flow",
    )
    ax.set_title("Historical revenue & free cash flow")
    ax.set_ylabel("$ billions")
    ax.set_xticks(years)
    _style(ax)
    ax.legend(frameon=False, fontsize=9)
    return _fig_to_base64(fig)


def projection_chart(result: "ValuationResult") -> str:
    """Projected FCF bars with discounted PV overlay, in $B."""
    proj = result.projection
    dcf = result.dcf
    fig, ax = plt.subplots(figsize=(7, 3.6))
    x = proj.years
    ax.bar(x, [_billions(v) for v in proj.fcf], color=_ACCENT, alpha=0.6, label="Projected FCF")
    ax.bar(
        x,
        [_billions(v) for v in dcf.discounted_fcf],
        color=_ACCENT2,
        alpha=0.9,
        width=0.5,
        label="Discounted (PV)",
    )
    ax.set_title("Projected free cash flow vs. present value")
    ax.set_xlabel("Forecast year")
    ax.set_ylabel("$ billions")
    ax.set_xticks(x)
    _style(ax)
    ax.legend(frameon=False, fontsize=9)
    return _fig_to_base64(fig)


def bridge_chart(result: "ValuationResult") -> str:
    """Waterfall: enterprise value -> (- debt) -> (+ cash) -> equity value, $B."""
    dcf = result.dcf
    fig, ax = plt.subplots(figsize=(7, 3.6))

    steps = [
        ("Enterprise\nvalue", _billions(dcf.enterprise_value), _ACCENT),
        ("- Debt", -_billions(dcf.total_debt), _NEG),
        ("+ Cash &\ninvestments", _billions(dcf.cash), _ACCENT2),
    ]
    running = 0.0
    for i, (label, delta, color) in enumerate(steps):
        bottom = running if delta >= 0 else running + delta
        ax.bar(i, abs(delta), bottom=bottom, color=color, alpha=0.85)
        running += delta
    ax.bar(len(steps), _billions(dcf.equity_value), color="#7c3aed", alpha=0.85)

    labels = [s[0] for s in steps] + ["Equity\nvalue"]
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_title("Enterprise value → equity value bridge")
    ax.set_ylabel("$ billions")
    _style(ax)
    return _fig_to_base64(fig)


def sensitivity_chart(result: "ValuationResult") -> str:
    """Heatmap of intrinsic per-share value across WACC x terminal growth."""
    sens = result.dcf.sensitivity
    data = np.array(sens.per_share, dtype=float)
    fig, ax = plt.subplots(figsize=(7, 4))
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto")

    ax.set_xticks(range(len(sens.growth_grid)))
    ax.set_xticklabels([f"{g:.1%}" for g in sens.growth_grid])
    ax.set_yticks(range(len(sens.wacc_grid)))
    ax.set_yticklabels([f"{w:.1%}" for w in sens.wacc_grid])
    ax.set_xlabel("Terminal growth")
    ax.set_ylabel("WACC")
    ax.set_title("Intrinsic value / share sensitivity")

    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            val = data[i, j]
            txt = "n/a" if math.isnan(val) else f"{val:,.0f}"
            ax.text(j, i, txt, ha="center", va="center", fontsize=8, color="#111827")
    fig.colorbar(im, ax=ax, label="$ / share", fraction=0.046, pad=0.04)
    return _fig_to_base64(fig)


def price_vs_value_chart(result: "ValuationResult") -> str:
    """Bar: current market price vs. intrinsic value."""
    dcf = result.dcf
    fig, ax = plt.subplots(figsize=(5, 3.6))
    labels, values, colors = [], [], []
    if dcf.current_price:
        labels.append("Market price")
        values.append(dcf.current_price)
        colors.append("#6b7280")
    labels.append("Intrinsic value")
    values.append(dcf.intrinsic_per_share)
    colors.append(_ACCENT2 if (dcf.upside or 0) >= 0 else _NEG)

    bars = ax.bar(labels, values, color=colors, alpha=0.85)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v, f"${v:,.0f}",
                ha="center", va="bottom", fontsize=10)
    title = "Price vs. intrinsic value"
    if dcf.upside is not None:
        title += f"  ({dcf.upside:+.0%})"
    ax.set_title(title)
    ax.set_ylabel("$ / share")
    _style(ax)
    return _fig_to_base64(fig)


def comparison_chart(results: list["ValuationResult"]) -> str:
    """Football-field: upside/downside vs. market price across tickers."""
    rows = [r for r in results if r.dcf and r.dcf.upside is not None]
    fig, ax = plt.subplots(figsize=(7, max(3, 0.6 * len(rows) + 1)))
    if not rows:
        ax.text(0.5, 0.5, "No comparable upside data", ha="center", va="center")
        ax.axis("off")
        return _fig_to_base64(fig)

    rows.sort(key=lambda r: r.dcf.upside)
    tickers = [r.ticker for r in rows]
    upsides = [r.dcf.upside for r in rows]
    colors = [_ACCENT2 if u >= 0 else _NEG for u in upsides]
    ax.barh(tickers, [u * 100 for u in upsides], color=colors, alpha=0.85)
    ax.axvline(0, color="#374151", linewidth=1)
    for i, u in enumerate(upsides):
        ax.text(u * 100, i, f" {u:+.0%}", va="center",
                ha="left" if u >= 0 else "right", fontsize=9)
    ax.set_xlabel("Upside / downside vs. market price (%)")
    ax.set_title("Intrinsic value vs. market — comparison")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return _fig_to_base64(fig)
