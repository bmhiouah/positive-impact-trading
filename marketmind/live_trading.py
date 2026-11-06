"""Live paper-trading runner for long-only strategies.

Lifecycle (this is what the cockpit's "Start Algo" button triggers):

    1. connect to the one IB Gateway paper session  <- mandatory, fails loudly
    2. read the account snapshot via the API         <- capital comes from HERE
    3. record the original paper money once          <- the journal baseline
    4. only then start the trading thread            <- signals -> compliance -> brackets

The runner is fully synchronous and thread-based. Streamlit executes scripts
on a thread with no persistent asyncio loop, so scheduling the bot as an
``asyncio`` task there meant the loop stopped as soon as the script rerun
finished. The IB adapter owns its own event loop internally; the runner simply
calls its thread-safe synchronous methods from a dedicated background thread.

Long-only: the runner opens BUY positions only. Every entry is a TP/SL bracket,
so exits are managed by IBKR and the lab never sends a naked order. Stopping
the algo leaves the resting brackets in place so open positions stay protected.
"""

from __future__ import annotations

# Ensure an event loop exists before ib_insync is imported (Python 3.14).
from marketmind import ib_compat  # noqa: F401

import logging
import threading
import time
from datetime import datetime, timezone
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
from marketmind.market_data import MarketDataService
from marketmind.universe import Universe
from marketmind import strategy_catalog

logger = logging.getLogger(__name__)


class LiveTradingRunner:
    """Runs long-only strategies continuously and submits TP/SL brackets."""

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

    def _maybe_sync_data(self) -> None:
        """Refresh the universe through the ALREADY OPEN session.

        A second connection is forbidden, so the runner reuses its own adapter
        instead of opening a new client id 1 session. A cooldown prevents a
        symbol IBKR cannot serve from being retried every 60 seconds.
        """
        now = time.monotonic()
        if now - self._last_data_sync < config.DATA_SYNC_COOLDOWN:
            return
        coverage = self._market.coverage()
        if coverage and all(row["status"] == "ready" for row in coverage):
            self._last_data_sync = now
            return
        logger.info("Refreshing IBKR market data for %s instruments",
                    len(coverage))
        self._market.sync(self._adapter)
        self._last_data_sync = now

    def _run_trading_cycle(self) -> Dict[str, Any]:
        logger.info("Starting trading cycle at %s",
                    datetime.now(timezone.utc).isoformat())

        # Step 0: real account snapshot -> journal (baseline, daily row, P&L).
        snap = self._adapter.get_account_snapshot()
        self._journal.record(snap)
        self._portfolio_value = float(snap.net_liquidation)

        # Step 0b: make the lab self-sufficient. The first cycle with an empty
        # or stale price history ingests contracts, daily bars and quotes over
        # this same connection, so strategies have real data to work with.
        try:
            self._maybe_sync_data()
        except Exception as exc:  # noqa: BLE001 - never let data stop trading
            logger.warning("Automatic data sync failed: %s", exc)

        # Step 1: run only the strategies the CURRENT data actually supports.
        # The universe comes from the instruments table, so adding a stock in
        # the cockpit automatically extends every subsequent cycle.
        symbols = self._universe.symbols()
        coverage = self._market.coverage()
        ready = [s["name"] for s in strategy_catalog.availability(coverage)
                 if s["ready"]]
        entries = strategy_catalog.run_all_ready(symbols, self._store, coverage)
        if not entries:
            return {"signals": 0, "entries": 0, "approved": 0,
                    "submitted": 0, "skipped": 0,
                    "universe": len(symbols), "strategies_ready": len(ready)}

        final_signals = entries

        active = self._active_symbols()
        approved_count = submitted_count = skipped_count = 0

        for signal in final_signals:
            if signal["symbol"] in active:
                skipped_count += 1
                logger.info("Skipping %s — position or bracket already open",
                            signal["symbol"])
                continue

            # The compliance module owns these whitelists. Never duplicate
            # them here: a new impact ETF must be recognised everywhere at once.
            is_impact_etf = signal["symbol"] in WHITELISTED_ETFS
            is_spot_metal = signal["symbol"] in WHITELISTED_METALS
            asset_class = (AssetClass.ETF if is_impact_etf
                           else AssetClass.SPOT_METAL if is_spot_metal
                           else AssetClass.EQUITY)

            # The compliance gate needs real fundamental data. It is supplied
            # from the data layer (contract metadata + values entered in the
            # cockpit); a missing debt ratio correctly blocks the order.
            fund = self._store.get_fundamental_data(signal["symbol"])
            intent = OrderIntent(
                symbol=signal["symbol"],
                asset_class=asset_class,
                side=OrderSide.BUY,
                quantity=config.QUANTITY,
                account_type=AccountType.CASH,
                sector=fund.sector if fund else None,
                debt_to_market_cap=(fund.debt_to_market_cap if fund else None),
                is_impact_etf=is_impact_etf,
                is_spot_metal=is_spot_metal,
            )
            try:
                result = self._adapter.submit_order(intent)
            except Exception as exc:  # noqa: BLE001
                self._journal.record_bracket(
                    symbol=signal["symbol"], side="BUY", quantity=config.QUANTITY,
                    entry_order_id=None, entry_type="BRACKET",
                    entry_price=None, reference_price=0.0,
                    take_profit_order_id=None, take_profit_price=0.0,
                    stop_loss_order_id=None, stop_loss_price=0.0,
                    status="error", account_id=snap.account_id, note=str(exc),
                )
                logger.error("Bracket failed for %s: %s", signal["symbol"], exc)
                continue

            if not result.get("approved"):
                self._journal.record_bracket(
                    symbol=signal["symbol"], side="BUY", quantity=config.QUANTITY,
                    entry_order_id=None, entry_type="BRACKET",
                    entry_price=None, reference_price=0.0,
                    take_profit_order_id=None, take_profit_price=0.0,
                    stop_loss_order_id=None, stop_loss_price=0.0,
                    status="blocked", account_id=snap.account_id,
                    note=result.get("rejection_reason"),
                )
                logger.debug("Blocked for %s: %s", signal["symbol"],
                             result.get("rejection_reason"))
                continue

            approved_count += 1
            if result.get("submitted"):
                submitted_count += 1
                active.add(signal["symbol"])
                self._journal.record_bracket(
                    symbol=signal["symbol"], side="BUY",
                    quantity=config.QUANTITY,
                    entry_order_id=result.get("order_id"),
                    entry_type=result.get("entry_type", "MKT"),
                    entry_price=result.get("entry_price"),
                    reference_price=result["reference_price"],
                    take_profit_order_id=result["take_profit_order_id"],
                    take_profit_price=result["take_profit_price"],
                    stop_loss_order_id=result["stop_loss_order_id"],
                    stop_loss_price=result["stop_loss_price"],
                    status=result.get("status", "Submitted"),
                    account_id=snap.account_id,
                    note=f"{signal.get('strategy_name', 'strategy')} · "
                         f"TP +{config.TAKE_PROFIT_PCT:.0%} / "
                         f"SL -{config.STOP_LOSS_PCT:.0%}",
                )
                logger.info("BRACKET SUBMITTED to IBKR: %s %s (id=%s)",
                            signal["symbol"], config.QUANTITY, result.get("order_id"))

        return {
            "signals": len(final_signals),
            "entries": len(final_signals),
            "approved": approved_count,
            "submitted": submitted_count,
            "skipped": skipped_count,
            "universe": len(symbols),
            "strategies_ready": len(ready),
        }


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
        description="Long-only paper trading runner (TP/SL brackets)"
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
