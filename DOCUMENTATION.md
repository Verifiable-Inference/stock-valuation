# Stock Valuation Tool — User Documentation

## What This Tool Does

This tool computes the **intrinsic (fair) value** of a stock using a Discounted Cash Flow (DCF) model. The core idea: a business is worth the sum of all the cash it will generate in the future, adjusted for the fact that a dollar received ten years from now is worth less than a dollar today. The tool pulls historical financials from SEC EDGAR, fetches live market data from Yahoo Finance, projects future cash flows, and discounts them back to a present value — producing a fair-value estimate per share that you can compare against the current market price.

**What it does not do:** predict stock prices, account for sentiment or momentum, or value financial companies (banks, insurers) — those require fundamentally different models.

---

## Pipeline (What Happens When You Run a Ticker)

1. **Data pull** — 10 years of annual 10-K filings from SEC EDGAR + live price, beta, market cap, and the 10-year Treasury yield from Yahoo Finance.
2. **FCF history** — Computes unlevered free cash flow for each historical year: `EBIT × (1 − tax rate) + D&A − CapEx − change in working capital`. Falls back to `Operating Cash Flow − CapEx` if EBIT is unavailable.
3. **WACC** — Builds the discount rate from first principles (CAPM cost of equity + after-tax cost of debt, weighted by capital structure).
4. **Projection** — Seeds a growth rate from the historical FCF CAGR, clamps it to a sane band, then linearly fades it to the terminal growth rate over the forecast horizon.
5. **DCF** — Discounts each projected year's FCF to present value, adds a terminal value (Gordon growth), subtracts debt, adds cash, divides by shares outstanding → intrinsic value per share.
6. **Sensitivity grid** — Re-runs the DCF across 25 combinations of WACC and terminal growth to show how the answer changes under different assumptions.
7. **Report** — Renders a self-contained HTML file with charts and all intermediate figures.

---

## Assumptions

Every assumption below has a default defined in `config.yaml`. All of them can be overridden on the command line or per-ticker in the `overrides` block.

---

### 1. Projection Years (`projection_years`)
**Default: 5**

The number of years for which cash flows are projected explicitly before collapsing into a terminal value. Five years is the standard in equity research — short enough that projections stay somewhat grounded in current business trends, long enough to capture a meaningful fraction of value before the terminal value takes over. Longer horizons (7–10 years) are appropriate for companies with high but durable growth where the story plays out slowly (e.g., a biotech with a long drug-approval runway). Shorter horizons (3 years) make sense when visibility is poor.

---

### 2. History Years (`history_years`)
**Default: 10**

How many years of annual filings to pull from EDGAR when computing historical trends. Ten years captures at least one full economic cycle, which is important for computing a stable FCF CAGR and tax rate. SEC XBRL data typically goes back to 2009, so 10 years is effectively the maximum for most companies.

---

### 3. Base FCF Years (`base_fcf_years`)
**Default: 3**

The trailing average window used to set the starting point for projections. Rather than anchoring the model on a single year's FCF — which can be distorted by a lumpy CapEx cycle, a one-time working-capital swing, or an acquisition — the tool averages the last 3 years to get a smoother base. Setting this to 1 uses only the most recent year, which is appropriate if you believe the latest year is the best representation of run-rate earnings.

---

### 4. Base FCF Method (`base_fcf_method`)
**Default: `trailing`**

Controls how the starting FCF is estimated. Two options:

- **`trailing`** (default): Simple 3-year trailing average of actual FCF. Works well for stable, predictable businesses.
- **`normalized`**: Designed for **cyclical companies** (semiconductor memory, commodities, oil & gas). It computes the average FCF margin (FCF ÷ Revenue) across the full history — capturing mid-cycle profitability rather than a boom or bust year — then applies that margin to the most recent revenue figure. This prevents the model from projecting a peak or trough year into perpetuity. Use `--base-fcf-method normalized` or flag the ticker in `config.yaml` for cyclical names like MU, FCX, or CVX.

---

### 5. Terminal Growth Rate (`terminal_growth`)
**Default: 2.5%**

The assumed perpetual growth rate of free cash flow once the explicit forecast period ends. This is the single most impactful assumption in any DCF. The default of 2.5% approximates long-run nominal GDP growth (roughly 2% real + 2.5% inflation target ≈ 4% nominal, but the terminal growth rate is applied to FCF, not revenue, and FCF tends to grow more slowly than GDP for mature companies). It is also just below the long-run historical US nominal GDP growth of ~4–5%, providing a conservative anchor.

**Rule of thumb:** No company can grow faster than the overall economy forever — if terminal growth exceeds GDP growth, the company would eventually become larger than the economy. Keep this between 1.5% and 3.5% for most businesses. Higher values may be reasonable for companies in secular growth industries with strong moats.

**Guardrail:** WACC must exceed terminal growth or the Gordon terminal value becomes infinite/undefined. The tool will refuse to run if this constraint is violated.

---

### 6. Equity Risk Premium (`equity_risk_premium`)
**Default: 5.0%**

The additional return investors demand for holding equities versus a risk-free Treasury bond (i.e., the equity market return minus the risk-free rate). Used in the CAPM formula: `Cost of Equity = Risk-free rate + Beta × ERP`.

The 5% default is the long-run historical equity risk premium for US equities, consistent with Damodaran's widely-used estimates. It is conservative relative to current implied ERP (which fluctuates with market valuations), but robust over longer holding periods. Raising this to 5.5–6% increases the cost of equity and reduces fair value; lowering it to 4% does the opposite.

---

### 7. Risk-Free Rate (`risk_free_override`)
**Default: auto-derived from the 10-year US Treasury yield via Yahoo Finance (`^TNX`)**

The return on a theoretically risk-free investment, used as the baseline in CAPM. The tool pulls the current 10-year Treasury yield live at run time, so it reflects prevailing interest rates without any manual input. You can override this with `--risk-free 0.043` (e.g., to freeze a specific rate for comparability across runs). If the live fetch fails, the tool falls back to 4.0% and logs a warning.

---

### 8. Tax Rate (`tax_rate_override`)
**Default: auto-computed from filings; fallback 21%**

The effective corporate tax rate applied to EBIT to convert operating income to after-tax earnings (NOPAT). The tool computes this from the actual `Income Tax Expense ÷ Pretax Income` ratio across all profitable historical years, averaging them to smooth year-to-year volatility. This is more accurate than the statutory 21% US corporate rate because it reflects actual deductions, credits, and international tax structures. The 21% fallback applies when tax data is missing from filings.

You can override with `--tax-rate 0.15` for companies with structural tax advantages (e.g., heavy R&D credits, IP in low-tax jurisdictions).

---

### 9. Beta (`beta_override`)
**Default: auto-derived from Yahoo Finance (5-year monthly regression vs. S&P 500)**

Beta measures how much a stock moves relative to the overall market. A beta of 1.0 means it moves in line with the market; 1.5 means 50% more volatile. Beta feeds directly into the CAPM cost of equity: higher beta → higher cost of equity → higher WACC → lower intrinsic value.

The tool fetches the 5-year monthly beta from Yahoo Finance, which is the most common convention in equity research. Override with `--beta 1.2` when you believe the historical beta is unrepresentative (e.g., the company recently underwent a major restructuring, or you want to use an industry-average unlevered beta).

**Fallback: 1.0** (market-average) when data is unavailable.

---

### 10. WACC Override (`wacc_override`)
**Default: null (auto-computed)**

Bypasses the entire CAPM-based WACC calculation and uses a fixed discount rate instead. Use sparingly — the auto-computed WACC is grounded in observed data. The main reason to override is when you want to match a specific analyst's model or apply an industry-standard discount rate for comparability. Example: `--wacc 0.09` forces a 9% discount rate.

---

### 11. Growth Override (`growth_override`)
**Default: null (derived from historical FCF CAGR)**

Forces a specific first-year FCF growth rate, bypassing the historical CAGR calculation. Useful when the historical CAGR is an unreliable signal — for example, if the company has recently undergone a large acquisition that makes FCF history non-comparable, or if consensus analyst estimates are meaningfully different from the historical trend. Example: `--growth 0.10` seeds the model at 10% growth, fading to terminal growth over the forecast horizon.

---

### 12. Growth Guardrails (`max_growth` / `min_growth`)
**Defaults: max 15%, min −5%**

Hard caps applied to the auto-derived initial FCF growth rate. No matter what the historical CAGR implies, the model will not project growth above 15% or below −5% per year.

- **15% ceiling**: Prevents a company that happened to have a great run from being projected at an unsustainable pace indefinitely. Even elite compounders rarely sustain >15% FCF growth long enough for it to be the right base assumption.
- **−5% floor**: Prevents a temporary FCF dip (a heavy investment year, a recession) from projecting permanent decline. If the business is genuinely in structural decline, override growth manually.

---

### 13. Fallback Tax Rate (`fallback_tax_rate`)
**Default: 21%**

The US statutory corporate income tax rate (set by the Tax Cuts and Jobs Act of 2017). Applied only when tax expense and pretax income are both missing from EDGAR filings, which is rare for US-listed companies.

---

### 14. Fallback Cost of Debt (`fallback_cost_of_debt`)
**Default: 5%**

The pre-tax cost of debt assumed when interest expense is absent from filings (e.g., the company has minimal debt and doesn't separately disclose interest). 5% approximates a solid investment-grade borrowing rate in a normal interest rate environment. After the 21% tax shield, the after-tax cost is ~4%.

---

### 15. Fallback Beta (`fallback_beta`)
**Default: 1.0**

The beta assumed when market data is unavailable. A beta of 1.0 (market-average risk) is the most neutral assumption possible and avoids artificially inflating or deflating the cost of equity in the absence of data.

---

## Charts

The report includes up to six charts. Here is what each one shows and how to read it.

---

### Chart 1: Historical Revenue & Free Cash Flow

**What it shows:** A bar chart of annual revenue (blue bars) with free cash flow plotted as a line (green dots) over the last 10 years — both in billions of dollars.

**Executive read:** This is the business's track record. Revenue tells you how fast the company has been growing its top line. The FCF line tells you how much of that revenue has translated into actual cash for shareholders after all operating costs, taxes, and reinvestment. A widening gap between revenue and FCF (FCF growing faster) signals improving capital efficiency. A flat or declining FCF line against growing revenue signals the company is consuming cash to grow. This chart is the foundation: the model's projections are grounded in this history.

---

### Chart 2: Projected Free Cash Flow vs. Present Value

**What it shows:** Two overlapping bar charts for each forecast year. The taller blue bars are the raw projected FCF in each year (in $B). The narrower green bars are what those same cash flows are worth **today** — i.e., discounted back to the present using WACC.

**Executive read:** This makes the time-value of money visible. A dollar of FCF in Year 5 is worth less than a dollar today because of risk and opportunity cost — that's what WACC captures. The shrinking green bars show you exactly how much discounting erodes the value of distant cash flows. If the green bars in Year 4 and Year 5 are very small relative to Year 1, it tells you the valuation is dominated by near-term cash flows and the terminal value, not the mid-period growth story.

---

### Chart 3: Enterprise Value → Equity Value Bridge

**What it shows:** A waterfall chart with four columns: Enterprise Value → minus Debt → plus Cash & Investments → Equity Value. All figures in $B.

**Executive read:** This chart answers the question "who owns what?" The Enterprise Value is what the entire business is worth (as if you bought it free of any financing). But shareholders don't own the whole business free and clear — the debt holders have a prior claim. Subtracting debt and adding back cash gives the **equity value**: what's left for shareholders. Dividing that by shares outstanding gives the per-share intrinsic value. This chart makes it transparent whether a large spread between enterprise value and equity value is due to a heavily leveraged balance sheet or simply a large cash hoard.

---

### Chart 4: Sensitivity Analysis (WACC × Terminal Growth Heatmap)

**What it shows:** A 5×5 grid where each cell is an intrinsic value per share. The Y-axis varies WACC (±2% around the base case in 1% steps). The X-axis varies the terminal growth rate (±1% around the base case in 0.5% steps). Green cells are higher values; red cells are lower values.

**Executive read:** No DCF assumption is known with certainty. This chart is the honest answer to "how confident are you?" Rather than presenting one number as the truth, it shows the full range of outcomes across the most consequential assumptions. The cell in the center is the base-case estimate. Reading across the chart: if the current stock price sits comfortably in the green zone even in the top-left (high WACC, low growth) corner, the stock looks cheap under almost any reasonable assumption. If the price only looks attractive in the bottom-right (low WACC, high growth) corner, the bull case depends on everything going right. A smart use of this chart is to mark where the current market price falls on the color scale — that tells you what assumptions the market is implicitly pricing in.

---

### Chart 5: Price vs. Intrinsic Value

**What it shows:** Two bars side by side: the current market price per share (gray) and the model's intrinsic value per share (green if undervalued, red if overvalued). The percentage upside or downside is shown in the chart title.

**Executive read:** This is the bottom line. The model says the stock is worth $X; the market is paying $Y. The spread is the implied margin of safety (or premium). A green bar taller than the gray means the model sees an undervalued stock; a red bar shorter than the gray means the market is pricing in more optimism than the model's assumptions justify. This number should never be read in isolation — always cross-reference with the sensitivity heatmap to understand how robust the conclusion is.

---

### Chart 6: Comparison Chart (Multi-ticker runs only)

**What it shows:** A horizontal bar chart ranking all valued tickers by implied upside/downside vs. their current market price. Bars to the right (green) are undervalued by the model; bars to the left (red) are overvalued. Tickers that couldn't be valued (FCF-negative, financial companies, insufficient data) are listed separately.

**Executive read:** A relative ranking tool. It answers "across the stocks I'm considering, which ones does this model flag as most attractively priced relative to their intrinsic value?" This is useful for portfolio construction or capital allocation decisions. Important caveat: stocks with very high upside often have it because they are higher-risk or more volatile (and thus carry a higher WACC), not because they are necessarily better businesses. Always read the upside figure alongside the individual company's WACC and projection assumptions.

---

## Limitations and When to Be Cautious

- **FCF-negative companies** cannot be valued with this model (early-stage, deeply cyclical, or distressed businesses). The tool will explicitly say so.
- **Financial companies** (banks, insurers, brokers — SIC codes 6000–6499) are excluded because they have no meaningful distinction between operating and financing cash flows.
- **Foreign companies** are supported via IFRS filings (20-F), but IFRS tagging is less standardized and the data may be noisier. The tool converts all figures to USD using the current spot FX rate — a deliberate simplification that preserves growth rates.
- **The terminal value typically represents 60–80% of total enterprise value** in a 5-year DCF. This means the terminal growth rate dominates the output. Small changes in terminal growth have large effects on fair value — use the sensitivity chart to understand this exposure.
- **This is educational, not investment advice.** Market prices reflect information (competition, regulatory risk, management quality, macro) that a mechanical DCF cannot fully capture.
