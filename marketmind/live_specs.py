"""Helpers that connect file-driven stock specs to the live paper runner."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from marketmind import contest
from marketmind.stock_specs.loader import (
    StockSpec,
    default_stocks_dir,
    load_stock_specs,
    specs_by_ticker,
)

logger = logging.getLogger(__name__)


def load_live_specs(
    directory: Optional[Path | str] = None,
) -> Tuple[List[StockSpec], Dict[str, StockSpec]]:
    """Load enabled stock specs; empty list when the folder is missing."""
    root = Path(directory) if directory is not None else default_stocks_dir()
    if not root.is_dir():
        logger.warning("Stock specs folder missing at %s — using instruments table",
                       root)
        return [], {}
    try:
        specs = load_stock_specs(root, enabled_only=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load stock specs from %s: %s", root, exc)
        return [], {}
    return specs, specs_by_ticker(specs)


def trading_symbols(
    specs: Sequence[StockSpec],
    fallback: Sequence[str],
) -> List[str]:
    """Prefer enabled stock-file tickers; fall back to the instruments universe."""
    if specs:
        return [spec.ticker for spec in specs]
    return list(fallback)


def rule_and_exits(
    symbol: str,
    by_ticker: Dict[str, StockSpec],
    live_rule: contest.Rule,
) -> Tuple[contest.Rule, Optional[float], Optional[float]]:
    """Per-symbol rule + optional fixed % exits from the stock file.

    When a stock file exists, its entry / ATR / target_r / max_hold win.
    Otherwise the contest (or incumbent) live rule is kept. Fixed percentage
    exits are only defined on the stock file.
    """
    spec = by_ticker.get(symbol.upper())
    if spec is None:
        return live_rule, None, None
    return (
        contest.rule_from_spec(spec),
        spec.fixed_stop_pct,
        spec.fixed_take_profit_pct,
    )
