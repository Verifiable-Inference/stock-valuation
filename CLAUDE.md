# stock_valuation — CLAUDE.md

## What this is

A Discounted Cash Flow (DCF) intrinsic-value tool. Pulls 10-K / 20-F financials from SEC EDGAR and live market data from Yahoo Finance, projects FCF, discounts at WACC, and renders self-contained HTML reports. Also has a Flask web interface.

Formula: `PV = Σ FCF_t/(1+r)^t + TV/(1+r)^n` where `r = WACC`, `TV` = Gordon-growth terminal value.

## Running

```bash
source .venv/bin/activate
python -m stock_valuation AAPL                   # CLI, single ticker
python -m stock_valuation AAPL MSFT --out reports/  # multi-ticker + comparison.html
python -m stock_valuation.webapp                 # web UI at http://127.0.0.1:5000
pytest                                           # 12 unit tests, all pure (no network)
```

## Architecture

```
stock_valuation/
  config.py          # Assumptions dataclass + YAML loader + precedence merge
  valuation.py       # Pipeline orchestrator: calls data → model → report in order
  cli.py             # argparse entry point
  webapp.py          # Flask app; in-memory _RUNS dict keyed by run_id hex
  data/
    edgar.py         # EdgarClient: ticker→CIK, companyfacts XBRL extraction
    market.py        # yfinance: price, shares, beta, market cap, ^TNX risk-free rate
    cache.py         # JsonCache: on-disk TTL cache at ~/.cache/stock_valuation/
  model/
    fcf.py           # FCFF = EBIT*(1-tax) + D&A - CapEx - ΔNWC; falls back to OCF-CapEx
    wacc.py          # CAPM cost of equity + after-tax cost of debt, market-cap weighted
    projections.py   # Base FCF + growth decay + Gordon terminal value
    dcf.py           # Discounting, equity bridge, sensitivity grid (5×5)
  report/
    charts.py        # matplotlib → base64 PNG
    render.py        # Jinja2 → HTML (report and comparison)
    templates/       # *.j2 templates
  web/
    templates/       # index.html, results.html, docs.html
```

## Assumption precedence (lowest → highest)

`Assumptions` dataclass defaults → `config.yaml` defaults → per-ticker `config.yaml` overrides → CLI flags / form fields

## Key design decisions

**FCF method priority:** FCFF (EBIT-based) preferred; silently falls back to OCF − CapEx when EBIT is absent. `FcfHistory.method` records which path was taken.

**NWC calculation:** Excludes cash and current-portion debt from operating NWC to avoid non-operating swings inflating ΔNWC.

**Cash add-back:** Only cash + *current* marketable securities. Noncurrent investments are deliberately excluded (may be illiquid / equity-method stakes). Tracked in `WaccResult.noncurrent_investments` for transparency.

**Growth decay:** Initial growth fades linearly to `terminal_growth` over the projection horizon — the firm doesn't grow at its recent rate forever.

**Normalized base (cyclicals):** `base_fcf_method = "normalized"` uses dollar-weighted FCF margin across full history × latest revenue, then grows at revenue CAGR instead of noisy FCF CAGR. Use for MU, FCX, CVX, etc.

**Financial company exclusion:** SIC 6000–6499 → hard error. Banks/insurers have no operating FCF in the FCFF sense.

**EDGAR tag fallbacks:** `CONCEPT_TAGS` (US-GAAP) and `IFRS_CONCEPT_TAGS` list ordered fallback XBRL tags per concept. Higher-priority tag wins a given year; lower-priority tags fill gaps, enabling continuous series for companies that changed tags.

**Balance vs. flow items:** `_BALANCE_CONCEPTS` — point-in-time items (cash, debt, NWC) use the latest fiscal-year-end value; flow items filter for periods ≥ 300 days to exclude partial-year restatements.

**Period-end year key:** EDGAR XBRL items are keyed by `period.end` calendar year, not the `fy` field — older filings stamp all comparative periods with the filing year, which collapses distinct years.

## Guardrails

- `WACC > terminal_growth` is enforced before running DCF (Gordon TV undefined otherwise).
- `base_fcf > 0` required to run; else raises with clear message.
- `equity_value > 0` required; else raises (debt > EV + cash case).
- Growth auto-derived from FCF CAGR is clamped to `[min_growth, max_growth]` (defaults −5% / +15%).
- Market cap missing → WACC approximated as cost of equity (prevents collapsing to after-tax debt rate).

## Data sources

- **SEC EDGAR** — `companyfacts` XBRL API and `company_tickers.json`. User-Agent header required (set to `stock-valuation-tool lespinsj@gmail.com`).
- **Yahoo Finance** (`yfinance`) — price, shares, beta, market cap, `^TNX` as risk-free rate proxy. Unofficial; degrades gracefully.
- **Cache** — `~/.cache/stock_valuation/`, 24h TTL, SHA-256-keyed JSON files. `--no-cache` bypasses it.

## Tests

All 12 tests in `tests/test_dcf.py` are pure unit tests (no network). They cover: terminal value math, DCF discounting, equity bridge, FCFF assembly, OCF−CapEx fallback, WACC CAPM formula, weight edge cases (no market cap), cash add-back exclusions, growth decay, normalized base for cyclicals, and sensitivity grid NaN handling.

Run with: `pytest` (from venv).

## Adding a new CLI flag

1. Add to `Assumptions` dataclass in `config.py`.
2. Add to `_build_parser()` in `cli.py`.
3. Add the key to `_cli_overrides()` in `cli.py`.
4. Add the form field to `webapp.py` (`_PCT_FIELDS` or `_INT_FIELDS`), and to `web/templates/index.html`.
5. Wire it into the relevant model call in `valuation.py`.
