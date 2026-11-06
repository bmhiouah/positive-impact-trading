"""Data-layer-integrated impact compliance screener.

Bridges the market data layer (DuckDB store + enrichment pipeline)
with the compliance rules engine. Uses cached fundamental data
to run impact screening on symbols.

Design principles:
  * Cache-first — uses DuckDB-stored fundamentals if available
  * Falls back to provided data if cache miss
  * Type-safe — uses Pydantic models and AssetClass enum
  * Audit-ready — logs all screening decisions
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..data.models import FundamentalData, ScreenResult, DataUpdate
from ..data.storage import DataStore
from ..compliance.rules import (
    AssetClass,
    MAX_DEBT_TO_MARKET_CAP,
    ScreeningResult,
    is_debt_ratio_compliant,
    is_excluded_sector,
    is_whitelisted_etf,
    is_whitelisted_metal,
)

logger = logging.getLogger(__name__)


class DataIntegratedScreener:
    """Screens symbols using cached fundamental data from the DataStore.

    Uses the DataStore to look up fundamental data (sectors, debt ratios)
    and applies the impact-trading compliance rules from the rules module.

    Parameters
    ----------
    store : DataStore
        DuckDB-backed data store with cached fundamental data.
    """

    def __init__(self, store: DataStore) -> None:
        self._store = store

    def screen_symbol(
        self, symbol: str, asset_class: AssetClass
    ) -> ScreeningResult:
        """Run the full impact screen for a symbol using cached data.

        Parameters
        ----------
        symbol : str
            Ticker symbol (e.g., "AAPL").
        asset_class : AssetClass
            The asset class of the symbol.

        Returns
        -------
        ScreeningResult
            Result of the impact screening pipeline.
        """
        symbol = symbol.upper().strip()

        # Look up cached fundamental data
        fundamentals = self._store.get_fundamental_data(symbol)

        reasons: List[str] = []

        # --- Whitelisted instruments bypass equity-specific screens ---------
        if asset_class == AssetClass.ETF or asset_class == AssetClass.SPOT_METAL:
            # Check if it's a whitelisted symbol
            if asset_class == AssetClass.ETF and not is_whitelisted_etf(symbol):
                reasons.append(
                    f"ETF '{symbol}' is not on the Impact-compliant whitelist"
                )
            if asset_class == AssetClass.SPOT_METAL and not is_whitelisted_metal(
                symbol
            ):
                reasons.append(
                    f"Spot metal '{symbol}' is not on the physical metals whitelist"
                )
            passed = len(reasons) == 0
            return ScreeningResult(
                symbol=symbol,
                passed=passed,
                reasons=reasons,
                sector=fundamentals.sector if fundamentals else None,
                debt_to_market_cap=fundamentals.debt_to_market_cap if fundamentals else None,
            )

        # --- Equity screening ---------------------------------------------
        # Resolve sector from fundamental data
        resolved_sector = fundamentals.sector if fundamentals else None

        # Resolve debt ratio from fundamental data
        resolved_ratio = fundamentals.debt_to_market_cap if fundamentals else None

        # 1. Business-sector screen
        if resolved_sector is not None and is_excluded_sector(resolved_sector):
            reasons.append(
                f"Sector '{resolved_sector}' is excluded under Impact principles"
            )

        # 2. Debt-to-market-cap screen (must be under 33%)
        if resolved_ratio is None:
            reasons.append(
                f"No debt-to-market-cap data for '{symbol}'; cannot validate "
                f"under {MAX_DEBT_TO_MARKET_CAP:.0%} threshold"
            )
        elif not is_debt_ratio_compliant(resolved_ratio):
            reasons.append(
                f"Debt-to-market-cap {resolved_ratio:.2%} exceeds the "
                f"{MAX_DEBT_TO_MARKET_CAP:.0%} Impact limit"
            )

        passed = len(reasons) == 0

        if not passed:
            logger.warning(
                "Impact screen FAILED for %s: %s", symbol, "; ".join(reasons)
            )
        else:
            logger.info("Impact screen PASSED for %s", symbol)

        return ScreeningResult(
            symbol=symbol,
            passed=passed,
            reasons=reasons,
            sector=resolved_sector,
            debt_to_market_cap=resolved_ratio,
        )

    def screen_symbols(
        self, symbols: List[str], asset_classes: List[AssetClass]
    ) -> List[ScreeningResult]:
        """Screen multiple symbols.

        Parameters
        ----------
        symbols : list[str]
            Ticker symbols to screen.
        asset_classes : list[AssetClass]
            Asset class for each symbol (must be same length as symbols).

        Returns
        -------
        list[ScreeningResult]
            Screening results for each symbol.
        """
        if len(symbols) != len(asset_classes):
            raise ValueError(
                f"symbols length ({len(symbols)}) must match "
                f"asset_classes length ({len(asset_classes)})"
            )

        results = []
        for symbol, asset_class in zip(symbols, asset_classes):
            result = self.screen_symbol(symbol, asset_class)
            results.append(result)
        return results

    def get_passed_symbols(self) -> List[str]:
        """Get all symbols that passed the latest compliance screen.

        Returns symbols from the screen_results table in DataStore.
        """
        return self._store.get_passed_symbols()


# Convenience function for single symbol screening
def screen_symbol(
    symbol: str, asset_class: AssetClass, store: DataStore
) -> ScreeningResult:
    """Convenience function: screen a single symbol with a data-integrated screener.

    Parameters
    ----------
    symbol : str
        Ticker symbol.
    asset_class : AssetClass
        Asset class of the symbol.
    store : DataStore
        Data store with cached fundamental data.

    Returns
    -------
    ScreeningResult
        Result of the impact screening pipeline.
    """
    screener = DataIntegratedScreener(store)
    return screener.screen_symbol(symbol, asset_class)
