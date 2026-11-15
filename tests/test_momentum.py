"""The live book: 12-1 momentum, the trailing stop, and the share count."""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pytest

from marketmind import momentum


def _rising(n: int = 400, step: float = 0.4) -> dict:
    close = np.array([100.0 + i * step for i in range(n)])
    return {
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "dates": [f"2024-01-{(i % 28) + 1:02d}" for i in range(n)],
    }


def test_twelve_minus_one_ignores_the_last_month() -> None:
    close = np.array([100.0 + i for i in range(253)])
    # A crash inside the skipped month does not change the reading.
    close[-21:] = 50.0
    calm = np.array([100.0 + i for i in range(253)])
    assert momentum.return_12_1(close, 252) == pytest.approx(
        momentum.return_12_1(calm, 252)
    )
    assert momentum.return_12_1(close, 252) > 0
    # Too short to know, and a fallen year is not a buy.
    assert momentum.return_12_1(close, 200) is None
    fallen = np.array([300.0 - i for i in range(253)])
    assert momentum.return_12_1(fallen, 252) < 0


def test_average_range_uses_gaps() -> None:
    close = np.array([10.0, 10.0, 10.0, 14.0])
    high = np.array([11.0, 11.0, 11.0, 14.0])
    low = np.array([9.0, 9.0, 9.0, 13.0])
    # Last bar's true range is the gap from 10 up to 14, not the 1-point bar.
    # The two quiet bars before it have a range of 2, so the average is 8/3.
    assert momentum.true_range(high, low, close, 3) == pytest.approx(4.0)
    assert momentum.atr_at(high, low, close, 3, period=3) == pytest.approx(8 / 3)


def test_assess_buys_a_rising_name_and_places_the_stop() -> None:
    book = _rising()
    # Dates in _rising are not ordered calendar days. Build real ones.
    start = date(2024, 1, 1)
    book["dates"] = [(start.fromordinal(start.toordinal() + i)).isoformat()
                     for i in range(len(book["close"]))]
    session = date(2026, 1, 1)  # every bar is already complete
    snap = momentum.assess(book["high"], book["low"], book["close"],
                           book["dates"], session)
    assert snap is not None and snap.buy
    assert snap.stop == pytest.approx(round(snap.close - 2 * snap.atr, 2))
    assert snap.stop < snap.close


def test_completed_bar_excludes_today() -> None:
    dates = ["2026-11-13", "2026-11-14", "2026-11-15"]
    assert momentum.completed_index(dates, date(2026, 11, 15)) == 1
    assert momentum.completed_index(dates, date(2026, 11, 13)) == -1


def test_session_day_follows_new_york() -> None:
    # 02:30 UTC is still the previous evening in New York in November.
    moment = datetime(2026, 11, 15, 2, 30, tzinfo=timezone.utc)
    assert momentum.session_day(moment) == date(2026, 11, 14)


def test_stop_trails_up_and_a_flat_signal_exits() -> None:
    n = 300
    close = np.array([100.0 + i * 0.5 for i in range(n)])
    high = close + 1
    low = close - 1
    start = date(2024, 1, 1)
    dates = [(start.fromordinal(start.toordinal() + i)).isoformat() for i in range(n)]
    session = date(2026, 1, 1)
    entry = date.fromisoformat(dates[260])
    first = momentum.hold_action(high, low, close, dates, entry, 90.0, session)
    assert first.kind == "raise"
    assert first.stop > 90.0
    # The same peak must not pull the stop back down.
    again = momentum.hold_action(high, low, close, dates, entry, first.stop, session)
    assert again.kind == "hold"
    assert again.stop == pytest.approx(first.stop)

    fallen = np.array([200.0] * n)
    fallen[230:] = 80.0
    left = momentum.hold_action(
        fallen + 1, fallen - 1, fallen, dates, entry, 50.0, session,
    )
    assert left.kind == "exit"
    assert left.reason == "momentum"


def test_a_stop_out_waits_for_the_signal_to_reset() -> None:
    close = np.array([100.0 + i * 0.4 for i in range(400)])
    assert momentum.should_enter(close, 300, 0.2, None, None)
    assert not momentum.should_enter(close, 300, 0.2, "stop_loss", 300)
    assert not momentum.should_enter(close, 320, 0.2, "stop_loss", 300)
    # A stretch where 12-1 is non-positive, then a fresh positive reading.
    dipped = close.copy()
    dipped[280:320] = 50.0
    assert momentum.went_non_positive(dipped, 270, 310)
    assert momentum.should_enter(dipped, 360, 0.1, "stop_loss", 270)
    # A momentum exit is already a reset. The next positive reading may buy.
    assert momentum.should_enter(close, 360, 0.1, "momentum", 300)


def test_share_count_risks_the_budget_and_refuses_a_single_oversized_share() -> None:
    # 0.5% of 100_000 is 500. A 4-point stop buys 125 shares.
    assert momentum.share_quantity(100_000, 100.0, 96.0) == 125
    # One share would risk 600 against a 500 budget.
    assert momentum.share_quantity(100_000, 100.0, 94.0) == 83
    assert momentum.share_quantity(1_000, 100.0, 90.0) == 0
    # Cash the account does not have caps the order.
    assert momentum.share_quantity(100_000, 100.0, 96.0, cash=1_000) == 9
    assert momentum.share_quantity(0, 100.0, 96.0) == 0
