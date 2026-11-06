"""Interest redemption ledger for idle-cash interest.

Conventional interest concentrates wealth — those who already have capital
earn more from it, widening inequality. If idle cash inside IBKR yields
conventional bank interest, those funds must be tracked in a redemption
ledger so they can be programmatically or manually deducted and donated —
they must never be reinvested or retained for personal benefit.

This module is side-effect free and stores records in memory. A production
deployment should persist the ledger to the project's database (DuckDB /
PostgreSQL) and integrate with a donations/payments subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass(frozen=True)
class InterestRecord:
    """A single entry recording interest income that must be redeemed."""

    timestamp_utc: str
    source: str                      # e.g. "IBKR cash balance", "money market fund"
    currency: str                    # e.g. "USD", "EUR"
    amount: float                    # gross interest amount
    account_id: str                  # IBKR account identifier
    redeemed: bool = False
    redemption_timestamp_utc: Optional[str] = None
    donation_reference: Optional[str] = None  # e.g. charity transaction ID
    notes: Optional[str] = None

    def mark_redeemed(self, donation_reference: Optional[str] = None) -> "InterestRecord":
        """Return a copy of this record marked as redeemed/donated."""
        return InterestRecord(
            timestamp_utc=self.timestamp_utc,
            source=self.source,
            currency=self.currency,
            amount=self.amount,
            account_id=self.account_id,
            redeemed=True,
            redemption_timestamp_utc=datetime.now(timezone.utc).isoformat(),
            donation_reference=donation_reference,
            notes=self.notes,
        )


class InterestRedemptionLedger:
    """Tracks interest income awaiting redemption (donation).

    All amounts are expected to be positive (income received). The ledger
    exposes the total outstanding amount that must be donated so the
    execution engine or a reporting job can act on it.
    """

    def __init__(self) -> None:
        self._records: List[InterestRecord] = []

    def record_interest(self, source: str, currency: str, amount: float,
                        account_id: str, notes: Optional[str] = None) -> InterestRecord:
        """Log a new interest-income entry awaiting redemption.

        Args:
            source:   Where the interest originated (e.g. "IBKR cash balance").
            currency: Currency of the interest amount.
            amount:   Gross interest amount (must be >= 0).
            account_id: IBKR account identifier.
            notes:    Optional free-form notes.

        Returns:
            The created InterestRecord.
        """
        if amount < 0:
            raise ValueError("Interest amount must be non-negative.")

        record = InterestRecord(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            source=source,
            currency=currency,
            amount=amount,
            account_id=account_id,
            redeemed=False,
            redemption_timestamp_utc=None,
            donation_reference=None,
            notes=notes,
        )
        self._records.append(record)
        return record

    def mark_redeemed(self, record: InterestRecord,
                      donation_reference: Optional[str] = None) -> None:
        """Mark an existing record as redeemed (donated to charity).

        Raises:
            ValueError: if the record is not found in this ledger.
        """
        for idx, existing in enumerate(self._records):
            if existing.timestamp_utc == record.timestamp_utc \
                    and existing.account_id == record.account_id \
                    and existing.source == record.source:
                self._records[idx] = existing.mark_redeemed(donation_reference)
                return
        raise ValueError("Interest record not found in ledger.")

    def outstanding_by_currency(self, currency: str) -> float:
        """Total unredeemed interest for *currency*."""
        return sum(
            r.amount for r in self._records
            if not r.redeemed and r.currency == currency
        )

    def total_outstanding(self) -> float:
        """Total unredeemed interest across all currencies."""
        return sum(r.amount for r in self._records if not r.redeemed)

    def total_redeemed(self) -> float:
        """Total already-redeemed (donated) interest across all currencies."""
        return sum(r.amount for r in self._records if r.redeemed)

    def records(self, redeemed: Optional[bool] = None) -> List[InterestRecord]:
        """Return all records, optionally filtered by redemption status."""
        if redeemed is None:
            return list(self._records)
        return [r for r in self._records if r.redeemed == redeemed]

    def clear(self) -> None:
        """Remove all records (for testing / reset)."""
        self._records.clear()


# Module-level default ledger for convenience use.
default_ledger = InterestRedemptionLedger()


def record_interest_income(source: str, currency: str, amount: float,
                           account_id: str,
                           notes: Optional[str] = None) -> InterestRecord:
    """Convenience function: record interest on the default ledger."""
    return default_ledger.record_interest(
        source=source,
        currency=currency,
        amount=amount,
        account_id=account_id,
        notes=notes,
    )