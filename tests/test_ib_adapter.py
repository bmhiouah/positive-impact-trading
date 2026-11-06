"""Regression tests for the fixed IBKR session and TP/SL brackets.

Unit tests run everywhere and need no Gateway. Integration tests are opt-in::

    MARKETMIND_IB_INTEGRATION=1 pytest tests/test_ib_adapter.py -v

They require IB Gateway running and logged into the paper account on port 4002.
"""

from __future__ import annotations

import inspect
import math
import os
from types import SimpleNamespace

import pytest

from marketmind import config
from marketmind.broker.ib_adapter import (
    IBKRAdapter,
    _maybe_num,
    _num,
    _pick_tag,
    bracket_prices,
    build_bracket,
    clean_legacy_order_fields,
)
from marketmind.compliance.engine import ImpactExecutionEngine
from marketmind.compliance.rules import (
    AccountType,
    AssetClass,
    OrderIntent,
    OrderSide,
)

LIVE = os.getenv("MARKETMIND_IB_INTEGRATION", "").lower() in {"1", "true", "yes"}
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _intent(limit_price=1.00):
    return OrderIntent(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        side=OrderSide.BUY,
        quantity=1.0,
        account_type=AccountType.CASH,
        sector="technology",
        debt_to_market_cap=0.10,
        is_impact_etf=False,
        is_spot_metal=False,
        limit_price=limit_price,  # far below market: rests, never fills
    )


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_session_is_fixed_and_not_configurable() -> None:
    """No host/port/client-id/capital knobs anywhere in the public API."""
    assert config.IBKR_PORT == 4002
    assert config.IBKR_CLIENT_ID == 1
    params = inspect.signature(IBKRAdapter.__init__).parameters
    assert not {"host", "port", "client_id", "account"} & set(params)
    runner_params = inspect.signature(
        __import__("marketmind.live_trading", fromlist=["LiveTradingRunner"])
        .LiveTradingRunner.__init__
    ).parameters
    assert "initial_capital" not in runner_params
    assert "client_id" not in runner_params


def test_clean_legacy_order_fields() -> None:
    order = SimpleNamespace(eTradeOnly=True, firmQuoteOnly=True,
                            nbboPriceCap=1.7976931348623157e308)
    clean_legacy_order_fields(order)
    assert order.eTradeOnly is False
    assert order.firmQuoteOnly is False
    assert order.nbboPriceCap is None


def test_bracket_prices() -> None:
    prices = bracket_prices(100.0)
    assert prices.reference == 100.0
    assert prices.take_profit == 105.0
    assert prices.stop_loss == 97.0
    assert prices.stop_loss < prices.reference < prices.take_profit
    for bad in (0.0, -1.0, float("nan"), float("inf"), "nope"):
        with pytest.raises(ValueError):
            bracket_prices(bad)
    with pytest.raises(ValueError):
        bracket_prices(100.0, take_profit_pct=-1.0)


def test_build_bracket_is_a_three_leg_bracket() -> None:
    """Parent + TP + SL with parentId links and only ONE transmit=True."""
    class FakeClient:
        def __init__(self):
            self.next_id = 10

        def getReqId(self):
            self.next_id += 1
            return self.next_id

    fake_ib = SimpleNamespace(client=FakeClient())
    prices = bracket_prices(100.0)
    parent, take_profit, stop_loss = build_bracket(
        fake_ib, "BUY", 1.0, None, prices, account="DU123"
    )
    assert parent.action == "BUY" and parent.orderType == "MKT"
    assert parent.transmit is False
    assert take_profit.action == "SELL" and take_profit.orderType == "LMT"
    assert take_profit.lmtPrice == 105.0 and take_profit.transmit is False
    assert stop_loss.action == "SELL" and stop_loss.orderType == "STP"
    assert stop_loss.auxPrice == 97.0 and stop_loss.transmit is True
    assert take_profit.parentId == parent.orderId
    assert stop_loss.parentId == parent.orderId
    assert len({parent.orderId, take_profit.orderId, stop_loss.orderId}) == 3
    for leg in (parent, take_profit, stop_loss):
        assert leg.eTradeOnly is False
        assert leg.firmQuoteOnly is False
        assert leg.nbboPriceCap is None
        assert leg.account == "DU123"
        assert leg.tif == "DAY"


def test_num_helpers_handle_ib_nan() -> None:
    assert _num("100.5") == 100.5
    assert _num(float("nan")) == 0.0
    assert _num("bad", default=7.0) == 7.0
    assert _maybe_num(float("inf")) is None
    assert _maybe_num("346.24") == 346.24


def test_pick_tag_prefers_base_currency() -> None:
    values = [
        SimpleNamespace(tag="NetLiquidation", value="100", currency="EUR"),
        SimpleNamespace(tag="NetLiquidation", value="200", currency="BASE"),
        SimpleNamespace(tag="TotalCashValue", value="90", currency="USD"),
    ]
    assert _pick_tag(values, "NetLiquidation") == 200.0
    assert _pick_tag(values, ("TotalCashValue", "$LEDGER-TotalCashBalance")) == 90.0
    assert _pick_tag(values, "MissingTag", default=3.0) == 3.0


def test_reference_price_prefers_bid_ask_mid() -> None:
    ticker = SimpleNamespace(
        bid=344.9, ask=345.1, last=float("nan"), close=float("nan"),
        marketPrice=lambda: 345.0,
    )
    assert IBKRAdapter.reference_price(ticker) == pytest.approx(345.0)
    empty = SimpleNamespace(
        bid=float("nan"), ask=float("nan"), last=float("nan"),
        close=340.0, marketPrice=lambda: float("nan"),
    )
    assert IBKRAdapter.reference_price(empty) == 340.0


def test_connect_failure_is_explicit_and_never_fake_running(monkeypatch) -> None:
    """A closed port must raise a clear error, not silently start 'dry-run'."""
    monkeypatch.setattr(config, "IBKR_PORT", 45999)
    adapter = IBKRAdapter(
        engine=ImpactExecutionEngine(auto_submit=False),
        connect_timeout=2.0,
    )
    with pytest.raises(RuntimeError) as exc:
        adapter.connect()
    message = str(exc.value)
    assert "IBKR Gateway connection failed" in message
    assert "45999" in message
    assert adapter.is_connected is False
    assert adapter.health_check() is False
    adapter.disconnect()


# ---------------------------------------------------------------------------
# Live integration tests (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LIVE, reason="set MARKETMIND_IB_INTEGRATION=1 to run against IB Gateway")
def test_live_connect_disconnect_reconnect() -> None:
    """The exact cycle the Start Algo button performs, twice in a row."""
    adapter = IBKRAdapter()
    try:
        for _ in range(2):
            info = adapter.connect()
            assert info.account, "no managed account returned"
            assert info.server_version > 0
            assert adapter.health_check() is True

            snapshot = adapter.get_account_snapshot()
            assert snapshot.net_liquidation > 0
            assert math.isfinite(snapshot.total_cash_value)

            adapter.disconnect()
            assert adapter.is_connected is False
            assert adapter.health_check() is False
    finally:
        adapter.disconnect()


@pytest.mark.skipif(not LIVE, reason="set MARKETMIND_IB_INTEGRATION=1 to run against IB Gateway")
def test_live_market_data_and_bracket_round_trip() -> None:
    """Receive data and send a safe, unfillable TP/SL bracket, then cancel it."""
    adapter = IBKRAdapter()
    try:
        adapter.connect()

        quote = adapter.request_market_data("AAPL")
        assert quote["con_id"] > 0
        assert quote["bid"] is not None and quote["ask"] is not None

        contract = adapter.get_contract_details("AAPL")
        assert contract["symbol"] == "AAPL"

        result = adapter.submit_order(_intent())
        assert result["approved"] is True
        assert result["submitted"] is True
        assert result["order_id"] > 0
        assert result["status"] in ("Submitted", "PreSubmitted", "Filled")
        assert result["stop_loss_price"] < result["reference_price"] \
               < result["take_profit_price"]
        assert result["take_profit_order_id"] != result["order_id"]
        assert result["stop_loss_order_id"] != result["order_id"]

        ids = [result["order_id"], result["take_profit_order_id"],
               result["stop_loss_order_id"]]
        assert adapter.cancel_orders(ids) >= 1
    finally:
        adapter.disconnect()


@pytest.mark.skipif(not LIVE, reason="set MARKETMIND_IB_INTEGRATION=1 to run against IB Gateway")
def test_live_runner_start_stop() -> None:
    """Start Algo connects before trading; capital comes from the API."""
    import time

    from marketmind.live_trading import LiveTradingRunner

    runner = LiveTradingRunner(
        db_path=config.DB_PATH,
        poll_interval=3600,
    )
    try:
        info = runner.start()
        assert info["connected"] is True
        assert info["net_liquidation"] > 0
        assert info["original_capital"] > 0
        assert runner.is_running() is True
        assert runner.is_connected() is True
        assert runner.journal.baseline() is not None
        deadline = time.time() + 15
        while runner.cycle_count < 1 and time.time() < deadline:
            time.sleep(0.2)
        assert runner.cycle_count >= 1, "first trading cycle never ran"
        assert runner.last_result is not None
    finally:
        runner.stop()
    assert runner.is_running() is False
    assert runner.is_connected() is False
