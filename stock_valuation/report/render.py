"""Render valuation results to self-contained HTML, as strings or files."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import charts

if TYPE_CHECKING:
    from ..valuation import ValuationResult

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )


def report_filename(ticker: str) -> str:
    return f"{ticker.upper()}_dcf.html"


def report_html(result: "ValuationResult", generated_at: str) -> str:
    """Render a single-stock report to a self-contained HTML string."""
    chart_imgs = {
        "history": charts.history_chart(result),
        "projection": charts.projection_chart(result),
        "bridge": charts.bridge_chart(result),
        "price": charts.price_vs_value_chart(result),
        "sensitivity": charts.sensitivity_chart(result),
    }
    tbl = result.fcf_history.table
    fcf_rows = [
        {
            "year": int(year),
            "fcff": row["fcff"] if "fcff" in row.index else float("nan"),
            "nopat": row.get("nopat", float("nan")),
            "dep_amort": row.get("dep_amort", float("nan")),
            "capex": row.get("capex", float("nan")),
            "delta_nwc": row.get("delta_nwc", float("nan")),
        }
        for year, row in tbl.iterrows()
    ]
    return _env().get_template("report.html.j2").render(
        ticker=result.ticker,
        company_name=result.financials.company_name,
        generated_at=generated_at,
        dcf=result.dcf,
        wacc=result.wacc,
        projection=result.projection,
        fcf_history=result.fcf_history,
        fcf_rows=fcf_rows,
        assumptions=result.assumptions,
        charts=chart_imgs,
        notes=result.all_notes(),
    )


def comparison_rows(results: list["ValuationResult"]) -> list[dict]:
    ok = [r for r in results if r.dcf]
    rows = []
    for r in sorted(ok, key=lambda r: (r.dcf.upside is None, -(r.dcf.upside or 0))):
        rows.append(
            {
                "ticker": r.ticker,
                "intrinsic": r.dcf.intrinsic_per_share,
                "price": r.dcf.current_price,
                "upside": r.dcf.upside,
                "wacc": r.dcf.wacc,
                "initial_growth": r.projection.initial_growth,
                "filename": report_filename(r.ticker),
            }
        )
    return rows


def comparison_html(
    results: list["ValuationResult"], errors: list[dict], generated_at: str
) -> str:
    """Render the cross-stock comparison report to an HTML string."""
    ok = [r for r in results if r.dcf]
    return _env().get_template("comparison.html.j2").render(
        rows=comparison_rows(results),
        errors=errors,
        generated_at=generated_at,
        comparison_chart=charts.comparison_chart(ok),
    )


def render_report(result: "ValuationResult", out_dir: str, generated_at: str) -> str:
    """Render a single-stock report to a file; return the written path."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, report_filename(result.ticker))
    with open(path, "w") as fh:
        fh.write(report_html(result, generated_at))
    return path


def render_comparison(
    results: list["ValuationResult"],
    errors: list[dict],
    out_dir: str,
    generated_at: str,
) -> str:
    """Render the cross-stock comparison report to a file; return the path."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "comparison.html")
    with open(path, "w") as fh:
        fh.write(comparison_html(results, errors, generated_at))
    return path
