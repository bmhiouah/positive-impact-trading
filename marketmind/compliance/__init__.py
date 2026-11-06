"""Impact-trading execution engine constraints.

This module is the definitive logic boundary for the system's order routing
mechanism. Any code modification bypassing these filters invalidates the
project's positive-impact status.

The module enforces:
  * Whitelisted asset classes only (long-only equities, impact-screened ETFs, spot metals)
  * Blacklisted product rejection (derivatives, debt instruments, synthetics)
  * Cash-only accounts — no margin, no leverage
  * No short selling — BUY and SELL (close long) only
  * Zero-interest architecture with interest-redemption logging
  * Real-time screening: excluded business sectors + debt-to-market-cap < 33%

This file serves as the public API surface for the compliance package.
"""

from .rules import (
    AssetClass,
    AccountType,
    OrderSide,
    OrderIntent,
    ScreeningResult,
    ComplianceVerdict,
    WHITELISTED_ETFS,
    WHITELISTED_METALS,
    EXCLUDED_SECTORS,
    MAX_DEBT_TO_MARKET_CAP,
    is_whitelisted_etf,
    is_whitelisted_metal,
    is_excluded_sector,
)
from .screener import ImpactScreener, screen_symbol
from .validator import OrderValidator, validate_order_intent
from .redemption import InterestRedemptionLedger, record_interest_income
from .engine import ImpactExecutionEngine, route_order

__all__ = [
    "AssetClass",
    "AccountType",
    "OrderSide",
    "OrderIntent",
    "ScreeningResult",
    "ComplianceVerdict",
    "WHITELISTED_ETFS",
    "WHITELISTED_METALS",
    "EXCLUDED_SECTORS",
    "MAX_DEBT_TO_MARKET_CAP",
    "is_whitelisted_etf",
    "is_whitelisted_metal",
    "is_excluded_sector",
    "ImpactScreener",
    "screen_symbol",
    "OrderValidator",
    "validate_order_intent",
    "InterestRedemptionLedger",
    "record_interest_income",
    "ImpactExecutionEngine",
    "route_order",
]