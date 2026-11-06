"""Real-time Impact screening pipeline.

Before any order is pushed to the IBKR API, the stock's ticker symbol must
pass through a screening function validating:

  1. Business sector — no financial services, alcohol, gambling, adult
     entertainment, tobacco, or weapons.
  2. Financial ratios — total debt to market capitalisation under 33%.

ETFs and spot metals are whitelisted by symbol and bypass the equity-specific
ratio/sector screens (their compliance is established at the fund level).
"""

from __future__ import annotations

import logging
from typing import Optional

from .rules import (
    AssetClass,
    MAX_DEBT_TO_MARKET_CAP,
    ScreeningResult,
    is_debt_ratio_compliant,
    is_excluded_sector,
    is_whitelisted_etf,
    is_whitelisted_metal,
)

logger = logging.getLogger(__name__)


class ImpactScreener:
    """Screens a symbol against Impact business-sector and financial-ratio
    rules before an order may be routed to IBKR.

    The screener is intentionally side-effect free so it can be unit-tested
    without a live broker connection. Subclass or inject a data provider to
    look up sector and ratio metadata for a given symbol.
    """

    def __init__(self, sector_lookup: Optional[dict[str, str]] = None,
                 ratio_lookup: Optional[dict[str, float]] = None) -> None:
        """Create a screener.

        Args:
            sector_lookup: Optional mapping of symbol -> business sector.
                Used when the engine does not have live fundamental data.
            ratio_lookup: Optional mapping of symbol -> debt/market-cap ratio.
        """
        self._sector_lookup = sector_lookup or {}
        self._ratio_lookup = ratio_lookup or {}

    def screen(self, symbol: str,
               asset_class: AssetClass,
               sector: Optional[str] = None,
               debt_to_market_cap: Optional[float] = None,
               is_impact_etf: bool = False,
               is_spot_metal: bool = False) -> ScreeningResult:
        """Run the full Impact screen for *symbol*.

        Args:
            symbol:            Ticker symbol.
            asset_class:       Declared asset class.
            sector:            Business sector (equities only). Falls back to
                the internal lookup if not supplied.
            debt_to_market_cap: Total debt / market cap (equities only).
                Falls back to the internal lookup if not supplied.
            is_impact_etf:    True if the symbol is a known Impact ETF.
            is_spot_metal:     True if the symbol is a physical spot metal.

        Returns:
            A ScreeningResult with passed=True only when every applicable
            check succeeds.
        """
        symbol = symbol.upper().strip()
        reasons: list[str] = []

        # --- Whitelisted instruments bypass equity-specific screens ---------
        if is_impact_etf or asset_class == AssetClass.ETF:
            if not is_whitelisted_etf(symbol):
                reasons.append(
                    f"ETF '{symbol}' is not on the Impact-compliant whitelist"
                )
            return ScreeningResult(
                symbol=symbol,
                passed=len(reasons) == 0,
                reasons=reasons,
                sector=sector,
                debt_to_market_cap=debt_to_market_cap,
            )

        if is_spot_metal or asset_class == AssetClass.SPOT_METAL:
            if not is_whitelisted_metal(symbol):
                reasons.append(
                    f"Spot metal '{symbol}' is not on the physical metals whitelist"
                )
            return ScreeningResult(
                symbol=symbol,
                passed=len(reasons) == 0,
                reasons=reasons,
                sector=sector,
                debt_to_market_cap=debt_to_market_cap,
            )

        # --- Equity screening ---------------------------------------------
        # Resolve sector from argument or lookup table.
        resolved_sector = sector
        if resolved_sector is None:
            resolved_sector = self._sector_lookup.get(symbol)

        # Resolve debt ratio from argument or lookup table.
        resolved_ratio = debt_to_market_cap
        if resolved_ratio is None:
            resolved_ratio = self._ratio_lookup.get(symbol)

        # 1. Business-sector screen
        if resolved_sector is not None and is_excluded_sector(resolved_sector):
            reasons.append(
                f"Sector '{resolved_sector}' is excluded under Impact principles"
            )

        # 2. Debt-to-market-cap screen (must be under 33%)
        if resolved_ratio is None:
            reasons.append(
                f"No debt-to-market-cap data for '{symbol}'; cannot validate "
                f"< {MAX_DEBT_TO_MARKET_CAP:.0%} threshold"
            )
        elif not is_debt_ratio_compliant(resolved_ratio):
            reasons.append(
                f"Debt-to-market-cap {resolved_ratio:.2%} exceeds the "
                f"{MAX_DEBT_TO_MARKET_CAP:.0%} Impact limit"
            )

        passed = len(reasons) == 0

        if not passed:
            logger.warning(
                "Impact screen FAILED for %s: %s",
                symbol,
                "; ".join(reasons),
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


def screen_symbol(symbol: str,
                  asset_class: AssetClass,
                  sector: Optional[str] = None,
                  debt_to_market_cap: Optional[float] = None,
                  is_impact_etf: bool = False,
                  is_spot_metal: bool = False) -> ScreeningResult:
    """Convenience function: screen a single symbol with a default screener."""
    screener = ImpactScreener()
    return screener.screen(
        symbol=symbol,
        asset_class=asset_class,
        sector=sector,
        debt_to_market_cap=debt_to_market_cap,
        is_impact_etf=is_impact_etf,
        is_spot_metal=is_spot_metal,
    )