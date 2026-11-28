"""Live paper-trading runner for the one momentum book.

Lifecycle (this is what the cockpit's "Start Algo" button triggers):

    1. connect to the one IB Gateway paper session  <- mandatory, fails loudly
    2. read the account snapshot via the API         <- capital comes from HERE
    3. record the original paper money once          <- the journal baseline
    4. only then start the trading thread            <- one rule, once a day

The runner is fully synchronous and thread-based. Streamlit executes scripts
on a thread with no persistent asyncio loop, so scheduling the bot as an
``asyncio`` task there meant the loop stopped as soon as the script rerun
finished. The IB adapter owns its own event loop internally; the runner simply
calls its thread-safe synchronous methods from a dedicated background thread.

The thread checks the connection and reads fills on every poll. The buy, the
trailing stop and the exit run once per New York session, on the last
completed daily bar. A long entry is a market buy plus a GTC stop. There is
no take-profit. Stopping the algo leaves that stop in place.
"""

from __future__ import annotations

# Ensure an event loop exists before ib_insync is imported (Python 3.14).
from marketmind import ib_compat  # noqa: F401

import logging
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from marketmind import config
from marketmind.broker import IBKRAdapter
from marketmind.compliance import (
    AccountType,
    AssetClass,
    ImpactExecutionEngine,
    OrderIntent,
    OrderSide,
)
from marketmind.compliance.rules import WHITELISTED_ETFS, WHITELISTED_METALS
from marketmind.data import DataStore
from marketmind.journal import PaperJournal
from marketmind.live_specs import load_live_specs, rule_and_exits, trading_symbols
from marketmind.market_data import MarketDataService
from marketmind.stock_specs.loader import StockSpec
from marketmind.universe import Universe
from marketmind import contest, momentum

logger = logging.getLogger(__name__)


class LiveTradingRunner:
    """Runs the 12-month momentum book and keeps its stops trailed."""

    def __init__(
        self,
        db_path: str = config.DB_PATH,
        poll_interval: float = config.POLL_INTERVAL,
        cancel_orders_on_stop: bool = False,
    ) -> None:
        self._db_path = str(db_path)
        self._poll_interval = max(1.0, float(poll_interval))
        # Brackets are the risk management. Cancelling them on stop would leave
        # open positions unprotected, so this defaults to False.
        self._cancel_orders_on_stop = cancel_orders_on_stop
        self._portfolio_value = 0.0   # set from the API on start()

        self._adapter = IBKRAdapter(
            engine=ImpactExecutionEngine(auto_submit=False),
        )
        self._store = DataStore(self._db_path)
        self._journal = PaperJournal(self._store)
        self._universe = Universe(self._store)
        self._market = MarketDataService(self._store, self._universe)

        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None
        self._cycle_count = 0
        self._last_cycle_at: Optional[str] = None
        self._last_data_sync = 0.0
        self._last_decision_day: Optional[str] = None
        self._last_sync_day: Optional[str] = None
        self._live_rule = contest.INCUMBENT
        self._specs: List[StockSpec] = []
        self._specs_by_ticker: Dict[str, StockSpec] = {}

    # ------------------------------------------------------------------
    # Lifecycle — connection first, trading second
    # ------------------------------------------------------------------

    def start(self) -> Dict[str, Any]:
        """Connect, read capital from the API, seed the journal, then trade."""
        logger.info("Starting LiveTradingRunner — connecting to IBKR Gateway")
        self.stop()  # clean any previous run so no stale session survives

        try:
            info = self._adapter.connect()
            snapshot = self._adapter.get_account_snapshot()
        except Exception as exc:  # noqa: BLE001
            self._adapter.disconnect()
            raise RuntimeError(
                f"Cannot start LiveTradingRunner: {exc}. "
                "The algo will NOT start without a valid IBKR Gateway connection. "
                f"Check: Gateway running & logged into the paper account on "
                f"{config.IBKR_HOST}:{config.IBKR_PORT}, API sockets enabled, "
                "and no other client using client id 1."
            ) from exc

        # Capital is READ FROM THE API — never a parameter.
        self._portfolio_value = float(snapshot.net_liquidation)
        baseline = self._journal.ensure_baseline(snapshot)
        self._journal.record(snapshot)
        # File-driven universe + currency-aware IB listings before the first sync.
        trade_symbols = self._reload_specs()
        logger.info(
            "Trading universe from %s: %s",
            "stocks/" if self._specs else "instruments",
            ", ".join(trade_symbols) or "(empty)",
        )

        with self._lock:
            self._running = True
            self._stop_event.clear()
            self._last_error = None
            self._cycle_count = 0
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="marketmind-bot"
            )
            self._thread.start()

        logger.info("IBKR account %s — algo started (original paper money %.2f)",
                    info.account, baseline.original_capital)
        return {
            "connected": True,
            "account": info.account,
            "server_version": info.server_version,
            "net_liquidation": snapshot.net_liquidation,
            "original_capital": baseline.original_capital,
            "baseline_recorded_at": baseline.recorded_at_utc,
            "currency": snapshot.currency,
        }

    async def start_async(self) -> Dict[str, Any]:
        """Async compatibility wrapper (uses a worker thread internally)."""
        import asyncio
        return await asyncio.to_thread(self.start)

    def _run_loop(self) -> None:
        """Trading thread: one cycle immediately, then every poll_interval."""
        while self._running:
            if not self._ensure_connection():
                if self._stop_event.wait(self._poll_interval):
                    break
                continue
            try:
                result = self._run_trading_cycle()
                with self._lock:
                    self._last_result = result
                    self._last_error = None
                    self._cycle_count += 1
                    self._last_cycle_at = datetime.now(timezone.utc).isoformat()
                logger.info("Trading cycle finished: %s", result)
            except Exception as exc:  # noqa: BLE001
                logger.error("Error in trading cycle: %s", exc)
                with self._lock:
                    self._last_error = str(exc)
            if self._stop_event.wait(self._poll_interval):
                break

    def _ensure_connection(self) -> bool:
        """Reconnect automatically if the Gateway dropped the session."""
        if self._adapter.health_check():
            return True
        logger.warning("IBKR session lost — attempting to reconnect")
        try:
            self._adapter.reconnect()
            return True
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"IBKR reconnect failed: {exc}"
            logger.error("IBKR reconnect failed: %s", exc)
            return False

    def stop(self) -> None:
        """Stop the trading thread and gracefully disconnect from IBKR."""
        self._running = False
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10.0)
        self._thread = None
        try:
            if self._cancel_orders_on_stop and self._adapter.is_connected:
                cancelled = self._adapter.cancel_all_orders()
                if cancelled:
                    logger.info("Cancelled %s open order(s) on stop", cancelled)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not cancel open orders: %s", exc)
        try:
            self._adapter.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Disconnect note: %s", exc)
        logger.info("LiveTradingRunner stopped")

    async def stop_async(self) -> None:
        """Async compatibility wrapper."""
        import asyncio
        await asyncio.to_thread(self.stop)

    # ------------------------------------------------------------------
    # Status (read by the cockpit on every rerun)
    # ------------------------------------------------------------------

    @property
    def adapter(self) -> IBKRAdapter:
        return self._adapter

    @property
    def journal(self) -> PaperJournal:
        return self._journal

    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def is_connected(self) -> bool:
        return self._adapter.health_check()

    @property
    def last_result(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._last_result

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    @property
    def cycle_count(self) -> int:
        with self._lock:
            return self._cycle_count

    @property
    def last_cycle_at(self) -> Optional[str]:
        with self._lock:
            return self._last_cycle_at

    # ------------------------------------------------------------------
    # Core trading cycle (all calls are synchronous and thread-safe)
    # ------------------------------------------------------------------

    def _active_symbols(self) -> set[str]:
        """Symbols that already have a position or a resting bracket."""
        active: set[str] = set()
        try:
            for position in self._adapter.get_positions():
                if position["position"] > 0:
                    active.add(position["symbol"])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not read positions: %s", exc)
        try:
            for order in self._adapter.get_open_orders():
                active.add(order["symbol"])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not read open orders: %s", exc)
        return active

    def _reload_specs(self) -> List[str]:
        """Load ``stocks/``, stamp listings, ensure instruments rows exist.

        Returns the symbols the algo will trade (enabled specs, or the full
        instruments table when the folder is empty/missing).
        """
        specs, by_ticker = load_live_specs()
        self._specs = list(specs)
        self._specs_by_ticker = dict(by_ticker)
        if specs:
            self._universe.ensure_from_specs(specs)
        # Instrument meta first, then specs overwrite — EUR names win when filed.
        self._adapter.listings.clear()
        self._adapter.listings.load_mappings(self._universe.listing_rows())
        if specs:
            self._adapter.listings.load_specs(specs)
        return trading_symbols(specs, self._universe.symbols())

    def _trade_symbols(self) -> List[str]:
        """Current trading universe (file-driven when specs exist)."""
        if self._specs:
            return [spec.ticker for spec in self._specs]
        return self._universe.symbols()

    def _maybe_sync_data(self, force: bool = False) -> None:
        """Refresh the universe through the ALREADY OPEN session.

        A second connection is forbidden, so the runner reuses its own adapter
        instead of opening a new client id 1 session. A cooldown prevents a
        symbol IBKR cannot serve from being retried every 60 seconds. The
        first pass of a new session ignores that cooldown so the decision sees
        the latest completed bar.
        """
        symbols = self._reload_specs()
        now = time.monotonic()
        coverage = self._market.coverage()
        short = any(
            int(row.get("bars") or 0) < config.MIN_BARS_FOR_SIGNALS
            for row in coverage
            if not symbols or row.get("symbol") in symbols
        )
        relevant = [row for row in coverage
                    if not symbols or row.get("symbol") in symbols]
        fresh = bool(relevant) and not short and all(
            row["status"] == "ready" for row in relevant
        )
        if fresh and not force:
            self._last_data_sync = now
            return
        if not force and now - self._last_data_sync < config.DATA_SYNC_COOLDOWN:
            return
        logger.info("Refreshing IBKR market data for %s instruments",
                    len(symbols))
        self._market.sync(self._adapter, symbols=symbols or None)
        self._last_data_sync = now

    def _settle_open_brackets(self) -> None:
        """Read fills and close any bracket whose take-profit or stop filled."""
        since_text = self._journal.oldest_open_at()
        if not since_text:
            return
        try:
            since = datetime.fromisoformat(since_text)
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            fills = self._adapter.executions_since(since)
            closed = self._journal.settle(fills)
            if closed:
                logger.info("Closed %s bracket(s) from IBKR fills", closed)
        except Exception as exc:  # noqa: BLE001 - a fill read must not stop the cycle
            logger.warning("Could not update open brackets: %s", exc)

    def _intent(self, symbol: str, side: OrderSide, quantity: float, *,
                stop_price: Optional[float] = None,
                target_price: Optional[float] = None) -> OrderIntent:
        """Build an order the compliance gate can judge.

        The whitelists live in the compliance module. A new impact ETF has to
        be recognised here and at the gate from that one list.
        """
        is_impact_etf = symbol in WHITELISTED_ETFS
        is_spot_metal = symbol in WHITELISTED_METALS
        asset_class = (AssetClass.ETF if is_impact_etf
                       else AssetClass.SPOT_METAL if is_spot_metal
                       else AssetClass.EQUITY)
        fund = self._store.get_fundamental_data(symbol)
        return OrderIntent(
            symbol=symbol,
            asset_class=asset_class,
            side=side,
            quantity=float(quantity),
            account_type=AccountType.CASH,
            sector=fund.sector if fund else None,
            debt_to_market_cap=(fund.debt_to_market_cap if fund else None),
            is_impact_etf=is_impact_etf,
            is_spot_metal=is_spot_metal,
            stop_price=stop_price,
            target_price=target_price,
        )

    def _positions(self) -> Dict[str, Dict[str, Any]]:
        held: Dict[str, Dict[str, Any]] = {}
        try:
            for position in self._adapter.get_positions():
                if position["position"] > 0:
                    held[position["symbol"]] = position
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not read positions: %s", exc)
        return held

    def _enter(self, signal: Dict[str, Any], snap, quantity: int) -> str:
        """Send one buy plus its stop. Returns submitted, blocked, skipped or error."""
        symbol = signal["symbol"]
        intent = self._intent(symbol, OrderSide.BUY, quantity,
                              stop_price=float(signal["stop"]),
                              target_price=signal.get("target"))
        try:
            result = self._adapter.submit_order(intent)
        except Exception as exc:  # noqa: BLE001
            self._journal.record_bracket(
                symbol=symbol, side="BUY", quantity=quantity,
                entry_order_id=None, entry_type="MKT",
                entry_price=None, reference_price=float(signal["price"]),
                take_profit_order_id=None, take_profit_price=None,
                stop_loss_order_id=None, stop_loss_price=float(signal["stop"]),
                status="error", account_id=snap.account_id, note=str(exc),
            )
            logger.error("Entry failed for %s: %s", symbol, exc)
            return "error"
        if not result.get("approved"):
            reason = result.get("rejection_reason") or "compliance blocked"
            self._journal.record_bracket(
                symbol=symbol, side="BUY", quantity=quantity,
                entry_order_id=None, entry_type="MKT",
                entry_price=None, reference_price=float(signal["price"]),
                take_profit_order_id=None, take_profit_price=None,
                stop_loss_order_id=None, stop_loss_price=float(signal["stop"]),
                status="blocked", account_id=snap.account_id, note=str(reason),
            )
            logger.info("Not sent for %s: %s", symbol, reason)
            return "blocked"
        if not result.get("submitted"):
            return "skipped"
        self._journal.record_bracket(
            symbol=symbol, side="BUY", quantity=quantity,
            entry_order_id=result.get("order_id"),
            entry_type=result.get("entry_type", "MKT"),
            entry_price=result.get("entry_price"),
            reference_price=result["reference_price"],
            take_profit_order_id=result.get("take_profit_order_id"),
            take_profit_price=result.get("take_profit_price"),
            stop_loss_order_id=result["stop_loss_order_id"],
            stop_loss_price=result["stop_loss_price"],
            status=result.get("status", "Submitted"),
            account_id=snap.account_id,
            note=signal.get("rationale") or signal.get("strategy_name") or "",
        )
        logger.info("ENTRY SUBMITTED %s x%s stop %s (id=%s)",
                    symbol, quantity, result["stop_loss_price"], result.get("order_id"))
        return "submitted"

    def _flatten(self, symbol: str, quantity: float, stop_order_id: Optional[int],
                 reason: str, target_order_id: Optional[int] = None) -> bool:
        """Cancel the resting stop (and target) and sell the shares we still hold."""
        for order_id, label in ((stop_order_id, "stop"), (target_order_id, "target")):
            if not order_id:
                continue
            try:
                self._adapter.cancel_order(int(order_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not cancel the %s on %s: %s", label, symbol, exc)
        held = self._positions().get(symbol)
        shares = float(held["position"]) if held else 0.0
        if shares <= 0:
            logger.info("%s is already flat after the stop cancel", symbol)
            return False
        intent = self._intent(symbol, OrderSide.SELL, shares)
        try:
            result = self._adapter.submit_close(intent)
        except Exception as exc:  # noqa: BLE001
            logger.error("Exit failed for %s: %s", symbol, exc)
            return False
        if not result.get("submitted"):
            logger.info("Exit not sent for %s: %s", symbol,
                        result.get("rejection_reason"))
            return False
        self._journal.attach_exit_order(symbol, int(result["order_id"]))
        logger.info("EXIT SUBMITTED %s x%s (%s, id=%s)",
                    symbol, shares, reason, result["order_id"])
        return True

    def _resting_sells(self) -> Optional[set[str]]:
        """Symbols with a working sell. None when the book cannot be read."""
        try:
            orders = self._adapter.get_open_orders()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read resting orders: %s", exc)
            return None
        # A resting stop is also a sell. Only a market sell means an exit is
        # already working, so the trail and a second exit must wait.
        return {
            order["symbol"] for order in orders
            if order.get("side") == "SELL" and order.get("order_type") == "MKT"
        }

    def _manage_open(self, session: date) -> Dict[str, int]:
        """Trail stops and leave positions the completed bar no longer wants."""
        raised = exited = 0
        held = self._positions()
        resting = self._resting_sells()
        if resting is None:
            return {"raised": raised, "exited": exited}
        for row in self._journal.open_trades():
            symbol = row["symbol"]
            if symbol not in held:
                continue
            if symbol in resting:
                continue
            book = contest.load_book(self._store, symbol)
            if book is None:
                continue
            entry_day = momentum.as_date(row["timestamp_utc"]) or session
            rule, fixed_sl, fixed_tp = rule_and_exits(
                symbol, self._specs_by_ticker, self._live_rule,
            )
            action = contest.manage(
                book, rule, entry_day,
                float(row["stop_loss_price"] or 0.0), session,
                fixed_stop_pct=fixed_sl,
                fixed_take_profit_pct=fixed_tp,
            )
            if action.kind == "exit":
                if self._flatten(symbol, float(row["quantity"]),
                                 row["stop_loss_order_id"], action.reason,
                                 row.get("take_profit_order_id")):
                    exited += 1
            elif action.kind == "raise" and row["stop_loss_order_id"]:
                try:
                    moved = self._adapter.modify_stop(
                        int(row["stop_loss_order_id"]), action.stop,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not raise the stop on %s: %s", symbol, exc)
                    continue
                if moved:
                    self._journal.set_stop(symbol, action.stop)
                    raised += 1
                    logger.info("Stop on %s raised to %.2f", symbol, action.stop)
        return {"raised": raised, "exited": exited}

    def _refresh_rule(self, symbols: List[str]) -> contest.Rule:
        """Re-score the contest when the bars changed. Never blocks a cycle."""
        try:
            report = contest.ensure(self._store, symbols)
            self._live_rule = report.holder
        except Exception as exc:  # noqa: BLE001
            logger.warning("Contest skipped: %s", exc)
            self._live_rule = contest.live_rule(self._store)
        return self._live_rule

    def _decide(self, snap, session: date) -> Dict[str, Any]:
        """One pass on the last completed session: exits first, then new buys."""
        symbols = self._reload_specs()
        coverage = self._market.coverage()
        ready = any(
            int(row.get("bars") or 0) >= config.MIN_BARS_FOR_SIGNALS
            for row in coverage
            if row.get("symbol") in symbols
        )
        if not ready:
            return {"signals": 0, "entries": 0, "approved": 0, "submitted": 0,
                    "blocked": 0, "skipped": 0, "raised": 0, "exited": 0,
                    "universe": len(symbols), "strategies_ready": 0,
                    "decided": False, "waiting": "history"}

        # Contest still picks a default holder; per-stock files override exits
        # and entry family when present (see rule_and_exits).
        self._refresh_rule(symbols)
        managed = self._manage_open(session)
        active = self._active_symbols()
        approved = submitted = skipped = blocked = signals = 0
        for symbol in symbols:
            book = contest.load_book(self._store, symbol)
            if book is None:
                continue
            rule, fixed_sl, fixed_tp = rule_and_exits(
                symbol, self._specs_by_ticker, self._live_rule,
            )
            reading = contest.read_symbol(
                book, rule, session,
                fixed_stop_pct=fixed_sl,
                fixed_take_profit_pct=fixed_tp,
            )
            if reading is None or not reading.buy:
                continue
            if symbol in active:
                skipped += 1
                continue
            prior = self._journal.last_closed(symbol)
            exit_index = None
            outcome = None
            if prior and prior.get("closed_at_utc"):
                outcome = prior["outcome"]
                closed_day = momentum.as_date(prior["closed_at_utc"])
                if closed_day is not None:
                    exit_index = momentum.index_of_day(book["dates"], closed_day)
            # Rising-edge / stop cooldown live here — count a signal only when
            # the runner would actually attempt an entry (not every day m12
            # stays true while flat). Cold-start lets a never-traded name take
            # its first entry while an already-on state regime is still true.
            if not contest.allow_entry(
                book, rule, reading.index, outcome, exit_index,
                cold_start=not self._journal.has_submitted_entry(symbol),
            ):
                skipped += 1
                continue
            signals += 1
            quantity = momentum.share_quantity(
                self._portfolio_value, reading.price, reading.stop,
                cash=float(snap.total_cash_value or 0.0),
            )
            if quantity < 1:
                skipped += 1
                logger.info("Skipping %s — the stop risks more than the budget "
                            "on a single share", symbol)
                continue
            signal = {
                "symbol": symbol, "stop": reading.stop, "price": reading.price,
                "target": reading.target, "rationale": reading.rationale,
            }
            outcome_send = self._enter(signal, snap, quantity)
            if outcome_send == "submitted":
                submitted += 1
                approved += 1
                active.add(symbol)
            elif outcome_send == "blocked":
                blocked += 1
            elif outcome_send == "error":
                continue
            else:
                skipped += 1
        self._last_decision_day = session.isoformat()
        return {
            "signals": signals,
            "entries": signals,
            "approved": approved,
            "submitted": submitted,
            "blocked": blocked,
            "skipped": skipped,
            "raised": managed["raised"],
            "exited": managed["exited"],
            "universe": len(symbols),
            "strategies_ready": 1,
            "decided": True,
            "waiting": None,
        }

    def _run_trading_cycle(self) -> Dict[str, Any]:
        logger.info("Starting trading cycle at %s",
                    datetime.now(timezone.utc).isoformat())

        snap = self._adapter.get_account_snapshot()
        self._journal.record(snap)
        self._portfolio_value = float(snap.net_liquidation)
        self._settle_open_brackets()

        session = momentum.session_day()
        force_sync = self._last_sync_day != session.isoformat()
        if force_sync:
            self._last_sync_day = session.isoformat()
        try:
            self._maybe_sync_data(force=force_sync)
        except Exception as exc:  # noqa: BLE001 - never let data stop the cycle
            logger.warning("Automatic data sync failed: %s", exc)

        if self._last_decision_day == session.isoformat():
            symbols = self._trade_symbols()
            return {"signals": 0, "entries": 0, "approved": 0, "submitted": 0,
                    "blocked": 0, "skipped": 0, "raised": 0, "exited": 0,
                    "universe": len(symbols), "strategies_ready": 1,
                    "decided": False, "waiting": None}

        return self._decide(snap, session)


def run_continuous_trading(
    db_path: str = config.DB_PATH,
    poll_interval: float = config.POLL_INTERVAL,
) -> None:
    """Blocking CLI entry point: start, keep alive, stop on Ctrl-C."""
    runner = LiveTradingRunner(db_path=db_path, poll_interval=poll_interval)
    try:
        info = runner.start()
        currency = info.get("currency") or "USD"
        print(f"Connected to paper account {info['account']} — algo running")
        print(f"Original paper money: {info['original_capital']:,.2f} {currency}")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Trading runner stopped by user")
    finally:
        runner.stop()


def main() -> None:
    """CLI entry point for the live trading runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Long-only paper trading runner (12-month momentum, trailing stop)"
    )
    parser.add_argument("--db-path", default=config.DB_PATH,
                        help="Path to the paper journal SQLite file")
    parser.add_argument("--interval", type=float, default=config.POLL_INTERVAL,
                        help="Seconds between analysis passes")
    args = parser.parse_args()

    try:
        run_continuous_trading(
            db_path=args.db_path,
            poll_interval=args.interval,
        )
    except RuntimeError as exc:
        logger.error("%s", exc)
        print(f"\nFATAL ERROR: {exc}\n")
        print("The algo cannot start without a valid IBKR Gateway connection.")
        print("Checklist:")
        print("  1. IBKR Gateway is running and you are logged into the paper account")
        print("  2. Configure > API > Settings > Enable ActiveX and Socket Clients")
        print(f"  3. Port {config.IBKR_PORT} and client id 1 are free")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
