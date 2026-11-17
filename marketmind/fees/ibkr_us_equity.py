"""IBKR Pro Fixed US equity costs for a French-resident EUR-base account.

Rates follow the research note (IBKR official pricing as of 2026-11-17):

* Pro Fixed: USD 0.005 / share, USD 1.00 minimum, 1% of trade value cap
* SEC on sells: USD 0.0000206 × sale value
* FINRA TAF: 0 through 2026-12-31 (fee holiday)
* CAT: USD 0.000003 / share (both sides; optional)
* AutoFX ~3 bp/leg: off by default (assume a USD cash sleeve)

Paper P&L is not fee-accurate; this model is for honest backtests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class IbkrProFixed:
    """IBKR Pro Fixed schedule for US SMART stocks/ETFs."""

    per_share: float = 0.005
    minimum: float = 1.00
    max_pct_of_trade: float = 0.01
    sec_rate: float = 0.0000206
    cat_per_share: float = 0.000003
    taf_per_share: float = 0.0  # holiday through 2026-12-31
    include_cat: bool = True
    fx_bp_per_leg: float = 0.0003  # AutoFX ~0.03%
    include_fx: bool = False  # off: keep USD cash after sells


DEFAULT_IBKR_PRO_FIXED = IbkrProFixed()


def order_commission(
    shares: float,
    price: float,
    schedule: IbkrProFixed = DEFAULT_IBKR_PRO_FIXED,
) -> float:
    """Per-order Fixed commission: max(shares × rate, min), capped at 1% value."""
    qty = float(shares)
    px = float(price)
    if qty <= 0 or px <= 0:
        return 0.0
    notional = qty * px
    raw = max(qty * schedule.per_share, schedule.minimum)
    cap = schedule.max_pct_of_trade * notional
    return float(min(raw, cap) if cap > 0 else raw)


def buy_fees(
    shares: float,
    price: float,
    schedule: IbkrProFixed = DEFAULT_IBKR_PRO_FIXED,
) -> float:
    """USD cost to open a long (commission + optional CAT + optional FX)."""
    qty = float(shares)
    px = float(price)
    if qty <= 0 or px <= 0:
        return 0.0
    total = order_commission(qty, px, schedule)
    if schedule.include_cat:
        total += qty * schedule.cat_per_share
    if schedule.include_fx:
        total += qty * px * schedule.fx_bp_per_leg
    return float(total)


def sell_fees(
    shares: float,
    price: float,
    schedule: IbkrProFixed = DEFAULT_IBKR_PRO_FIXED,
) -> float:
    """USD cost to close a long (commission + SEC + TAF + optional CAT/FX)."""
    qty = float(shares)
    px = float(price)
    if qty <= 0 or px <= 0:
        return 0.0
    notional = qty * px
    total = order_commission(qty, px, schedule)
    total += schedule.sec_rate * notional
    total += qty * schedule.taf_per_share
    if schedule.include_cat:
        total += qty * schedule.cat_per_share
    if schedule.include_fx:
        total += notional * schedule.fx_bp_per_leg
    return float(total)


def round_trip_fees(
    shares: float,
    buy_price: float,
    sell_price: float,
    schedule: IbkrProFixed = DEFAULT_IBKR_PRO_FIXED,
) -> float:
    """All-in USD fees for a round-trip long."""
    return buy_fees(shares, buy_price, schedule) + sell_fees(
        shares, sell_price, schedule
    )


def effective_cost_fraction(
    shares: float,
    price: float,
    *,
    side: str,
    schedule: IbkrProFixed = DEFAULT_IBKR_PRO_FIXED,
) -> float:
    """Fee as a fraction of notional (for comparison with flat-bp models)."""
    qty = float(shares)
    px = float(price)
    if qty <= 0 or px <= 0:
        return 0.0
    notional = qty * px
    fee = buy_fees(qty, px, schedule) if side == "buy" else sell_fees(
        qty, px, schedule
    )
    return float(fee / notional)
