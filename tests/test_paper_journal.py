"""Tests for the clean paper-trading journal (no Gateway required)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketmind.data import DataStore
from marketmind.data.models import AccountSnapshot
from marketmind.journal import PaperJournal, allocation_of


def _snap(net: float, cash: float, gross: float = 0.0,
          account: str = "DUR251791") -> AccountSnapshot:
    return AccountSnapshot(
        timestamp_utc=datetime.now(timezone.utc),
        account_id=account,
        net_liquidation=net,
        total_cash_value=cash,
        cash_balance=cash,
        gross_position_value=gross,
    )


@pytest.fixture
def journal(tmp_path):
    store = DataStore(tmp_path / "journal.db")
    yield PaperJournal(store)
    store.close()


def test_allocation_cash_vs_equity(journal) -> None:
    snap = _snap(1_000_000.0, 600_000.0, gross=400_000.0)
    split = allocation_of(snap)
    assert split.cash == 600_000.0
    assert split.equity == 400_000.0
    assert split.total == 1_000_000.0
    assert split.cash_pct == pytest.approx(60.0)
    assert split.equity_pct == pytest.approx(40.0)


def test_accrued_interest_is_cash_not_equity(journal) -> None:
    """A flat account with NetLiq > TotalCashValue holds interest, not stocks."""
    split = allocation_of(_snap(1_001_470.70, 1_001_305.80))
    assert split.equity == 0.0
    assert split.cash == 1_001_470.70
    assert split.cash_pct == 100.0
    assert allocation_of(_snap(0.0, 0.0)).total == 0.0


def test_baseline_is_written_once_and_never_overwritten(journal) -> None:
    assert journal.baseline() is None
    first = journal.ensure_baseline(_snap(1_000_000.0, 1_000_000.0))
    assert first.original_capital == 1_000_000.0
    assert first.original_cash == 1_000_000.0
    assert first.account_id == "DUR251791"

    again = journal.ensure_baseline(_snap(2_000_000.0, 0.0,
                                          gross=2_000_000.0, account="OTHER"))
    assert again.original_capital == 1_000_000.0
    assert again.account_id == "DUR251791"
    assert journal.conn.execute(
        "SELECT COUNT(*) FROM paper_baseline"
    ).fetchone()[0] == 1


def test_record_upserts_one_row_per_day(journal) -> None:
    journal.ensure_baseline(_snap(1_000_000.0, 1_000_000.0))
    journal.record(_snap(1_000_100.0, 900_000.0, gross=100_100.0))
    journal.record(_snap(1_000_250.0, 900_000.0, gross=100_250.0))
    journal.record(_snap(1_000_250.0, 900_000.0, gross=100_250.0))

    history = journal.daily_history()
    assert len(history) == 1, "repeated observations must not pile up rows"
    row = history[0]
    assert row["net_liquidation"] == 1_000_250.0
    assert row["cash"] == 900_000.0
    assert row["equity"] == 100_250.0
    assert row["pnl_vs_original"] == 250.0
    assert journal.split().equity_pct == pytest.approx(
        100 * 100_250.0 / 1_000_250.0
    )


def test_record_writes_only_the_journal(journal) -> None:
    """record() runs every trading cycle; it must never spam legacy tables."""
    journal.ensure_baseline(_snap(1_000_000.0, 1_000_000.0))
    for _ in range(5):
        journal.record(_snap(1_000_000.0, 1_000_000.0))
    assert journal.conn.execute(
        "SELECT COUNT(*) FROM paper_daily"
    ).fetchone()[0] == 1
    assert journal.conn.execute(
        "SELECT COUNT(*) FROM portfolio_equity"
    ).fetchone()[0] == 0
    assert journal.conn.execute(
        "SELECT COUNT(*) FROM account_snapshots"
    ).fetchone()[0] == 0


def test_bracket_orders_are_logged_with_tp_and_sl(journal) -> None:
    journal.ensure_baseline(_snap(1_000_000.0, 1_000_000.0))
    journal.record_bracket(
        symbol="AAPL", side="BUY", quantity=1.0,
        entry_order_id=10, entry_type="MKT", entry_price=None,
        reference_price=100.0, take_profit_order_id=11,
        take_profit_price=105.0, stop_loss_order_id=12,
        stop_loss_price=97.0, status="Submitted",
        account_id="DUR251791", note="TP +5% / SL -3%",
    )
    journal.record(_snap(1_000_000.0, 999_900.0, gross=100.0))

    orders = journal.orders()
    assert len(orders) == 1
    row = orders[0]
    assert row["symbol"] == "AAPL"
    assert row["take_profit_price"] == 105.0
    assert row["stop_loss_price"] == 97.0
    assert row["entry_order_id"] == 10
    assert journal.latest()["orders_count"] == 1
    assert row["outcome"] == "open"
    assert row["exit_price"] is None


def test_blocked_orders_are_deleted(journal) -> None:
    journal.conn.execute(
        """INSERT INTO paper_orders
           (timestamp_utc, symbol, side, quantity, reference_price,
            take_profit_price, stop_loss_price, status)
           VALUES ('2026-01-01T00:00:00+00:00', 'AAPL', 'BUY', 1, 10, 11, 9,
                   'blocked')"""
    )
    journal.conn.commit()
    journal._migrate()
    assert journal.orders() == []
    assert journal.trades() == []


def test_settle_marks_take_profit_and_stop(journal) -> None:
    journal.record_bracket(
        symbol="AAPL", side="BUY", quantity=1.0,
        entry_order_id=10, entry_type="MKT", entry_price=None,
        reference_price=100.0, take_profit_order_id=11,
        take_profit_price=105.0, stop_loss_order_id=12,
        stop_loss_price=97.0, status="Submitted",
    )
    journal.record_bracket(
        symbol="MSFT", side="BUY", quantity=2.0,
        entry_order_id=20, entry_type="MKT", entry_price=50.0,
        reference_price=50.0, take_profit_order_id=21,
        take_profit_price=52.5, stop_loss_order_id=22,
        stop_loss_price=48.5, status="Submitted",
    )
    assert journal.settle([
        {"order_id": 10, "price": 101.0},
        {"order_id": 11, "price": 106.0},
    ]) == 1
    by_symbol = {row["symbol"]: row for row in journal.trades()}
    assert by_symbol["AAPL"]["outcome"] == "take_profit"
    assert by_symbol["AAPL"]["entry_price"] == 101.0
    assert by_symbol["AAPL"]["result_pnl"] == pytest.approx(5.0)
    assert by_symbol["MSFT"]["outcome"] == "open"

    assert journal.settle([{"order_id": 22, "price": 48.0}]) == 1
    by_symbol = {row["symbol"]: row for row in journal.trades()}
    assert by_symbol["MSFT"]["outcome"] == "stop_loss"
    assert by_symbol["MSFT"]["result_pnl"] == pytest.approx(-4.0)
    assert journal.settle([{"order_id": 11, "price": 106.0}]) == 0
