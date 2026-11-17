"""The contest: fills, the out-of-sample gate, and the saved live rule."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from marketmind.contest import (
    INCUMBENT,
    Rule,
    Score,
    choose,
    describe,
    ensure,
    live_rule,
    simulate,
)
from marketmind.data import DataStore
from marketmind.data.models import PriceBar


def _flat(n, price=100.0, atr=1.0):
    close = np.full(n, price)
    return {
        "open": close.copy(),
        "high": close + 0.4,
        "low": close - 0.4,
        "close": close,
        "atr": np.full(n, atr),
        "signal": np.zeros(n, dtype=bool),
        "known": np.ones(n, dtype=bool),
        "dates": [f"2024-01-{(i % 28) + 1:02d}" for i in range(n)],
    }


def test_a_bar_that_touches_both_levels_is_the_stop() -> None:
    book = _flat(6)
    book["signal"][1] = True
    # Next open is the fill at 100. Risk is 2 × atr 1, so the stop is 98
    # and a 2R target is 104. This session trades through both.
    book["open"][2] = 100
    book["high"][2] = 105
    book["low"][2] = 97
    rule = Rule("breakout", 2.0, target_r=2.0, max_hold=None)
    trades = simulate(
        book["open"], book["high"], book["low"], book["close"], book["atr"],
        book["signal"], book["known"], book["dates"], rule, cost=0.0,
    )
    assert len(trades) == 1
    assert trades[0].reason == "stop"
    assert trades[0].r_multiple < 0


def test_a_target_fills_when_the_stop_is_not_touched() -> None:
    book = _flat(6)
    book["signal"][1] = True
    book["high"][2] = 105
    book["low"][2] = 99.5
    rule = Rule("breakout", 2.0, target_r=2.0)
    trades = simulate(
        book["open"], book["high"], book["low"], book["close"], book["atr"],
        book["signal"], book["known"], book["dates"], rule, cost=0.0,
    )
    assert trades[0].reason == "target"
    assert trades[0].r_multiple == pytest.approx(2.0)


def test_a_favorite_that_loses_later_does_not_replace_the_incumbent() -> None:
    favorite = Score(
        rule=Rule("m3", 1.5, None, 63),
        selection_r=12.0, selection_trades=8, selection_dd=1.0,
        confirmation_r=-3.0, confirmation_trades=6, confirmation_dd=4.0,
    )
    incumbent = Score(
        rule=INCUMBENT,
        selection_r=2.0, selection_trades=8, selection_dd=1.0,
        confirmation_r=1.5, confirmation_trades=6, confirmation_dd=1.0,
    )
    holder, promoted, _reason = choose([favorite, incumbent])
    assert holder == INCUMBENT
    assert promoted is False


def test_a_favorite_that_keeps_making_money_becomes_the_live_rule() -> None:
    favorite = Score(
        rule=Rule("trend", 2.5, None, None),
        selection_r=6.0, selection_trades=10, selection_dd=2.0,
        confirmation_r=4.0, confirmation_trades=8, confirmation_dd=1.0,
    )
    incumbent = Score(
        rule=INCUMBENT,
        selection_r=3.0, selection_trades=10, selection_dd=2.0,
        confirmation_r=1.0, confirmation_trades=8, confirmation_dd=1.0,
    )
    holder, promoted, _reason = choose([favorite, incumbent])
    assert promoted is True
    assert holder == favorite.rule
    assert "Golden cross" in describe(holder)


def test_contest_keeps_the_incumbent_until_there_are_two_years_of_bars(tmp_path) -> None:
    store = DataStore(tmp_path / "lab.db")
    start = datetime(2024, 1, 1)
    bars = []
    for i in range(30):
        price = 100 + i * 0.2
        bars.append(PriceBar(
            symbol="AAPL", timestamp=start + timedelta(days=i),
            open=price, high=price + 1, low=price - 0.5, close=price + 0.1,
            volume=1_000, bar_size="1 day",
        ))
    store.insert_price_bars(bars)
    report = ensure(store, ["AAPL"])
    assert report.ready is False
    assert report.holder == INCUMBENT
    assert live_rule(store) == INCUMBENT
    store.close()


def test_allow_entry_state_requires_rising_edge(monkeypatch) -> None:
    """Sticky m12 must not re-fire every day while the regime stays on."""
    from marketmind import contest

    book = {
        "high": np.zeros(5), "low": np.zeros(5),
        "close": np.zeros(5), "volume": np.zeros(5),
    }
    # False, False, True, True, False — only index 2 is a rising edge.
    values = {0: False, 1: False, 2: True, 3: True, 4: False}

    def fake(high, low, close, volume, index, entry):
        return values.get(index)

    monkeypatch.setattr(contest, "signal_at", fake)
    rule = Rule("m12", 2.0)
    assert contest.allow_entry(book, rule, 2, None, None) is True
    assert contest.allow_entry(book, rule, 3, None, None) is False
    assert contest.allow_entry(book, rule, 1, None, None) is False
    # Event entries stay level-triggered.
    breakout = Rule("breakout", 2.0)
    assert contest.allow_entry(book, breakout, 3, None, None) is True


def test_saved_contest_is_not_recomputed_when_the_bars_have_not_changed(tmp_path) -> None:
    store = DataStore(tmp_path / "lab.db")
    start = datetime(2020, 1, 1)
    bars = []
    for i in range(420):
        price = 50 + i * 0.15
        bars.append(PriceBar(
            symbol="MSFT", timestamp=start + timedelta(days=i),
            open=price, high=price + 0.8, low=max(0.1, price - 0.8),
            close=price + 0.05, volume=2_000 + i, bar_size="1 day",
        ))
    store.insert_price_bars(bars)
    first = ensure(store, ["MSFT"])
    second = ensure(store, ["MSFT"])
    assert second.fingerprint == first.fingerprint
    assert second.updated_at == first.updated_at
    assert second.holder == first.holder
    store.close()
