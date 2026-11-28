"""Unit tests for cockpit-facing IB Gateway error messages (no Gateway)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from marketmind import ib_compat  # noqa: F401 - before ib_insync imports
from marketmind.broker.ib_adapter import format_gateway_error
from marketmind.cockpit.state import CockpitState, refresh_snapshot
from marketmind.data.models import AccountSnapshot


def test_format_empty_timeout_is_actionable() -> None:
    message = format_gateway_error(TimeoutError())
    assert "timed out" in message.lower()
    assert "4002" in message
    assert ": ." not in message


def test_format_connection_refused_is_actionable() -> None:
    message = format_gateway_error(
        ConnectionRefusedError(61, "Connection refused")
    )
    assert "not accepting connections" in message
    assert "4002" in message
    assert "paper" in message.lower()


def test_format_wrapped_refused_follows_cause() -> None:
    inner = ConnectionRefusedError(61, "Connection refused")
    outer = RuntimeError(
        "handshake failed (ConnectionRefusedError: "
        "[Errno 61] Connection refused)."
    )
    outer.__cause__ = inner
    message = format_gateway_error(outer)
    assert "not accepting connections" in message


def test_format_client_id_busy() -> None:
    message = format_gateway_error(
        RuntimeError(
            "handshake failed (TimeoutError: ). IB error: 326: "
            "Unable to connect as the client id is already in use."
        )
    )
    assert "client id 1 is already in use" in message
    assert "Read account" in message


def test_format_does_not_double_wrap_clean_message() -> None:
    clean = (
        "IB Gateway is not accepting connections on 127.0.0.1:4002. "
        "Open IB Gateway, log into the paper account, wait until the API "
        "socket is listening, then click Read account again."
    )
    assert format_gateway_error(RuntimeError(clean)) == clean


def test_refresh_snapshot_clears_stale_errors(monkeypatch) -> None:
    """A successful Read Account must not leave a prior Start Algo error on screen."""
    state = CockpitState(
        ibkr_last_error="stale gateway failure",
        algo_last_error="stale cycle failure",
        refresh_error="old refresh failure",
    )

    class FakeAdapter:
        def connect(self):
            return SimpleNamespace(account="DUR251791", server_version=151)

        def get_account_snapshot(self):
            return AccountSnapshot(
                timestamp_utc=datetime.now(timezone.utc),
                account_id="DUR251791",
                source="IBKR",
                net_liquidation=1_000_000.0,
                total_cash_value=1_000_000.0,
                cash_balance=1_000_000.0,
                buying_power=1_000_000.0,
                available_funds=1_000_000.0,
                excess_liquidity=1_000_000.0,
                gross_position_value=0.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
                total_positions_value=0.0,
                accrued_cash=0.0,
                currency="EUR",
            )

        def disconnect(self):
            return None

        def executions_since(self, _since):
            return []

    class FakeJournal:
        def ensure_baseline(self, snapshot):
            return SimpleNamespace(
                recorded_at_utc=snapshot.timestamp_utc.isoformat(),
                account_id=snapshot.account_id,
                original_capital=snapshot.net_liquidation,
            )

        def record(self, _snapshot):
            return None

        def oldest_open_at(self):
            return None

    monkeypatch.setattr("marketmind.cockpit.state.get_state", lambda: state)
    monkeypatch.setattr("marketmind.cockpit.state.get_journal", lambda: FakeJournal())
    monkeypatch.setattr("marketmind.broker.IBKRAdapter", FakeAdapter)

    assert refresh_snapshot() is True
    assert state.refresh_error is None
    assert state.ibkr_last_error is None
    assert state.algo_last_error is None
    assert state.ibkr_account == "DUR251791"
    assert state.live_snapshot is not None


def test_refresh_snapshot_formats_gateway_down(monkeypatch) -> None:
    state = CockpitState()

    class DownAdapter:
        def connect(self):
            raise ConnectionRefusedError(61, "Connection refused")

        def disconnect(self):
            return None

    monkeypatch.setattr("marketmind.cockpit.state.get_state", lambda: state)
    monkeypatch.setattr("marketmind.broker.IBKRAdapter", DownAdapter)

    assert refresh_snapshot() is False
    assert state.refresh_error is not None
    assert "not accepting connections" in state.refresh_error
    assert "4002" in state.refresh_error
    assert ": ." not in state.refresh_error
