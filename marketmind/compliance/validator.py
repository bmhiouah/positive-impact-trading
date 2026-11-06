"""Order-intent validation against Impact compliance rules.

This module is the hard gate between the opportunity engine / research agents
and the IBKR API. No order reaches the broker without passing
``OrderValidator.validate``.
"""

from __future__ import annotations

import logging
from typing import Optional

from .rules import (
    AccountType,
    AssetClass,
    ComplianceVerdict,
    OrderIntent,
    OrderSide,
    ScreeningResult,
    MAX_DEBT_TO_MARKET_CAP,
    is_debt_ratio_compliant,
    is_excluded_sector,
)
from .screener import ImpactScreener, screen_symbol

logger = logging.getLogger(__name__)


class OrderValidator:
    """Validates an OrderIntent against every Impact compliance boundary.

    Checks performed, in order:
      1. Account must be CASH (no margin / leverage).
      2. Side must be BUY or SELL (SELL_SHORT is rejected).
      3. Asset class must be whitelisted (equity, Impact ETF, spot metal).
      4. Derivatives, bonds, CFDs and spread bets are rejected outright.
      5. Real-time screening: excluded sector + debt/market-cap < 33%.
    """

    def __init__(self, screener: Optional[ImpactScreener] = None) -> None:
        self._screener = screener or ImpactScreener()

    def validate(self, order_intent: OrderIntent) -> ComplianceVerdict:
        """Return a ComplianceVerdict for *order_intent*.

        The verdict is approved only when every check passes. All failure
        reasons are collected and returned in ``blocked_reasons`` so the
        caller can log or surface them to the user.
        """
        blocked: list[str] = []

        # --- 1. Cash-only account (no margin / leverage) ------------------
        if order_intent.account_type != AccountType.CASH:
            blocked.append(
                f"Account type '{order_intent.account_type.value}' is not "
                "permitted — only IBKR Cash Accounts are allowed (no margin, "
                "no leverage, no borrowing)."
            )

        # --- 2. No short selling ------------------------------------------
        if order_intent.side == OrderSide.SELL_SHORT:
            blocked.append(
                "SELL_SHORT is prohibited — the system can only BUY (open "
                "long) or SELL (close long). You cannot sell what you do not own."
            )

        # --- 3. Blacklisted asset classes ---------------------------------
        if order_intent.asset_class in (
            AssetClass.DERIVATIVE,
            AssetClass.BOND,
            AssetClass.CFD,
            AssetClass.SPREAD_BET,
        ):
            blocked.append(
                f"Asset class '{order_intent.asset_class.value}' is strictly "
                "prohibited under Impact finance principles (Interest / Speculation)."
            )

        # --- 4. Whitelist enforcement -------------------------------------
        if order_intent.asset_class == AssetClass.ETF:
            if not order_intent.is_impact_etf:
                blocked.append(
                    f"ETF '{order_intent.symbol}' is not flagged as a "
                    "Impact-compliant ETF / mutual fund."
                )
        elif order_intent.asset_class == AssetClass.SPOT_METAL:
            if not order_intent.is_spot_metal:
                blocked.append(
                    f"Symbol '{order_intent.symbol}' is not recognised as a "
                    "physical spot metal (gold/silver, T+0)."
                )
        elif order_intent.asset_class == AssetClass.EQUITY:
            # Equities pass through the real-time screen below.
            pass
        else:
            # UNKNOWN or any other class — reject by default.
            blocked.append(
                f"Asset class '{order_intent.asset_class.value}' is not on the "
                "Impact-compliant whitelist."
            )

        # --- 5. Real-time screening pipeline ------------------------------
        screening = self._screener.screen(
            symbol=order_intent.symbol,
            asset_class=order_intent.asset_class,
            sector=order_intent.sector,
            debt_to_market_cap=order_intent.debt_to_market_cap,
            is_impact_etf=order_intent.is_impact_etf,
            is_spot_metal=order_intent.is_spot_metal,
        )

        if not screening.passed:
            blocked.extend(screening.reasons)

        approved = len(blocked) == 0

        verdict = ComplianceVerdict(
            approved=approved,
            order_intent=order_intent,
            screening=screening,
            blocked_reasons=blocked,
        )

        if approved:
            logger.info(
                "Order APPROVED: %s %s %s (screening: %s)",
                order_intent.side.value,
                order_intent.symbol,
                order_intent.quantity,
                "passed",
            )
        else:
            logger.warning(
                "Order BLOCKED: %s %s %s — %s",
                order_intent.side.value,
                order_intent.symbol,
                order_intent.quantity,
                "; ".join(blocked),
            )

        return verdict


def validate_order_intent(order_intent: OrderIntent,
                          screener: Optional[ImpactScreener] = None) -> ComplianceVerdict:
    """Convenience function: validate a single order intent."""
    validator = OrderValidator(screener=screener)
    return validator.validate(order_intent)