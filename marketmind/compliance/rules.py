"""Core impact-trading rule definitions and data models.

Eligibility lists (excluded sectors, debt cap, ETF/metal whitelists) are
loaded from the shared file ``ethics/exclusions.json``. This module exposes
typed constants and predicates used by the execution engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Final, List, Optional

from marketmind.ethics.filter import default_ethics_filter

# ---------------------------------------------------------------------------
# Shared ethics filter (file-driven source of truth)
# ---------------------------------------------------------------------------

_ETHICS = default_ethics_filter()

# Impact-screened ETFs / Mutual Funds tracking positive-impact indices.
WHITELISTED_ETFS: Final[frozenset[str]] = _ETHICS.whitelisted_etfs

# Physical spot metals — 100% cash-backed, immediate ownership transfer (T+0).
WHITELISTED_METALS: Final[frozenset[str]] = _ETHICS.whitelisted_metals

# Canonical excluded sector ids (aliases live in ethics/exclusions.json).
EXCLUDED_SECTORS: Final[frozenset[str]] = _ETHICS.excluded_sector_ids

# Financial ratio threshold — total debt / market cap.
MAX_DEBT_TO_MARKET_CAP: Final[float] = _ETHICS.max_debt_to_market_cap

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
        stop_price:        Protective sell-stop for a BUY. Ignored on a close.
        target_price:      Optional sell-limit. Absent when the stop trails.
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
    stop_price: Optional[float] = None
    target_price: Optional[float] = None


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
    return default_ethics_filter().is_whitelisted_etf(symbol)


def is_whitelisted_metal(symbol: str) -> bool:
    """Return True if *symbol* is a physical spot metal (gold/silver, T+0)."""
    return default_ethics_filter().is_whitelisted_metal(symbol)


def is_excluded_sector(sector: Optional[str]) -> bool:
    """Return True if *sector* matches an excluded id or IB/UI alias.

    Matching is normalized (case, spaces, punctuation), so labels like
    ``Financial Services``, ``financials``, and ``financial_services`` all
    resolve against ``ethics/exclusions.json``.
    """
    return default_ethics_filter().is_excluded_sector(sector)


def is_debt_ratio_compliant(debt_to_market_cap: Optional[float]) -> bool:
    """Return True if the debt-to-market-cap ratio is under the ethics cap."""
    return default_ethics_filter().is_debt_ratio_compliant(debt_to_market_cap)