"""Tests for the scalable IBKR data layer and the long-only strategy catalogue.

No Gateway is needed: ingestion is exercised through a small fake adapter that
returns the same shapes as ``IBKRAdapter``.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from marketmind import config, strategy_catalog
from marketmind.data import DataStore
from marketmind.data.models import AccountSnapshot
from marketmind.journal import PaperJournal
from marketmind.market_data import MarketDataService, _parse_bar_date
from marketmind.universe import Universe, normalize_symbol


class FakeAdapter:
    """Minimal IBKR stand-in: contract details, daily bars, one quote."""

    def __init__(self, bars: int = 260, broken: bool = False) -> None:
        self.bars = bars
        self.broken = broken

    def get_contract_details(self, symbol):
        if self.broken:
            raise RuntimeError("no contract for this symbol")
        return {
            "con_id": 265598, "exchange": "NASDAQ", "currency": "USD",
            "multiplier": 1.0, "min_tick": 0.01,
            "long_name": f"{symbol} Inc.", "category": "Technology",
            "industry": "Software",
        }

    def fetch_historical_bars(self, symbol, duration, bar_size):
        start = datetime(2025, 1, 1)
        return [
            {
                "symbol": symbol,
                "timestamp": (start + timedelta(days=i)).strftime("%Y%m%d"),
                "open": p, "high": p + 1, "low": p - 1,
                "close": p + 0.25, "volume": 1_000 + i,
                "bar_size": bar_size, "what_to_show": "TRADES",
            }
            for i in range(self.bars)
            for p in [100 + i * 0.3 + 3 * math.sin(i / 8)]
        ]

    def request_market_data(self, symbol):
        return {
            "symbol": symbol, "con_id": 265598,
            "bid": 175.0, "ask": 175.2, "last": 175.1, "volume": 12_345,
        }


@pytest.fixture
def store(tmp_path):
    db = DataStore(tmp_path / "lab.db")
    yield db
    db.close()


@pytest.fixture
def lab(store):
    return Universe(store), MarketDataService(store)


# --- Universe ---------------------------------------------------------------

def test_universe_is_seeded_once_and_is_the_single_source(lab, store) -> None:
    universe, _ = lab
    symbols = universe.symbols()
    assert symbols == sorted(symbols)
    assert set(config.DEFAULT_UNIVERSE).issubset(set(symbols))
    assert Universe(store).symbols() == symbols, "reopening must not re-seed"


def test_add_normalizes_and_reactivates_without_duplicates(lab) -> None:
    universe, _ = lab
    assert universe.add("  ko ") == "KO"
    assert universe.add("ko") == "KO"
    assert universe.symbols().count("KO") == 1
    universe.remove("KO")
    assert "KO" not in universe.symbols()
    assert "KO" in [row["symbol"] for row in universe.all_rows()]
    universe.add("KO")
    assert "KO" in universe.symbols()


def test_invalid_symbols_are_rejected(lab) -> None:
    universe, _ = lab
    for junk in ("", "A B", "TOO-LONG-TICKER", "A/B", "$AAPL"):
        with pytest.raises(ValueError):
            universe.add(junk)
    assert normalize_symbol("brk.b") == "BRK.B"


# --- Ingestion & coverage ---------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("20261105", datetime(2026, 11, 5)),
    ("20261105 16:00:00 US/Eastern", datetime(2026, 11, 5)),
    ("2026-11-05T00:00:00", datetime(2026, 11, 5)),
    ("2026-11-05T00:00:00+00:00", datetime(2026, 11, 5)),
    ("garbage", None),
    ("", None),
])
def test_bar_date_parsing_never_fabricates_now(raw, expected) -> None:
    assert _parse_bar_date(raw) == expected


def test_sync_ingests_idempotently_and_reports_per_symbol(lab) -> None:
    universe, market = lab
    symbols = universe.symbols()[:2]
    first = market.sync(FakeAdapter(), symbols=symbols)
    assert first["ok"] == len(symbols)
    assert first["bars"] == 260 * len(symbols)

    second = market.sync(FakeAdapter(), symbols=symbols)
    assert market.summary()["bars"] == 260 * len(symbols), \
        "bars must upsert, never duplicate"

    coverage = market.coverage()
    by_symbol = {row["symbol"]: row for row in coverage}
    for symbol in symbols:
        row = by_symbol[symbol]
        assert row["status"] == "ready"
        assert row["bars"] == 260
        assert row["fresh"] is True
        assert row["last_close"] is not None
        assert row["quote"]["bid"] == 175.0
        assert row["name"] == f"{symbol} Inc."
        assert row["sector"] == "Technology"
    assert len(market.price_series(symbols[0])) == 260
    assert market.summary()["ready"] == len(symbols)
    assert len(market.sync_log()) >= 2 * len(symbols)


def test_one_bad_symbol_does_not_abort_the_batch(lab) -> None:
    universe, market = lab
    symbols = universe.symbols()[:2]
    result = market.sync(FakeAdapter(broken=True), symbols=symbols)
    assert result["ok"] == 0
    assert all(row["status"] == "error" for row in result["results"])
    assert market.coverage()[0]["status"] == "empty"


def test_manual_compliance_data_preserves_api_metadata(lab, store) -> None:
    _, market = lab
    market.sync(FakeAdapter(bars=5), symbols=["AAPL"])
    market.set_fundamental("AAPL", debt_to_market_cap=0.12)
    data = store.get_fundamental_data("AAPL")
    assert data.sector == "Technology", "API sector must survive a ratio edit"
    assert data.debt_to_market_cap == pytest.approx(0.12)
    assert data.source == "COCKPIT"
    assert market.summary()["with_fundamentals"] == 1
    assert next(a for a in strategy_catalog.SPECS
                if a.key == "impact_debt").needs_fundamentals


# --- Strategies -------------------------------------------------------------

def test_strategies_are_long_only_and_data_gated(lab, store) -> None:
    universe, market = lab
    symbols = universe.symbols()

    empty_coverage = market.coverage()
    availability = strategy_catalog.availability(empty_coverage)
    assert all(not item["ready"] for item in availability)
    assert strategy_catalog.run_all_ready(symbols, store, empty_coverage) == []

    market.sync(FakeAdapter(), symbols=symbols[:2])
    coverage = market.coverage()
    ready = [item for item in strategy_catalog.availability(coverage)
             if item["ready"]]
    assert {"trend", "breakout", "dip", "momentum"}.issubset(
        {item["key"] for item in ready}
    )
    entries = strategy_catalog.run_all_ready(symbols, store, coverage)
    assert all(s["action"] == "BUY" for s in entries)
    assert len({s["symbol"] for s in entries}) == len(entries), \
        "one strongest BUY per symbol"
    assert all(s["strategy"] != "impact_debt" for s in entries), \
        "no debt-ratio data means that screen cannot run"


def test_replay_uses_only_past_data_and_reports_forward_returns(lab, store) -> None:
    universe, market = lab
    market.sync(FakeAdapter(bars=300), symbols=["AAPL"])
    result = strategy_catalog.replay("momentum", "AAPL", store,
                                    lookback=120, horizon=5)
    assert result["supported"] is True
    assert result["evaluated"] == 120
    assert result["count"] >= 0
    assert len(result["prices"]) == 120
    for signal in result["signals"]:
        assert signal["price"] > 0
        assert signal["return_5"] is None or isinstance(signal["return_5"], float)
    if result["measured"]:
        assert 0.0 <= result["win_rate"] <= 1.0
        assert result["best"] >= result["worst"]

    fundamental = strategy_catalog.replay("impact_debt", "AAPL", store)
    assert fundamental["supported"] is False
    assert all(key in strategy_catalog.replayable_keys()
               for key in ("trend", "breakout", "dip", "momentum"))
    with pytest.raises(ValueError):
        strategy_catalog.run("unknown", ["AAPL"], store)


# --- Honest P&L -------------------------------------------------------------

def _snap(net: float, *, gross: float = 0.0, realized: float = 0.0,
          unrealized: float = 0.0, accrued: float = 0.0,
          currency: str = "EUR") -> AccountSnapshot:
    return AccountSnapshot(
        account_id="DUR251791", net_liquidation=net,
        gross_position_value=gross, realized_pnl=realized,
        unrealized_pnl=unrealized, accrued_cash=accrued, currency=currency,
    )


def test_pnl_breakdown_separates_interest_from_trading(store) -> None:
    journal = PaperJournal(store)
    journal.ensure_baseline(_snap(1_001_470.70, accrued=164.90))
    current = _snap(1_001_527.15, accrued=221.35)
    journal.record(current)
    pnl = journal.pnl_breakdown(current)

    assert pnl.currency == "EUR"
    assert pnl.total_change == pytest.approx(56.45)
    assert pnl.trading_pnl == 0.0
    assert pnl.interest_and_other == pytest.approx(56.45)
    assert pnl.accrued_cash == pytest.approx(221.35)
    assert pnl.trades_count == 0
    assert pnl.has_trading_activity is False


def test_pnl_breakdown_counts_real_trading(store) -> None:
    journal = PaperJournal(store)
    journal.ensure_baseline(_snap(1_000_000.0))
    current = _snap(1_000_100.0, gross=500.0, realized=60.0,
                    unrealized=30.0, accrued=10.0)
    journal.record(current)
    journal.record_bracket(
        "AAPL", "BUY", 1.0, 10, "MKT", None, 100.0,
        11, 105.0, 12, 97.0, "Submitted", note="test",
    )
    pnl = journal.pnl_breakdown(current)
    assert pnl.trading_pnl == pytest.approx(90.0)
    assert pnl.interest_and_other == pytest.approx(10.0)
    assert pnl.trades_count == 1
    assert pnl.has_trading_activity is True


def test_legacy_database_is_migrated_with_new_columns(tmp_path) -> None:
    """A first-MVP DB must open cleanly with the new P&L/currency columns."""
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE paper_baseline (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            recorded_at_utc TEXT NOT NULL, account_id TEXT,
            original_capital REAL NOT NULL, original_cash REAL NOT NULL,
            original_equity REAL NOT NULL, source TEXT NOT NULL DEFAULT 'IBKR'
        );
        CREATE TABLE paper_daily (
            date_utc TEXT PRIMARY KEY, net_liquidation REAL NOT NULL,
            cash REAL NOT NULL, equity REAL NOT NULL,
            pnl_vs_original REAL NOT NULL DEFAULT 0,
            orders_count INTEGER NOT NULL DEFAULT 0,
            updated_at_utc TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    journal = PaperJournal(DataStore(path))
    journal.ensure_baseline(_snap(1_000_000.0))
    journal.record(_snap(1_000_025.0, accrued=25.0))
    assert journal.baseline().currency == "EUR"
    assert journal.latest()["interest_pnl"] == pytest.approx(25.0)
    assert journal.pnl_breakdown().interest_and_other == pytest.approx(25.0)


def test_legacy_daily_rows_backfill_interest_instead_of_showing_zero(tmp_path) -> None:
    """The $56.45 case: an old row must not make the chart claim 'no interest'."""
    import sqlite3

    path = tmp_path / "legacy_with_rows.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE paper_baseline (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            recorded_at_utc TEXT NOT NULL, account_id TEXT,
            original_capital REAL NOT NULL, original_cash REAL NOT NULL,
            original_equity REAL NOT NULL, source TEXT NOT NULL DEFAULT 'IBKR'
        );
        CREATE TABLE paper_daily (
            date_utc TEXT PRIMARY KEY, net_liquidation REAL NOT NULL,
            cash REAL NOT NULL, equity REAL NOT NULL,
            pnl_vs_original REAL NOT NULL DEFAULT 0,
            orders_count INTEGER NOT NULL DEFAULT 0,
            updated_at_utc TEXT NOT NULL
        );
        INSERT INTO paper_baseline VALUES
            (1, '2026-11-05T00:00:00', 'DUR251791', 1001470.70, 1001470.70, 0, 'IBKR');
        INSERT INTO paper_daily VALUES
            ('2026-11-05', 1001470.70, 1001470.70, 0, 0, 0, '2026-11-05T23:57:00');
        INSERT INTO paper_daily VALUES
            ('2026-11-06', 1001527.15, 1001527.15, 0, 56.45, 0, '2026-11-06T15:31:00');
        """
    )
    conn.commit()
    conn.close()

    journal = PaperJournal(DataStore(path))
    rows = {row["date_utc"]: row for row in journal.daily_history()}
    assert rows["2026-11-05"]["interest_pnl"] == 0.0
    assert rows["2026-11-06"]["interest_pnl"] == pytest.approx(56.45)
    assert journal.pnl_breakdown().interest_and_other == pytest.approx(56.45)
    assert journal.pnl_breakdown().trading_pnl == 0.0

    # Migration is idempotent across restarts and must not reclassify new rows.
    # 1,001,600.00 - 1,001,470.70 = 129.30 total; 72.85 trading => 56.45 interest.
    again = PaperJournal(DataStore(path))
    again.record(_snap(1_001_600.0, gross=100.0, realized=72.85,
                       unrealized=0.0, accrued=56.45))
    assert again.latest()["interest_pnl"] == pytest.approx(56.45)
    assert again.latest()["trading_pnl"] == pytest.approx(72.85)
    third = PaperJournal(DataStore(path))
    assert third.latest()["interest_pnl"] == pytest.approx(56.45)
    assert third.latest()["trading_pnl"] == pytest.approx(72.85)


def test_legacy_baseline_currency_is_corrected_by_a_fresh_api_read(store) -> None:
    """First-MVP rows assumed USD; the account base currency is actually EUR."""
    journal = PaperJournal(store)
    journal.ensure_baseline(_snap(1_001_470.70, currency="USD"))
    assert journal.baseline().currency == "USD"

    journal.ensure_baseline(_snap(1_001_527.15, currency="EUR"))
    assert journal.baseline().currency == "EUR"
    assert journal.baseline().original_capital == pytest.approx(1_001_470.70), \
        "correcting metadata must never rewrite the immutable original money"
    assert journal.pnl_breakdown(_snap(1_001_527.15)).currency == "EUR"


def test_runner_uses_the_compliance_whitelists() -> None:
    """Impact ETFs / metals have ONE definition — a new ticker is recognised everywhere."""
    from marketmind import live_trading
    from marketmind.compliance.rules import WHITELISTED_ETFS, WHITELISTED_METALS

    assert live_trading.WHITELISTED_ETFS is WHITELISTED_ETFS
    assert live_trading.WHITELISTED_METALS is WHITELISTED_METALS
    assert not hasattr(live_trading, "_IMPACT_ETFS")
    assert not hasattr(live_trading, "_SPOT_METALS")
    assert "SPUS" in WHITELISTED_ETFS
