"""SEC EDGAR data access: ticker->CIK lookup and XBRL companyfacts extraction.

The companyfacts API returns every structured (XBRL) fact a company has ever
filed. XBRL has been mandatory since ~2009, so typically 10-15 years of annual
data are available. We extract annual (fiscal-year, form 10-K) series for the
concepts a DCF needs, with ordered tag fallbacks because filers tag differently.

Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import requests

from .cache import JsonCache

logger = logging.getLogger(__name__)

# EDGAR requires a descriptive User-Agent with a contact email; 403 otherwise.
# Email is the user's, per the session context.
USER_AGENT = "stock-valuation-tool lespinsj@gmail.com"

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Ordered fallback tag lists per logical concept. First tag with data wins.
CONCEPT_TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "OilAndGasRevenue",
    ],
    "ebit": ["OperatingIncomeLoss"],
    "dep_amort": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
        "PaymentsToAcquireProductiveAssets",
        # Oil & gas E&P firms book capex under activity-specific tags.
        "PaymentsToExploreAndDevelopOilAndGasProperties",
        "PaymentsToAcquireOilAndGasProperty",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "tax_expense": ["IncomeTaxExpenseBenefit"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "assets_current": ["AssetsCurrent"],
    "liabilities_current": ["LiabilitiesCurrent"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "short_term_investments": [
        "MarketableSecuritiesCurrent",
        "ShortTermInvestments",
        "AvailableForSaleSecuritiesCurrent",
        "OtherShortTermInvestments",
    ],
    "long_term_investments": [
        "MarketableSecuritiesNoncurrent",
        "LongTermInvestments",
        "AvailableForSaleSecuritiesNoncurrent",
    ],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",
    ],
    "long_term_debt_current": [
        "LongTermDebtCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
        "DebtCurrent",
    ],
    "interest_expense": ["InterestExpense", "InterestExpenseDebt"],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "CommonStockSharesIssued",
    ],
}

# IFRS (ifrs-full taxonomy) equivalents for foreign private issuers filing
# Form 20-F. Same concept keys as CONCEPT_TAGS so the rest of the pipeline is
# taxonomy-agnostic. IFRS tagging varies more by filer, hence broad fallbacks.
IFRS_CONCEPT_TAGS: dict[str, list[str]] = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers"],
    "ebit": [
        "ProfitLossFromOperatingActivities",
        "ProfitLossFromContinuingOperations",
    ],
    "dep_amort": [
        "DepreciationAndAmortisationExpense",
        "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",
        "DepreciationExpense",
    ],
    # Many IFRS filers don't tag a clean PP&E-purchase line; net cash used in
    # investing activities is a reliable, economically meaningful proxy and
    # avoids the inflated "additions to non-current assets" figure that service-
    # concession operators (e.g. airports under IFRIC 12) report.
    "capex": [
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "CashFlowsFromUsedInInvestingActivities",
        "AdditionsToNoncurrentAssets",
    ],
    "operating_cash_flow": ["CashFlowsFromUsedInOperatingActivities"],
    "tax_expense": ["IncomeTaxExpenseContinuingOperations"],
    "pretax_income": ["ProfitLossBeforeTax"],
    "assets_current": ["CurrentAssets"],
    "liabilities_current": ["CurrentLiabilities"],
    "cash": ["CashAndCashEquivalents"],
    "short_term_investments": ["OtherCurrentFinancialAssets", "CurrentInvestments"],
    "long_term_investments": ["OtherNoncurrentFinancialAssets", "NoncurrentInvestments"],
    "long_term_debt": ["LongtermBorrowings", "NoncurrentBorrowings", "Borrowings"],
    "long_term_debt_current": [
        "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
        "CurrentBorrowings",
    ],
    "interest_expense": [
        "FinanceCosts",
        "InterestExpenseOnBorrowings",
        "InterestExpense",
    ],
    "shares_outstanding": ["NumberOfSharesOutstanding"],
}

# Concepts represented as point-in-time balances (vs. duration/period flows).
_BALANCE_CONCEPTS = {
    "assets_current",
    "liabilities_current",
    "cash",
    "short_term_investments",
    "long_term_investments",
    "long_term_debt",
    "long_term_debt_current",
    "shares_outstanding",
}


@dataclass
class CompanyFinancials:
    """Annual historical financials extracted from EDGAR, plus metadata."""

    ticker: str
    cik: int
    company_name: str
    annual: pd.DataFrame  # index = fiscal year (int), columns = concept keys
    currency: str = "USD"  # reporting currency of the figures in `annual`
    notes: list[str] = field(default_factory=list)


class EdgarClient:
    def __init__(self, cache: Optional[JsonCache] = None) -> None:
        self.cache = cache or JsonCache()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    # -- low level fetches ------------------------------------------------
    def _get_json(self, url: str) -> dict:
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def lookup_cik(self, ticker: str) -> tuple[int, str]:
        """Resolve a ticker symbol to its zero-padded CIK and company name."""
        data = self.cache.get_or_fetch(
            "edgar:tickers", lambda: self._get_json(_TICKERS_URL)
        )
        ticker_u = ticker.upper()
        for row in data.values():
            if row.get("ticker", "").upper() == ticker_u:
                return int(row["cik_str"]), row.get("title", ticker_u)
        raise ValueError(
            f"{ticker_u} is not a SEC operating-company filer. It may be an ETF, "
            f"mutual fund, index, or non-US security — none of which file the "
            f"10-K/20-F financial statements a DCF requires. Enter an individual "
            f"stock instead."
        )

    def fetch_company_facts(self, cik: int) -> dict:
        url = _FACTS_URL.format(cik=cik)
        return self.cache.get_or_fetch(
            f"edgar:facts:{cik}", lambda: self._get_json(url)
        )

    def fetch_sic(self, cik: int) -> tuple[Optional[int], str]:
        """Return (SIC code, description) from the submissions API."""
        url = _SUBMISSIONS_URL.format(cik=cik)
        data = self.cache.get_or_fetch(
            f"edgar:submissions:{cik}", lambda: self._get_json(url)
        )
        sic_raw = data.get("sic")
        try:
            sic = int(sic_raw) if sic_raw not in (None, "") else None
        except (TypeError, ValueError):
            sic = None
        return sic, data.get("sicDescription", "")

    # -- extraction -------------------------------------------------------
    def get_financials(
        self, ticker: str, history_years: int = 10
    ) -> CompanyFinancials:
        cik, name = self.lookup_cik(ticker)

        # Banks, brokers and insurers (SIC 6000-6499) have no capex or operating
        # free cash flow in the usual sense; an FCFF DCF doesn't apply to them.
        sic, sic_desc = self.fetch_sic(cik)
        if sic is not None and 6000 <= sic <= 6499:
            raise ValueError(
                f"{ticker.upper()} ({name}) is a financial company "
                f"({sic_desc or 'SIC ' + str(sic)}). Banks, brokers and insurers "
                f"have no capex / operating free cash flow, so a discounted-FCF "
                f"valuation does not apply — use a book-value, excess-return or "
                f"dividend-based model instead."
            )

        facts = self.fetch_company_facts(cik)
        all_facts = facts.get("facts", {})
        us_gaap = all_facts.get("us-gaap", {})
        ifrs = all_facts.get("ifrs-full", {})

        notes: list[str] = []
        # US-GAAP (10-K) is preferred; fall back to IFRS (20-F/40-F) for foreign
        # private issuers.
        if us_gaap:
            facts_node, concept_map, allowed_forms = us_gaap, CONCEPT_TAGS, {"10-K"}
        elif ifrs:
            facts_node, concept_map, allowed_forms = ifrs, IFRS_CONCEPT_TAGS, {
                "20-F",
                "40-F",
            }
            notes.append("Foreign private issuer: parsed IFRS (Form 20-F) data")
        else:
            raise ValueError(
                f"{ticker.upper()} ({name}) has no us-gaap or ifrs-full XBRL data "
                f"in EDGAR; cannot value it."
            )

        series: dict[str, dict[int, float]] = {}
        currency_votes: dict[str, int] = {}
        for concept, tags in concept_map.items():
            chosen_tag, year_map, unit = self._extract_concept(
                facts_node, tags, concept, allowed_forms
            )
            if year_map:
                series[concept] = year_map
                if unit and unit not in ("shares", "pure"):
                    currency_votes[unit] = currency_votes.get(unit, 0) + 1
                if chosen_tag != tags[0]:
                    notes.append(f"{concept}: used fallback tag '{chosen_tag}'")
            else:
                notes.append(f"{concept}: no data found (tags tried: {', '.join(tags)})")

        currency = (
            max(currency_votes, key=currency_votes.get) if currency_votes else "USD"
        )
        if currency != "USD":
            notes.append(f"Figures reported in {currency}")

        df = pd.DataFrame(series).sort_index()
        if not df.empty and history_years:
            df = df.tail(history_years)

        return CompanyFinancials(
            ticker=ticker.upper(),
            cik=cik,
            company_name=name,
            annual=df,
            currency=currency,
            notes=notes,
        )

    @staticmethod
    def _extract_concept(
        facts_node: dict, tags: list[str], concept: str, allowed_forms: set
    ) -> tuple[Optional[str], dict[int, float], Optional[str]]:
        """Return (primary_tag, {fiscal_year: value}, unit) merged across the
        fallback tags.

        Picks annual figures: an allowed annual form (10-K / 20-F / 40-F) with
        fiscal period FY. Values are keyed by the *period-end calendar year*,
        NOT the XBRL ``fy`` field — older filings stamp every comparative period
        with the filing's fiscal year, which would collapse distinct years.
        Tags are merged in priority order (higher-priority tag wins a given
        year, lower-priority tags fill gaps), so a company that switched tags
        over time still gets a continuous series. Only tags sharing the primary
        unit are merged, to avoid mixing currencies.
        """
        is_balance = concept in _BALANCE_CONCEPTS
        combined: dict[int, float] = {}
        primary_tag: Optional[str] = None
        base_unit: Optional[str] = None

        for tag in tags:
            node = facts_node.get(tag)
            if not node:
                continue
            units = node.get("units", {})
            # Share counts use the 'shares' unit; else the first monetary unit
            # (USD for domestic filers, local currency for IFRS).
            if concept == "shares_outstanding" and "shares" in units:
                unit_key = "shares"
            else:
                unit_key = next((k for k in units if k not in ("pure",)), None)
            if unit_key is None:
                continue
            if base_unit is not None and unit_key != base_unit:
                continue  # don't merge a different currency into the series

            tag_map: dict[int, float] = {}
            for item in units[unit_key]:
                if item.get("form") not in allowed_forms or item.get("fp") != "FY":
                    continue
                end, val = item.get("end"), item.get("val")
                if end is None or val is None:
                    continue
                if not is_balance:
                    # Flow items: only full-year periods (~365 days).
                    start = item.get("start")
                    if start and (pd.Timestamp(end) - pd.Timestamp(start)).days < 300:
                        continue
                # Key by period-end year; later-filed values (restatements)
                # overwrite earlier ones within the same tag.
                tag_map[pd.Timestamp(end).year] = float(val)

            if tag_map:
                if primary_tag is None:
                    primary_tag, base_unit = tag, unit_key
                for year, val in tag_map.items():
                    combined.setdefault(year, val)  # higher-priority tag wins

        if not combined:
            return None, {}, None
        return primary_tag, combined, base_unit
