"""IBKR Pro Fixed US equity fee math (no Gateway)."""

from __future__ import annotations

import pytest

from marketmind.fees.ibkr_us_equity import (
    DEFAULT_IBKR_PRO_FIXED,
    IbkrProFixed,
    buy_fees,
    order_commission,
    round_trip_fees,
    sell_fees,
)


def test_small_ticket_hits_usd_1_minimum() -> None:
    # 40 shares × 0.005 = 0.20 → minimum 1.00 (fee note worked example)
    assert order_commission(40, 100.0) == pytest.approx(1.00)


def test_large_ticket_uses_per_share_rate() -> None:
    # 500 × 0.005 = 2.50 > 1.00
    assert order_commission(500, 100.0) == pytest.approx(2.50)


def test_commission_capped_at_one_percent() -> None:
    # 1 share @ $0.50 → notional 0.50; raw min 1.00 would exceed 1% cap (0.005)
    assert order_commission(1, 0.50) == pytest.approx(0.005)


def test_sell_includes_sec() -> None:
    # 40 @ 100: commission 1.00 + SEC 0.0000206×4000 + CAT 40×3e-6
    fee = sell_fees(40, 100.0)
    assert fee == pytest.approx(1.00 + 0.0824 + 0.00012)


def test_buy_has_no_sec() -> None:
    fee = buy_fees(40, 100.0)
    assert fee == pytest.approx(1.00 + 0.00012)  # commission + CAT


def test_round_trip_matches_fee_note_equity_subtotal() -> None:
    # Fee note: ≈ 2.08 for 40 @ 100 flat (two minima + SEC + CAT both sides)
    total = round_trip_fees(40, 100.0, 100.0)
    assert total == pytest.approx(2.08264, rel=1e-4)


def test_taf_holiday_default_is_zero() -> None:
    assert DEFAULT_IBKR_PRO_FIXED.taf_per_share == 0.0


def test_fx_optional() -> None:
    no_fx = buy_fees(40, 100.0, DEFAULT_IBKR_PRO_FIXED)
    with_fx = buy_fees(
        40, 100.0, IbkrProFixed(include_fx=True, fx_bp_per_leg=0.0003)
    )
    assert with_fx == pytest.approx(no_fx + 1.20)


def test_zero_shares_is_zero() -> None:
    assert order_commission(0, 100.0) == 0.0
    assert buy_fees(-1, 100.0) == 0.0
