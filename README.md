# Stock Valuation — DCF Tool

Compute a **Discounted Cash Flow (DCF)** intrinsic value for one or more stocks,
using **SEC EDGAR** for historical fundamentals and **Yahoo Finance** for market
data, and render a **self-contained HTML report** (with charts) per stock plus a
cross-stock comparison.

Implements the present-value formula:

```
PV = Σ FCF_t / (1+r)^t  +  TV / (1+r)^n
```

where `r` = WACC, `n` = forecast horizon, `TV` = Gordon-growth terminal value.

## Install

```bash
cd stock_valuation
python -m venv .venv && source .venv/bin/activate
pip install -e .          # or: pip install -e ".[dev]" for tests
```

## Web interface

```bash
python -m stock_valuation.webapp        # then open http://127.0.0.1:5000
```

Enter one or more tickers, optionally expand **Assumptions** to adjust the
forecast horizon, terminal growth, ERP, WACC, or base-FCF method, then hit
**Get valuation**. The results page has a tab menu to view each stock's full
report and the comparison; tickers that can't be valued (e.g. FCF-negative)
are listed with the reason.

## Command line

```bash
# Single stock
python -m stock_valuation AAPL

# Multiple stocks -> per-stock reports + comparison.html
python -m stock_valuation AAPL MSFT NVDA --out reports/

# Override assumptions inline
python -m stock_valuation NVDA --proj-years 7 --terminal-growth 0.03 --wacc 0.09

# Cyclical names (memory, commodities): normalize the base on a full-cycle margin
python -m stock_valuation MU --base-fcf-method normalized

# Force a fresh data pull
python -m stock_valuation AAPL --no-cache -v
```

Reports are written to `--out` (default `reports/`): `AAPL_dcf.html`, …, and
`comparison.html` for multi-ticker runs. Open them in any browser — no server.

## How it works

1. **Data** — `data/edgar.py` resolves the ticker→CIK and pulls the `companyfacts`
   XBRL API (annual 10-K figures, ~10 yrs back to 2009), with ordered tag
   fallbacks. `data/market.py` pulls price, shares, beta, market cap from
   yfinance and the 10-yr Treasury (`^TNX`) as the risk-free rate. Responses are
   cached under `~/.cache/stock_valuation`.
2. **Model** —
   - `model/fcf.py`: unlevered FCF = `EBIT(1−tax) + D&A − CapEx − ΔNWC` (falls
     back to `OCF − CapEx`).
   - `model/wacc.py`: CAPM cost of equity + after-tax cost of debt, weighted by
     market cap and debt.
   - `model/projections.py`: base FCF via a trailing average (default) or, for
     cyclicals, a **normalized** mid-cycle margin (`Σ FCF / Σ revenue × latest
     revenue`, grown at the revenue CAGR); growth decayed toward terminal
     growth; Gordon terminal value.
   - `model/dcf.py`: discounts FCF + TV to enterprise value, bridges to equity
     (`− debt + cash`), divides by shares, and builds a WACC × growth
     sensitivity grid.
3. **Report** — `report/charts.py` (matplotlib → base64 PNG) + Jinja2 templates
   produce the HTML.

## Assumptions & overrides

Defaults live in `config.yaml` (`defaults` block), with optional per-ticker
`overrides`. Every value is also settable on the CLI (e.g. `--terminal-growth`,
`--erp`, `--risk-free`, `--tax-rate`, `--beta`, `--wacc`, `--growth`). Precedence:
packaged defaults < config defaults < per-ticker overrides < CLI flags.

## Tests

```bash
pytest
```

## Disclaimer

Educational use only — **not investment advice**. yfinance is unofficial; verify
figures before relying on them.
