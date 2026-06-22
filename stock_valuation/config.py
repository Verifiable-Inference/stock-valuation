"""Load default assumptions and merge CLI / per-ticker overrides."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field, fields
from typing import Any, Optional

import yaml

_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"
)


@dataclass
class Assumptions:
    """All knobs that drive a single DCF run.

    Auto-derivable fields are left as ``None`` when not forced; the model fills
    them in from data. Fields with concrete defaults are guardrails/fallbacks.
    """

    projection_years: int = 5
    history_years: int = 10
    base_fcf_years: int = 3
    base_fcf_method: str = "trailing"   # "trailing" | "normalized" (cyclicals)
    terminal_growth: float = 0.025
    equity_risk_premium: float = 0.05

    risk_free_override: Optional[float] = None
    tax_rate_override: Optional[float] = None
    beta_override: Optional[float] = None
    wacc_override: Optional[float] = None
    growth_override: Optional[float] = None

    max_growth: float = 0.15
    min_growth: float = -0.05

    fallback_tax_rate: float = 0.21
    fallback_cost_of_debt: float = 0.05
    fallback_beta: float = 1.0

    @classmethod
    def _field_names(cls) -> set[str]:
        return {f.name for f in fields(cls)}

    def merged(self, overrides: dict[str, Any]) -> "Assumptions":
        """Return a copy with ``overrides`` applied (ignoring unknown / None keys)."""
        valid = self._field_names()
        data = copy.copy(self)
        for key, value in overrides.items():
            if key in valid and value is not None:
                setattr(data, key, value)
        return data


def load_config(path: Optional[str] = None) -> dict[str, Any]:
    """Load the YAML config file. Falls back to packaged defaults if missing."""
    path = path or _DEFAULT_CONFIG_PATH
    if not os.path.exists(path):
        return {"defaults": {}, "overrides": {}}
    with open(path, "r") as fh:
        raw = yaml.safe_load(fh) or {}
    raw.setdefault("defaults", {})
    raw.setdefault("overrides", {})
    return raw


def build_assumptions(
    ticker: str,
    config: dict[str, Any],
    cli_overrides: Optional[dict[str, Any]] = None,
) -> Assumptions:
    """Resolve assumptions for ``ticker`` with precedence:

    packaged defaults < config ``defaults`` < per-ticker ``overrides`` < CLI flags.
    """
    base = Assumptions()
    base = base.merged(config.get("defaults", {}))

    per_ticker = config.get("overrides", {}).get(ticker.upper(), {})
    base = base.merged(per_ticker)

    if cli_overrides:
        base = base.merged(cli_overrides)
    return base
