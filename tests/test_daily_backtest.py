"""Fee-aware six-month daily backtest over local price_bars (no Gateway)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from marketmind.backtest.daily import (
    BACKTEST_SESSIONS,
    run_folder_backtest,
    run_spec_backtest,
)
from marketmind.data import DataStore
from marketmind.data.models import PriceBar
from marketmind.stock_specs.loader import stock_spec_from_payload


def _seed_bars(store: DataStore, symbol: str, n: int, start: datetime) -> None:
    bars = []
    for i in range(n):
        # Gentle uptrend so m12 can eventually flip on; ATR stays positive.
        price = 100.0 + i * 0.05
        bars.append(
            PriceBar(
                symbol=symbol,
                timestamp=start + timedelta(days=i),
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price + 0.1,
                volume=10_000 + i,
                bar_size="1 day",
            )
        )
    store.insert_price_bars(bars)


def test_backtest_sessions_is_six_months() -> None:
    assert BACKTEST_SESSIONS == 126


def test_missing_bars_skips_cleanly(tmp_path) -> None:
    store = DataStore(tmp_path / "lab.db")
    spec = stock_spec_from_payload({
        "ticker": "AAPL",
        "signal": {"entry": "m12"},
        "exits": {"atr_multiple": 2.0},
    })
    result = run_spec_backtest(store, spec, sessions=20)
    assert result.skipped_reason
    assert result.trades == tuple()
    store.close()


def test_fixed_pct_exits_and_fees_on_synthetic_book(tmp_path) -> None:
    store = DataStore(tmp_path / "lab.db")
    start = datetime(2023, 1, 1)
    # Enough history for breakout (21) + a short window.
    n = 80
    bars = []
    for i in range(n):
        # Flat then a breakout bar, then a run that hits +5% target.
        if i < 40:
            price = 100.0
        elif i == 40:
            price = 110.0  # breakout vs prior 20d high of 100
        else:
            price = 110.0 + (i - 40) * 2.0
        bars.append(
            PriceBar(
                symbol="XYZ",
                timestamp=start + timedelta(days=i),
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=50_000 if i == 40 else 5_000,
                bar_size="1 day",
            )
        )
    store.insert_price_bars(bars)

    spec = stock_spec_from_payload({
        "ticker": "XYZ",
        "signal": {"entry": "breakout"},
        "exits": {
            "atr_multiple": 2.0,
            "fixed_take_profit_pct": 0.05,
            "fixed_stop_pct": 0.03,
        },
    })
    result = run_spec_backtest(store, spec, sessions=40, equity=50_000.0)
    assert not result.skipped_reason
    assert result.bars_in_window == 40
    assert result.total_fees >= 0.0
    # At least one trade should pay two USD 1 minima when sized small enough,
    # or per-share commissions when larger — either way fees are positive if traded.
    if result.trades:
        assert result.total_fees > 0
        assert result.net_pnl == pytest.approx(
            result.gross_pnl - result.total_fees
        )
        for trade in result.trades:
            assert trade.shares >= 1
            assert trade.fees > 0
    store.close()


def test_folder_backtest_runs_enabled_specs(tmp_path) -> None:
    store = DataStore(tmp_path / "lab.db")
    _seed_bars(store, "AAA", 200, datetime(2024, 1, 1))
    stocks = tmp_path / "stocks"
    stocks.mkdir()
    (stocks / "AAA.json").write_text(
        """{
          "ticker": "AAA",
          "enabled": true,
          "signal": {"entry": "m3"},
          "exits": {"atr_multiple": 2.0, "target_r": 2.0}
        }""",
        encoding="utf-8",
    )
    (stocks / "BBB.json").write_text(
        """{
          "ticker": "BBB",
          "enabled": false,
          "signal": {"entry": "m3"},
          "exits": {"atr_multiple": 2.0}
        }""",
        encoding="utf-8",
    )
    results = run_folder_backtest(store, stocks, sessions=60)
    assert len(results) == 1
    assert results[0].ticker == "AAA"
    store.close()


def test_disabled_spec_skipped(tmp_path) -> None:
    store = DataStore(tmp_path / "lab.db")
    spec = stock_spec_from_payload({
        "ticker": "ZZZ",
        "enabled": False,
        "signal": {"entry": "trend"},
        "exits": {"atr_multiple": 2.0},
    })
    result = run_spec_backtest(store, spec)
    assert result.skipped_reason == "spec disabled"
    store.close()
