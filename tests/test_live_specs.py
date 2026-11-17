"""File-driven specs wiring for the live runner (no Gateway)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pytest

from marketmind import contest
from marketmind.data import DataStore
from marketmind.data.models import PriceBar
from marketmind.live_specs import load_live_specs, rule_and_exits, trading_symbols
from marketmind.stock_specs.loader import (
    default_stocks_dir,
    stock_spec_from_payload,
)
from marketmind.universe import Universe


def test_load_live_specs_includes_eur_names() -> None:
    specs, by_ticker = load_live_specs(default_stocks_dir())
    assert "AAPL" in by_ticker
    assert by_ticker["AIR"].currency == "EUR"
    assert by_ticker["ASML"].currency == "EUR"
    assert all(s.enabled for s in specs)


def test_trading_symbols_prefer_specs() -> None:
    specs = [
        stock_spec_from_payload({
            "ticker": "AAA",
            "signal": {"entry": "m12"},
            "exits": {"atr_multiple": 2.0},
        })
    ]
    assert trading_symbols(specs, ["X", "Y"]) == ["AAA"]
    assert trading_symbols([], ["X", "Y"]) == ["X", "Y"]


def test_rule_and_exits_from_spec_overrides_contest() -> None:
    spec = stock_spec_from_payload({
        "ticker": "NVDA",
        "signal": {"entry": "m6"},
        "exits": {
            "atr_multiple": 2.5,
            "target_r": 2.0,
            "fixed_take_profit_pct": 0.05,
            "fixed_stop_pct": 0.03,
        },
    })
    rule, fixed_sl, fixed_tp = rule_and_exits(
        "NVDA", {"NVDA": spec}, contest.INCUMBENT,
    )
    assert rule.entry == "m6"
    assert rule.atr_multiple == pytest.approx(2.5)
    assert rule.target_r == pytest.approx(2.0)
    assert fixed_sl == pytest.approx(0.03)
    assert fixed_tp == pytest.approx(0.05)
    # Unknown ticker keeps contest rule.
    rule2, sl2, tp2 = rule_and_exits("ZZZ", {"NVDA": spec}, contest.INCUMBENT)
    assert rule2 == contest.INCUMBENT
    assert sl2 is None and tp2 is None


def test_read_symbol_fixed_pct_exits() -> None:
    n = 30
    close = np.full(n, 100.0)
    book = {
        "open": close.copy(),
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": np.full(n, 1_000.0),
        "dates": [f"2024-06-{(i % 28) + 1:02d}" for i in range(n)],
    }
    # Force a known completed session index by using a day after the last bar.
    session = date(2024, 7, 1)
    # Make breakout buy true on last bar: need prior high + volume.
    book["close"][-1] = 120.0
    book["high"][-1] = 121.0
    book["volume"][-1] = 50_000.0
    rule = contest.Rule("breakout", 2.0, None, None)
    reading = contest.read_symbol(
        book, rule, session,
        fixed_stop_pct=0.03,
        fixed_take_profit_pct=0.05,
    )
    assert reading is not None
    assert reading.buy is True
    assert reading.stop == pytest.approx(116.4)  # 120 * 0.97
    assert reading.target == pytest.approx(126.0)  # 120 * 1.05


def test_universe_ensure_from_specs_stamps_currency(tmp_path) -> None:
    store = DataStore(tmp_path / "lab.db")
    universe = Universe(store)
    specs = [
        stock_spec_from_payload({
            "ticker": "AIR",
            "currency": "EUR",
            "exchange": "SMART",
            "signal": {"entry": "m12"},
            "exits": {"atr_multiple": 2.0},
        })
    ]
    enabled = universe.ensure_from_specs(specs)
    assert enabled == ["AIR"]
    rows = universe.listing_rows()
    assert rows["AIR"]["currency"] == "EUR"
    store.close()


def test_runner_reload_specs_sets_eur_listing(tmp_path, monkeypatch) -> None:
    """LiveTradingRunner stamps adapter listings from stocks/ without Gateway."""
    from marketmind.live_trading import LiveTradingRunner

    stocks = tmp_path / "stocks"
    stocks.mkdir()
    (stocks / "AIR.json").write_text(
        """{
          "ticker": "AIR",
          "enabled": true,
          "currency": "EUR",
          "exchange": "SMART",
          "signal": {"entry": "m12"},
          "exits": {"atr_multiple": 2.0}
        }""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "marketmind.live_specs.default_stocks_dir", lambda: stocks,
    )
    monkeypatch.setattr(
        "marketmind.stock_specs.loader.default_stocks_dir", lambda: stocks,
    )
    runner = LiveTradingRunner(db_path=str(tmp_path / "lab.db"))
    symbols = runner._reload_specs()
    assert symbols == ["AIR"]
    listing = runner._adapter.listing_for("AIR")
    assert listing.currency == "EUR"
    runner._store.close()
