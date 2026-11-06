"""Core impact-trading rule definitions and data models.

This module hardcodes the project's financial boundaries into typed constants
and data structures. These are the single source of truth for what the
execution engine may and may not do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Final, List, Optional


# ---------------------------------------------------------------------------
# Whitelisted asset classes (the only products the system may touch)
# ---------------------------------------------------------------------------

# Impact-screened ETFs / Mutual Funds tracking positive-impact indices.
WHITELISTED_ETFS: Final[frozenset[str]] = frozenset(
    {
        "SPUS",  # Impact-screened US large-cap ETF
        "HLAL",  # Impact-focused ETF
        "UMMA",  # Umma Fund ETF
        "ISUS",  # Sustainability-focused ETF
    }
)

# Physical spot metals — 100% cash-backed, immediate ownership transfer (T+0).
WHITELISTED_METALS: Final[frozenset[str]] = frozenset(
    {
        "XAUUSD",  # Spot Gold vs USD
        "XAGUSD",  # Spot Silver vs USD
        "GOLD",    # Physical gold
        "SILVER",  # Physical silver
    }
)

# ---------------------------------------------------------------------------
# Strictly prohibited sectors (business-sector screen)
# ---------------------------------------------------------------------------

EXCLUDED_SECTORS: Final[frozenset[str]] = frozenset(
    {
        "financial_services",   # conventional banking/insurance (interest-based / speculative)
        "alcohol",
        "gambling",
        "adult_entertainment",
        "tobacco",
        "weapons",
    }
)

# ---------------------------------------------------------------------------
# Financial ratio thresholds
# ---------------------------------------------------------------------------

MAX_DEBT_TO_MARKET_CAP: Final[float] = 0.33  # 33% — total debt / market cap

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AssetClass(str, Enum):
    """Asset classes recognised by the compliance engine."""

    EQUITY = "equity"                      # individual long-only stock
    ETF = "etf"                            # impact-screened ETF / mutual fund
    SPOT_METAL = "spot_metal"              # physical gold/silver (T+0)
    DERIVATIVE = "derivative"              # option, future, warrant (PROHIBITED)
    BOND = "bond"                          # corporate/government debt (PROHIBITED)
    CFD = "cfd"                            # contract for difference (PROHIBITED)
    SPREAD_BET = "spread_bet"              # spread betting (PROHIBITED)
    UNKNOWN = "unknown"


class AccountType(str, Enum):
    """IBKR account types. Only CASH is permitted."""

    CASH = "cash"
    MARGIN = "margin"                      # PROHIBITED — no leverage/borrowing


class OrderSide(str, Enum):
    """Order directions. Only BUY and SELL (close long) are permitted."""

    BUY = "BUY"                            # open / add to a long position
    SELL = "SELL"                          # close / reduce a long position
    SELL_SHORT = "SELL_SHORT"              # PROHIBITED — selling what you don't own


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderIntent:
    """A proposed order submitted for compliance validation before routing to IBKR.

    Attributes:
        symbol:            Ticker symbol (e.g. "AAPL", "SPUS", "XAUUSD").
        asset_class:       Declared asset class of the instrument.
        side:              BUY or SELL (SELL_SHORT is always rejected).
        quantity:          Number of shares / ounces to trade.
        limit_price:       Optional limit price. None = market order.
        account_type:      Must be AccountType.CASH.
        sector:            Business sector of the issuer (for equity screening).
        debt_to_market_cap: Total debt / market cap ratio (for equity screening).
        is_impact_etf:     True if the symbol is a known impact-screened ETF.
        is_spot_metal:     True if the symbol is a physical spot metal.
    """

    symbol: str
    asset_class: AssetClass
    side: OrderSide
    quantity: float
    limit_price: Optional[float] = None
    account_type: AccountType = AccountType.CASH
    sector: Optional[str] = None
    debt_to_market_cap: Optional[float] = None
    is_impact_etf: bool = False
    is_spot_metal: bool = False


@dataclass(frozen=True)
class ScreeningResult:
    """Outcome of the real-time impact screening pipeline for a symbol."""

    symbol: str
    passed: bool
    reasons: List[str] = field(default_factory=list)
    sector: Optional[str] = None
    debt_to_market_cap: Optional[float] = None


@dataclass(frozen=True)
class ComplianceVerdict:
    """Final verdict on whether an order intent may be routed to IBKR.

    Attributes:
        approved:          True only if every check passed.
        order_intent:      The original intent that was evaluated.
        screening:         Result of the real-time screening pipeline.
        blocked_reasons:   Human-readable list of why the order was blocked.
        timestamp_utc:     When the verdict was computed.
    """

    approved: bool
    order_intent: OrderIntent
    screening: ScreeningResult
    blocked_reasons: List[str] = field(default_factory=list)
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Helper predicates
# ---------------------------------------------------------------------------


def is_whitelisted_etf(symbol: str) -> bool:
    """Return True if *symbol* is a known impact-screened ETF / mutual fund."""
    return symbol.upper() in WHITELISTED_ETFS


def is_whitelisted_metal(symbol: str) -> bool:
    """Return True if *symbol* is a physical spot metal (gold/silver, T+0)."""
    return symbol.upper() in WHITELISTED_METALS


def is_excluded_sector(sector: Optional[str]) -> bool:
    """Return True if *sector* is on the excluded (prohibited) business list."""
    if sector is None:
        return False
    return sector.lower().strip() in EXCLUDED_SECTORS


def is_debt_ratio_compliant(debt_to_market_cap: Optional[float]) -> bool:
    """Return True if the debt-to-market-cap ratio is under 33%."""
    if debt_to_market_cap is None:
        return False
    return debt_to_market_cap < MAX_DEBT_TO_MARKET_CAP