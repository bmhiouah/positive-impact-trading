"""The paper-trading journal — one clean, append-only record for this lab.

Three small tables hold everything the cockpit needs:

``paper_baseline``  one immutable row: the ORIGINAL paper money, read from the
                    IBKR account via the API the first time the tool runs.
``paper_daily``     one row per UTC day: net liquidation, cash, equity invested
                    and P&L against the original money. Re-running the tool on
                    the same day updates that row instead of piling up noise.
``paper_orders``    one row per bracket order actually sent to IBKR, including
                    the take-profit and stop-loss order ids and prices.

Capital is never a parameter here. Every number comes from IBKR.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from marketmind.data.models import AccountSnapshot
from marketmind.data.storage import DataStore

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@dataclass(frozen=True)
class Baseline:
    """The original paper money. Written once, never overwritten."""

    recorded_at_utc: str
    account_id: Optional[str]
    original_capital: float
    original_cash: float
    original_equity: float


@dataclass(frozen=True)
class Allocation:
    """How the paper money is split between idle cash and long equity."""

    cash: float
    equity: float

    @property
    def total(self) -> float:
        return self.cash + self.equity

    @property
    def cash_pct(self) -> float:
        return 100.0 * self.cash / self.total if self.total else 0.0

    @property
    def equity_pct(self) -> float:
        return 100.0 * self.equity / self.total if self.total else 0.0


def allocation_of(snapshot: AccountSnapshot) -> Allocation:
    """Derive the cash / equity split from an IBKR account snapshot.

    ``GrossPositionValue`` is the market value of the long book (0 when the
    account is flat). Everything else — cash plus accrued interest — is idle
    paper money, so cash = NetLiq - equity. Using ``NetLiq - TotalCashValue``
    as "equity" would be wrong: on a flat account that residual is accrued
    interest, not a stock position.
    """
    equity = max(0.0, float(snapshot.gross_position_value or 0.0))
    cash = max(0.0, float(snapshot.net_liquidation or 0.0) - equity)
    return Allocation(cash=cash, equity=equity)


class PaperJournal:
    """Thin, thread-safe facade over the journal tables of a ``DataStore``."""

    def __init__(self, store: DataStore) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._init_schema()

    @property
    def conn(self):
        return self._store.conn

    def _init_schema(self) -> None:
        c = self.conn
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_baseline (
                id                INTEGER PRIMARY KEY CHECK (id = 1),
                recorded_at_utc   TEXT NOT NULL,
                account_id        TEXT,
                original_capital  REAL NOT NULL,
                original_cash     REAL NOT NULL,
                original_equity   REAL NOT NULL,
                source            TEXT NOT NULL DEFAULT 'IBKR'
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_daily (
                date_utc          TEXT PRIMARY KEY,
                net_liquidation   REAL NOT NULL,
                cash              REAL NOT NULL,
                equity            REAL NOT NULL,
                pnl_vs_original   REAL NOT NULL DEFAULT 0,
                orders_count      INTEGER NOT NULL DEFAULT 0,
                updated_at_utc    TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_orders (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc         TEXT NOT NULL,
                account_id            TEXT,
                symbol                TEXT NOT NULL,
                side                  TEXT NOT NULL,
                quantity              REAL NOT NULL,
                entry_order_id        INTEGER,
                entry_type            TEXT,
                entry_price           REAL,
                reference_price       REAL,
                take_profit_order_id  INTEGER,
                take_profit_price     REAL,
                stop_loss_order_id    INTEGER,
                stop_loss_price       REAL,
                status                TEXT NOT NULL,
                note                  TEXT,
                source                TEXT NOT NULL DEFAULT 'IBKR'
            )
            """
        )
        c.commit()

    # ------------------------------------------------------------------
    # Baseline (the original paper money)
    # ------------------------------------------------------------------

    def baseline(self) -> Optional[Baseline]:
        row = self.conn.execute(
            """SELECT recorded_at_utc, account_id, original_capital,
                      original_cash, original_equity
               FROM paper_baseline WHERE id = 1"""
        ).fetchone()
        if row is None:
            return None
        return Baseline(
            recorded_at_utc=row[0], account_id=row[1],
            original_capital=float(row[2]), original_cash=float(row[3]),
            original_equity=float(row[4]),
        )

    def ensure_baseline(self, snapshot: AccountSnapshot) -> Baseline:
        """Record the ORIGINAL paper money once, from the API. Never overwrite."""
        with self._lock:
            existing = self.baseline()
            if existing is not None:
                return existing
            split = allocation_of(snapshot)
            c = self.conn
            c.execute(
                """INSERT OR IGNORE INTO paper_baseline
                   (id, recorded_at_utc, account_id, original_capital,
                    original_cash, original_equity, source)
                   VALUES (1, ?, ?, ?, ?, ?, 'IBKR')""",
                (_utcnow(), snapshot.account_id, float(snapshot.net_liquidation),
                 split.cash, split.equity),
            )
            c.commit()
            logger.info(
                "Original paper money recorded: %.2f (cash %.2f / equity %.2f)",
                snapshot.net_liquidation, split.cash, split.equity,
            )
            return self.baseline()  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Daily evolution
    # ------------------------------------------------------------------

    def record(self, snapshot: AccountSnapshot) -> None:
        """Store one observation and upsert today's row (no duplicate spam)."""
        with self._lock:
            split = allocation_of(snapshot)
            base = self.baseline()
            original = base.original_capital if base else snapshot.net_liquidation
            pnl = float(snapshot.net_liquidation) - float(original)
            orders_today = self.conn.execute(
                "SELECT COUNT(*) FROM paper_orders "
                "WHERE substr(timestamp_utc, 1, 10) = ?", (_today(),)
            ).fetchone()[0]

            c = self.conn
            # The journal is the ONLY source of truth: one upserted row per UTC
            # day. We deliberately do NOT append to the legacy market-data
            # tables here — record() runs on every trading cycle, and writing a
            # raw row each time would recreate the noise this lab cleaned up.
            c.execute(
                """INSERT INTO paper_daily
                   (date_utc, net_liquidation, cash, equity,
                    pnl_vs_original, orders_count, updated_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(date_utc) DO UPDATE SET
                     net_liquidation = excluded.net_liquidation,
                     cash            = excluded.cash,
                     equity          = excluded.equity,
                     pnl_vs_original = excluded.pnl_vs_original,
                     orders_count    = excluded.orders_count,
                     updated_at_utc  = excluded.updated_at_utc""",
                (_today(), float(snapshot.net_liquidation), split.cash,
                 split.equity, pnl, orders_today, _utcnow()),
            )
            c.commit()

    def daily_history(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT date_utc, net_liquidation, cash, equity,
                      pnl_vs_original, orders_count, updated_at_utc
               FROM paper_daily ORDER BY date_utc ASC"""
        ).fetchall()
        keys = ("date_utc", "net_liquidation", "cash", "equity",
                "pnl_vs_original", "orders_count", "updated_at_utc")
        return [dict(zip(keys, row)) for row in rows]

    def latest(self) -> Optional[Dict[str, Any]]:
        history = self.daily_history()
        return history[-1] if history else None

    def split(self) -> Optional[Allocation]:
        """Latest known cash / equity split (for the camembert)."""
        latest = self.latest()
        if latest is not None:
            return Allocation(cash=float(latest["cash"]),
                              equity=float(latest["equity"]))
        base = self.baseline()
        if base is not None:
            return Allocation(cash=base.original_cash, equity=base.original_equity)
        return None

    # ------------------------------------------------------------------
    # Bracket orders
    # ------------------------------------------------------------------

    def record_bracket(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_order_id: Optional[int],
        entry_type: str,
        entry_price: Optional[float],
        reference_price: float,
        take_profit_order_id: Optional[int],
        take_profit_price: float,
        stop_loss_order_id: Optional[int],
        stop_loss_price: float,
        status: str,
        account_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO paper_orders
                   (timestamp_utc, account_id, symbol, side, quantity,
                    entry_order_id, entry_type, entry_price, reference_price,
                    take_profit_order_id, take_profit_price,
                    stop_loss_order_id, stop_loss_price, status, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_utcnow(), account_id, symbol, side, float(quantity),
                 entry_order_id, entry_type, entry_price, reference_price,
                 take_profit_order_id, take_profit_price,
                 stop_loss_order_id, stop_loss_price, status, note),
            )
            self.conn.commit()

    def orders(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT timestamp_utc, symbol, side, quantity, entry_order_id,
                      entry_type, entry_price, reference_price,
                      take_profit_price, stop_loss_price, status, note
               FROM paper_orders ORDER BY id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        keys = ("timestamp_utc", "symbol", "side", "quantity", "entry_order_id",
                "entry_type", "entry_price", "reference_price",
                "take_profit_price", "stop_loss_price", "status", "note")
        return [dict(zip(keys, row)) for row in rows]
