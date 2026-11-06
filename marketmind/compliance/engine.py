"""Impact-compliant execution engine.

This is the definitive routing boundary between the research/opportunity
layer and the Interactive Brokers API. Every order intent must pass the
compliance validator before it is eligible for submission.

The engine:

  * Rejects derivatives, bonds, CFDs, spread bets.
  * Rejects margin accounts — cash only.
  * Rejects short selling — BUY and SELL (close long) only.
  * Runs real-time Impact screening (sector + debt/market-cap < 33%).
  * Tracks idle-cash interest in a redemption ledger.
  * Records every decision (approved or blocked) for auditability.

This module is broker-agnostic: it returns a structured verdict and an
optional ``order_id`` placeholder. A thin IBKR adapter (``ib_insync``)
should call ``submit`` only when ``verdict.approved`` is True.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .redemption import InterestRecord, InterestRedemptionLedger
from .rules import (
    AccountType,
    AssetClass,
    ComplianceVerdict,
    OrderIntent,
    OrderSide,
)
from .validator import OrderValidator, validate_order_intent

logger = logging.getLogger(__name__)


@dataclass
class ExecutionDecision:
    """Outcome of attempting to route an order through the engine."""

    verdict: ComplianceVerdict
    submitted: bool
    order_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ImpactExecutionEngine:
    """Hard-gated execution engine. No order reaches IBKR without approval."""

    def __init__(self, validator: Optional[OrderValidator] = None,
                 ledger: Optional[InterestRedemptionLedger] = None,
                 auto_submit: bool = False) -> None:
        """Create the engine.

        Args:
            validator: Optional pre-configured OrderValidator.
            ledger: Optional InterestRedemptionLedger for interest tracking.
            auto_submit: If True, approved orders are assigned an order id
                as though submitted. In production this should call the
                IBKR adapter directly. Default False (dry-run).
        """
        self._validator = validator or OrderValidator()
        self._ledger = ledger or InterestRedemptionLedger()
        self._auto_submit = auto_submit
        self._decisions: list[ExecutionDecision] = []

    def submit(self, order_intent: OrderIntent) -> ExecutionDecision:
        """Validate and (optionally) submit an order intent.

        Returns an ExecutionDecision containing the compliance verdict and
        whether the order was actually routed.
        """
        verdict = self._validator.validate(order_intent)

        decision = ExecutionDecision(
            verdict=verdict,
            submitted=False,
        )

        if verdict.approved:
            if self._auto_submit:
                decision.order_id = f"SH-{uuid.uuid4().hex[:12]}"
                decision.submitted = True
                logger.info(
                    "Order SUBMITTED to IBKR: %s %s %s (id=%s)",
                    order_intent.side.value,
                    order_intent.symbol,
                    order_intent.quantity,
                    decision.order_id,
                )
            else:
                logger.info(
                    "Order APPROVED (dry-run, not submitted): %s %s %s",
                    order_intent.side.value,
                    order_intent.symbol,
                    order_intent.quantity,
                )
        else:
            decision.rejection_reason = "; ".join(verdict.blocked_reasons)
            logger.warning(
                "Order REJECTED: %s %s %s — %s",
                order_intent.side.value,
                order_intent.symbol,
                order_intent.quantity,
                decision.rejection_reason,
            )

        self._decisions.append(decision)
        return decision

    def record_interest_income(self, source: str, currency: str, amount: float,
                               account_id: str, notes: Optional[str] = None) -> InterestRecord:
        """Log interest income that must be redeemed (donated)."""
        return self._ledger.record_interest(
            source=source,
            currency=currency,
            amount=amount,
            account_id=account_id,
            notes=notes,
        )

    def outstanding_redemption(self, currency: Optional[str] = None) -> float:
        """Total unredeemed interest, optionally filtered by currency."""
        if currency is None:
            return self._ledger.total_outstanding()
        return self._ledger.outstanding_by_currency(currency)

    def decisions(self) -> list[ExecutionDecision]:
        """Return all routing decisions (audit trail)."""
        return list(self._decisions)

    def reset(self) -> None:
        """Clear decisions and ledger (testing only)."""
        self._decisions.clear()
        self._ledger.clear()


def route_order(order_intent: OrderIntent,
                auto_submit: bool = False,
                engine: Optional[ImpactExecutionEngine] = None) -> ExecutionDecision:
    """Convenience function: route a single order through the engine."""
    eng = engine or ImpactExecutionEngine(auto_submit=auto_submit)
    return eng.submit(order_intent)