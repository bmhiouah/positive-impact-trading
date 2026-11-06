#!/usr/bin/env python3
"""Standalone IBKR connection probe for the paper-trading lab.

No connection settings: the fixed session constants come from
``marketmind/config.py`` (127.0.0.1:4002, client id 1).

Run with the same interpreter the cockpit uses:

    /Users/badremhiouah/miniconda3/envs/py314/bin/python scripts/ib_connection_probe.py
    ... --all     # also test data + a full TP/SL bracket round trip

Stages
------
1. connect     - open the API socket and complete the IB handshake
2. disconnect  - close it *gracefully* (this frees client id 1)
3. reconnect   - open it again with the SAME client id, proving the daily
                 start/stop cycle works (the "Start Algo" use case)

--data     account values, historical bars, delayed quote
--order    place a deliberately unfillable 1-share bracket (entry + TP + SL),
           verify IB acknowledges it, then cancel all three legs. Nothing bought.
--all      both.

Exit code 0 = every requested stage passed, 1 = at least one failed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import sys

# ---------------------------------------------------------------------------
# Python 3.14: eventkit (an ib_insync dependency) calls get_event_loop() at
# import time and raises if the importing thread has no event loop. Creating
# and setting the loop BEFORE importing ib_insync is mandatory.
# ---------------------------------------------------------------------------
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from marketmind import config  # noqa: E402  (pure constants, no ib_insync)

from ib_insync import IB, LimitOrder, StopOrder, Stock, util  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
util.logToConsole(logging.WARNING)

DEFAULT_SYMBOL = "AAPL"


class ProbeError(RuntimeError):
    """A probe stage failed."""


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _info(msg: str) -> None:
    print(f"  [INFO] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def disconnect_now(ib: IB) -> None:
    """Close the transport immediately (synchronous, loop-safe)."""
    try:
        ib.disconnect()
    finally:
        # Avoid IB.__del__ trying to close a transport after the loop is gone.
        ib.client.conn = None


async def connect(timeout: float) -> IB:
    """Open the fixed paper session and complete the handshake on this loop."""
    ib = IB()
    errors: list[tuple[int, str]] = []

    def on_error(_req_id, code, message, _contract):
        errors.append((int(code), str(message)))

    ib.errorEvent += on_error
    try:
        await asyncio.wait_for(
            ib.connectAsync(config.IBKR_HOST, config.IBKR_PORT,
                            clientId=config.IBKR_CLIENT_ID,
                            timeout=timeout, readonly=False),
            timeout=timeout + 5,
        )
    except Exception as exc:  # noqa: BLE001
        disconnect_now(ib)
        detail = "; ".join(f"{c}: {m}" for c, m in errors[:3])
        raise ProbeError(
            f"handshake failed ({type(exc).__name__}: {exc}). {detail}"
        ) from exc
    finally:
        ib.errorEvent -= on_error

    if not ib.isConnected():
        disconnect_now(ib)
        raise ProbeError("socket closed immediately after handshake")
    for code, message in errors:
        if code in (326, 502, 504, 1100, 2110):
            disconnect_now(ib)
            raise ProbeError(
                f"IB rejected the session ({code}: {message}). "
                "Close the other client — only one connection is allowed."
            )

    ib.reqMarketDataType(config.MARKET_DATA_TYPE)
    _ok(f"connected: serverVersion={ib.client.serverVersion()} "
        f"clientId={ib.client.clientId} accounts={ib.managedAccounts()}")
    return ib


async def disconnect(ib: IB) -> None:
    """Gracefully close the socket and let asyncio process connection_lost."""
    if ib is None:
        return
    disconnect_now(ib)
    await asyncio.sleep(0.25)
    _ok(f"disconnected: isConnected={ib.isConnected()}")


def _tag(values, tag: str):
    matches = [v for v in values if v.tag == tag]
    for currency in ("BASE", "USD", "EUR", ""):
        for v in matches:
            if v.currency == currency:
                return v.value
    return matches[0].value if matches else None


def show_account(ib: IB) -> None:
    """Read account values synchronised during connectAsync()."""
    values = ib.accountValues()
    netliq = _tag(values, "NetLiquidation") or _tag(values, "$LEDGER-NetLiquidationByCurrency")
    cash = _tag(values, "TotalCashValue") or _tag(values, "$LEDGER-TotalCashBalance")
    if netliq is None:
        raise ProbeError("connected, but no account values were received")
    _ok(f"account data: NetLiquidation={netliq} TotalCashValue={cash} "
        f"({len(values)} value tags)")


REJECT_CODES = {
    201: "order rejected", 202: "order cancelled", 203: "security not available",
    321: "validation error", 404: "short sale / position constraint",
    10268: "unsupported legacy order attribute",
}


def clean_legacy_order_fields(order) -> None:
    """ib_insync 0.9.50 defaults eTradeOnly=True; Gateway >= 145 rejects it."""
    order.eTradeOnly = False
    order.firmQuoteOnly = False
    order.nbboPriceCap = None


async def _snapshot_ticker(ib: IB, contract):
    # snapshot=True ends the subscription itself; cancelling it afterwards
    # would only produce a spurious IB error 300.
    ticker = ib.reqMktData(contract, "", True, False)
    for _ in range(25):
        await asyncio.sleep(0.2)
        if ticker.marketPrice() and math.isfinite(ticker.marketPrice()) \
                and ticker.marketPrice() > 0:
            break
    return ticker


def _reference_price(ticker) -> float:
    bid, ask = ticker.bid, ticker.ask
    if bid and ask and math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    for value in (ticker.last, ticker.close, ticker.marketPrice()):
        if value and math.isfinite(value) and value > 0:
            return float(value)
    return 0.0


async def _receive_data(ib: IB, symbol: str) -> None:
    """Receive historical bars + a delayed quote on the live connection."""
    contract = Stock(symbol, "SMART", "USD")
    qualified = await ib.qualifyContractsAsync(contract)
    if not qualified:
        raise ProbeError(f"could not qualify contract {symbol}")
    _ok(f"contract qualified: {contract.symbol} conId={contract.conId} "
        f"exchange={contract.exchange}")

    close = 0.0
    try:
        bars = await ib.reqHistoricalDataAsync(
            contract, endDateTime="", durationStr="5 D", barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=1,
        )
        if bars:
            close = float(bars[-1].close)
            _ok(f"historical bars: {len(bars)} received, last close={close}")
        else:
            _warn("historical bars: empty response")
    except Exception as exc:  # noqa: BLE001
        _warn(f"historical bars unavailable: {type(exc).__name__}: {exc}")

    ticker = await _snapshot_ticker(ib, contract)
    price = _reference_price(ticker)
    if price > 0:
        _ok(f"market quote: bid={ticker.bid} ask={ticker.ask} "
            f"reference={price:.2f}")
    else:
        _warn("quote: no prices (delayed data not enabled / market closed?)")
    if not close and not price:
        raise ProbeError("no price data received from IBKR")


async def _bracket_round_trip(ib: IB, symbol: str) -> None:
    """Place an unfillable bracket, verify acknowledgement, cancel all 3 legs.

    The entry limit sits 50% below the reference price, so it rests on the book
    and never fills. TP/SL are anchored to the reference price, exactly like
    the trading bot does. No position is opened.
    """
    contract = Stock(symbol, "SMART", "USD")
    qualified = await ib.qualifyContractsAsync(contract)
    if not qualified:
        raise ProbeError(f"could not qualify contract {symbol}")

    ticker = await _snapshot_ticker(ib, contract)
    reference = _reference_price(ticker)
    if reference <= 0:
        raise ProbeError("cannot build TP/SL without a reference price")

    entry_price = round(max(0.01, reference * 0.50), 2)
    tp_price = round(reference * (1 + config.TAKE_PROFIT_PCT), 2)
    sl_price = round(reference * (1 - config.STOP_LOSS_PCT), 2)

    parent_id = ib.client.getReqId()
    parent = LimitOrder("BUY", 1, entry_price, orderId=parent_id,
                        transmit=False)
    take_profit = LimitOrder("SELL", 1, tp_price,
                             orderId=ib.client.getReqId(),
                             parentId=parent_id, transmit=False)
    stop_loss = StopOrder("SELL", 1, sl_price,
                          orderId=ib.client.getReqId(),
                          parentId=parent_id, transmit=True)
    for leg in (parent, take_profit, stop_loss):
        clean_legacy_order_fields(leg)
        leg.tif = "DAY"
        leg.outsideRth = False

    errors: list[tuple[int, str]] = []

    def on_error(_req_id, code, message, _contract):
        errors.append((int(code), str(message)))

    ib.errorEvent += on_error
    trades = []
    try:
        for leg in (parent, take_profit, stop_loss):
            trades.append(ib.placeOrder(contract, leg))
        _ok(f"BRACKET SENT: entry id={parent.orderId} BUY 1 {symbol} "
            f"LMT {entry_price} · TP {tp_price} (id={take_profit.orderId}) "
            f"· SL {sl_price} (id={stop_loss.orderId}) · ref={reference:.2f}")

        accepted = {"Submitted", "PreSubmitted", "Filled"}
        status = ""
        for _ in range(50):  # up to ~10 seconds
            await asyncio.sleep(0.2)
            status = trades[0].orderStatus.status
            if status in accepted or status in {"Cancelled", "Inactive", "ApiCancelled"}:
                break
        for code, message in errors:
            if code in REJECT_CODES:
                raise ProbeError(f"IB rejected the bracket ({code} "
                                 f"{REJECT_CODES[code]}: {message})")
        if status not in accepted:
            raise ProbeError(f"bracket NOT accepted by IB (status={status!r})")
        _ok(f"BRACKET ACCEPTED by IB: status={status} "
            f"permId={trades[0].order.permId}")
    finally:
        for trade in trades:
            if not trade.isDone():
                ib.cancelOrder(trade.order)
        for _ in range(25):
            await asyncio.sleep(0.2)
            if all(t.isDone() for t in trades):
                break
        _ok(f"BRACKET CANCELLED: {sum(t.isDone() for t in trades)}/3 legs done")
        ib.errorEvent -= on_error


async def main(args: argparse.Namespace) -> int:
    failures = 0
    print("\n=== IBKR connection probe (paper lab) ===")
    print(f"  target  : {config.IBKR_HOST}:{config.IBKR_PORT} "
          f"(client id {config.IBKR_CLIENT_ID}, fixed)")
    print(f"  extras  : data={args.data or args.all} order={args.order or args.all}\n")

    print("[1/3] CONNECT")
    try:
        ib = await connect(args.timeout)
        show_account(ib)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
        return 1

    print("\n[2/3] DISCONNECT (graceful)")
    try:
        await disconnect(ib)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
        failures += 1

    print("\n[3/3] RECONNECT with the same client id")
    try:
        ib = await connect(args.timeout)
        show_account(ib)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
        print("\nRESULT: FAILED — a fresh start/stop cycle does not work.")
        return 1

    try:
        if args.data or args.all:
            print("\n[extra] RECEIVING DATA")
            try:
                await _receive_data(ib, args.symbol)
            except Exception as exc:  # noqa: BLE001
                _fail(str(exc))
                failures += 1

        if args.order or args.all:
            print("\n[extra] TP/SL BRACKET ROUND-TRIP (place -> accept -> cancel)")
            try:
                await _bracket_round_trip(ib, args.symbol)
            except Exception as exc:  # noqa: BLE001
                _fail(str(exc))
                failures += 1
    finally:
        print("\n[cleanup] DISCONNECT")
        try:
            await disconnect(ib)
        except Exception as exc:  # noqa: BLE001
            _fail(str(exc))
            failures += 1

    print("\nRESULT: " + ("PASSED — connect / disconnect / reconnect works."
                          if not failures else
                          f"FAILED — {failures} optional stage(s) failed."))
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--data", action="store_true", help="test receiving market/account data")
    parser.add_argument("--order", action="store_true", help="test a TP/SL bracket place + cancel")
    parser.add_argument("--all", action="store_true", help="test data AND brackets")
    parsed = parser.parse_args()

    try:
        code = LOOP.run_until_complete(main(parsed))
    except KeyboardInterrupt:
        code = 130
    finally:
        LOOP.run_until_complete(LOOP.shutdown_asyncgens())
        LOOP.close()
    sys.exit(code)
