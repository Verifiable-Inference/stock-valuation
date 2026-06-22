"""Command-line entry point for the DCF valuation tool.

Examples:
    python -m stock_valuation AAPL
    python -m stock_valuation AAPL MSFT --proj-years 7 --out reports/
    python -m stock_valuation NVDA --terminal-growth 0.03 --wacc 0.09 --no-cache
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import sys

from .config import build_assumptions, load_config
from .data.cache import JsonCache
from .data.edgar import EdgarClient
from .report.render import render_comparison, render_report
from .valuation import ValuationResult, value_stock

logger = logging.getLogger("stock_valuation")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stock-valuation",
        description="Discounted Cash Flow valuation from SEC EDGAR + Yahoo Finance.",
    )
    p.add_argument("tickers", nargs="+", help="One or more ticker symbols (e.g. AAPL MSFT)")
    p.add_argument("--out", default="reports", help="Output directory for HTML reports")
    p.add_argument("--config", default=None, help="Path to config.yaml")
    p.add_argument("--no-cache", action="store_true", help="Bypass the on-disk API cache")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    # Inline assumption overrides (take precedence over config).
    p.add_argument("--proj-years", type=int, dest="projection_years")
    p.add_argument("--history-years", type=int, dest="history_years")
    p.add_argument("--base-fcf-years", type=int, dest="base_fcf_years")
    p.add_argument(
        "--base-fcf-method", choices=["trailing", "normalized"],
        dest="base_fcf_method",
        help="Base FCF estimation: 'trailing' avg (default) or 'normalized' "
             "mid-cycle margin for cyclical names",
    )
    p.add_argument("--terminal-growth", type=float, dest="terminal_growth")
    p.add_argument("--erp", type=float, dest="equity_risk_premium")
    p.add_argument("--risk-free", type=float, dest="risk_free_override")
    p.add_argument("--tax-rate", type=float, dest="tax_rate_override")
    p.add_argument("--beta", type=float, dest="beta_override")
    p.add_argument("--wacc", type=float, dest="wacc_override")
    p.add_argument("--growth", type=float, dest="growth_override")
    return p


def _cli_overrides(args: argparse.Namespace) -> dict:
    keys = [
        "projection_years", "history_years", "base_fcf_years", "base_fcf_method",
        "terminal_growth", "equity_risk_premium",
        "risk_free_override", "tax_rate_override", "beta_override", "wacc_override",
        "growth_override",
    ]
    return {k: getattr(args, k) for k in keys if getattr(args, k) is not None}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    overrides = _cli_overrides(args)
    cache = JsonCache(enabled=not args.no_cache)
    edgar = EdgarClient(cache=cache)
    generated_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    results: list[ValuationResult] = []
    errors: list[dict] = []

    for ticker in args.tickers:
        try:
            assumptions = build_assumptions(ticker, config, overrides)
            logger.info("Valuing %s ...", ticker)
            result = value_stock(ticker, assumptions, edgar=edgar)
            path = render_report(result, args.out, generated_at)
            results.append(result)
            up = result.dcf.upside
            up_str = f" ({up:+.0%})" if up is not None else ""
            print(
                f"{ticker.upper():6s} intrinsic ${result.dcf.intrinsic_per_share:,.2f}"
                f"{up_str}  ->  {path}"
            )
        except Exception as exc:  # noqa: BLE001 - report per-ticker, keep going
            errors.append({"ticker": ticker.upper(), "error": str(exc)})
            print(f"{ticker.upper():6s} ERROR: {exc}", file=sys.stderr)

    if len(args.tickers) > 1 and (results or errors):
        comp_path = render_comparison(results, errors, args.out, generated_at)
        print(f"\nComparison report -> {comp_path}")

    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
