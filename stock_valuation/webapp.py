"""Flask web interface for the DCF valuation tool.

Run:  python -m stock_valuation.webapp   (then open http://127.0.0.1:5000)

Enter one or more tickers and adjust assumptions in the form, then view each
stock's full report (and the comparison) from a tab menu on the results page.
Valuations are computed once per submission and cached in memory by run id.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import uuid
from typing import Any

from flask import Flask, abort, redirect, render_template, request, url_for

from .config import build_assumptions, load_config
from .data.cache import JsonCache
from .data.edgar import EdgarClient
from .report.render import comparison_html, comparison_rows, report_html
from .valuation import value_stock

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="web/templates")

# In-memory store of computed runs: run_id -> run dict. Fine for a local,
# single-user tool; swap for a real store if this is ever deployed widely.
_RUNS: dict[str, dict[str, Any]] = {}

_TICKER_RE = re.compile(r"[A-Za-z][A-Za-z.\-]{0,9}")

# Form field -> (assumption key, scale). Percentage fields are divided by 100.
_PCT_FIELDS = {
    "terminal_growth": "terminal_growth",
    "equity_risk_premium": "equity_risk_premium",
    "wacc_override": "wacc_override",
    "growth_override": "growth_override",
}
_INT_FIELDS = {"projection_years": "projection_years", "history_years": "history_years"}


def _parse_tickers(raw: str) -> list[str]:
    """Extract uppercased ticker symbols from free-form input (commas/spaces)."""
    seen: dict[str, None] = {}
    for match in _TICKER_RE.findall(raw or ""):
        seen.setdefault(match.upper(), None)
    return list(seen.keys())


def _parse_overrides(form) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for field, key in _INT_FIELDS.items():
        val = (form.get(field) or "").strip()
        if val:
            try:
                overrides[key] = int(val)
            except ValueError:
                pass
    for field, key in _PCT_FIELDS.items():
        val = (form.get(field) or "").strip()
        if val:
            try:
                overrides[key] = float(val) / 100.0
            except ValueError:
                pass
    method = (form.get("base_fcf_method") or "").strip()
    if method in {"trailing", "normalized"}:
        overrides["base_fcf_method"] = method
    return overrides


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/docs")
def docs():
    return render_template("docs.html")


@app.route("/value", methods=["POST"])
def value():
    tickers = _parse_tickers(request.form.get("tickers", ""))
    if not tickers:
        return render_template("index.html", error="Enter at least one ticker symbol.")

    overrides = _parse_overrides(request.form)
    config = load_config()
    edgar = EdgarClient(cache=JsonCache(enabled=True))
    generated_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    results, errors, reports = [], [], {}
    for ticker in tickers[:20]:  # sanity cap
        try:
            assumptions = build_assumptions(ticker, config, overrides)
            result = value_stock(ticker, assumptions, edgar=edgar)
            results.append(result)
            reports[ticker] = report_html(result, generated_at)
        except Exception as exc:  # noqa: BLE001 - report per ticker, continue
            logger.warning("Valuation failed for %s: %s", ticker, exc)
            errors.append({"ticker": ticker, "error": str(exc)})

    run_id = uuid.uuid4().hex[:12]
    _RUNS[run_id] = {
        "generated_at": generated_at,
        "reports": reports,
        "summary": comparison_rows(results),
        "errors": errors,
        "comparison": comparison_html(results, errors, generated_at)
        if len(results) > 1
        else None,
    }
    return redirect(url_for("results", run_id=run_id))


@app.route("/results/<run_id>")
def results(run_id: str):
    run = _RUNS.get(run_id)
    if not run:
        abort(404)
    return render_template(
        "results.html",
        run_id=run_id,
        summary=run["summary"],
        errors=run["errors"],
        has_comparison=run["comparison"] is not None,
        generated_at=run["generated_at"],
    )


@app.route("/report/<run_id>/<ticker>")
def report(run_id: str, ticker: str):
    run = _RUNS.get(run_id)
    if not run:
        abort(404)
    if ticker == "_comparison":
        if not run["comparison"]:
            abort(404)
        return run["comparison"]
    html = run["reports"].get(ticker.upper())
    if html is None:
        abort(404)
    return html


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
