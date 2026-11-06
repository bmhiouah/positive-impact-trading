"""Local data storage layer using SQLite.

Provides an append-only, immutable store for price bars, fundamental data,
screen results, and update audit logs. SQLite is serverless, stores everything
in a single file, and is optimized for analytical queries.

Design principles:
  * Append-only — never update or delete existing rows (immutable history)
  * Incremental — only insert new/changed records
  * Audit-ready — every data update is logged with timestamp and status
"""

from __future__ import annotations

import logging
import datetime
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    import sqlite3
except ImportError:
    sqlite3 = None

from .models import (
    PriceBar,
    FundamentalData,
    ScreenResult,
    DataUpdate,
    AccountSnapshot,
    PositionRecord,
    OrderRecord,
    ExecutionRecord,
    MarketQuote,
    ContractInfo,
)

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/marketmind.db")


def _utc_naive() -> datetime.datetime:
    """Naive UTC now — ``datetime.utcnow()`` is deprecated on Python 3.14."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class DataStore:
    """SQLite-backed storage for market data and compliance screening results."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        if sqlite3 is None:
            raise ImportError("sqlite3 is not available (built-in).")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._thread_local = threading.local()
        self._init_schema()

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._thread_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._thread_local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._thread_local, "conn", None)
        if conn is not None:
            conn.close()
            self._thread_local.conn = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "DataStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Schema initialization
    # ------------------------------------------------------------------ #

    def _init_schema(self) -> None:
        c = self.conn

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS price_bars (
                symbol         VARCHAR NOT NULL,
                timestamp      TEXT NOT NULL,
                open           DOUBLE NOT NULL,
                high           DOUBLE NOT NULL,
                low            DOUBLE NOT NULL,
                close          DOUBLE NOT NULL,
                volume         INTEGER NOT NULL,
                vwap           DOUBLE,
                bar_size       VARCHAR NOT NULL DEFAULT '1 day',
                what_to_show   VARCHAR NOT NULL DEFAULT 'ADJUSTED_LAST',
                source         VARCHAR NOT NULL DEFAULT 'IBKR',
                ingested_at    TEXT NOT NULL,
                PRIMARY KEY (symbol, timestamp, bar_size, what_to_show)
            )
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS fundamental_data (
                symbol             VARCHAR NOT NULL,
                sector             VARCHAR,
                industry           VARCHAR,
                market_cap         DOUBLE,
                total_debt         DOUBLE,
                debt_to_market_cap DOUBLE,
                pe_ratio           DOUBLE,
                pb_ratio           DOUBLE,
                dividend_yield     DOUBLE,
                country            VARCHAR,
                currency           VARCHAR NOT NULL DEFAULT 'USD',
                last_updated       TEXT NOT NULL,
                source             VARCHAR NOT NULL DEFAULT 'IBKR',
                ingested_at        TEXT NOT NULL,
                PRIMARY KEY (symbol)
            )
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS screen_results (
                symbol             VARCHAR NOT NULL,
                asset_class        VARCHAR NOT NULL,
                passed             BOOLEAN NOT NULL,
                reasons            TEXT,
                sector             VARCHAR,
                debt_to_market_cap DOUBLE,
                pe_ratio           DOUBLE,
                screened_at        TEXT NOT NULL,
                ingested_at        TEXT NOT NULL,
                PRIMARY KEY (symbol, screened_at)
            )
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS data_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol           VARCHAR,
                update_type        VARCHAR,
                records_inserted   INTEGER,
                started_at         TEXT NOT NULL,
                completed_at       TEXT,
                status             VARCHAR,
                error_message      TEXT,
                source             VARCHAR
            )
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc    TEXT NOT NULL,
                symbol           VARCHAR NOT NULL,
                order_id         VARCHAR,
                order_type       VARCHAR NOT NULL,
                side             VARCHAR NOT NULL,
                quantity         REAL NOT NULL,
                price            REAL NOT NULL,
                commission       REAL DEFAULT 0,
                status           VARCHAR NOT NULL DEFAULT 'pending',
                reason           TEXT
            )
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc    TEXT NOT NULL,
                portfolio_value  REAL NOT NULL,
                paper_trading    BOOLEAN NOT NULL DEFAULT TRUE
            )
            """
        )
        # --- Account snapshots ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS account_snapshots (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc        TEXT NOT NULL,
                account_id           VARCHAR,
                net_liquidation      DOUBLE NOT NULL,
                total_cash_value     DOUBLE NOT NULL DEFAULT 0,
                cash_balance         DOUBLE NOT NULL DEFAULT 0,
                buying_power         DOUBLE NOT NULL DEFAULT 0,
                available_funds      DOUBLE NOT NULL DEFAULT 0,
                excess_liquidity     DOUBLE NOT NULL DEFAULT 0,
                gross_position_value DOUBLE NOT NULL DEFAULT 0,
                unrealized_pnl       DOUBLE NOT NULL DEFAULT 0,
                realized_pnl         DOUBLE NOT NULL DEFAULT 0,
                total_positions_value DOUBLE NOT NULL DEFAULT 0,
                source               VARCHAR NOT NULL DEFAULT 'IBKR'
            )
            """
        )

        # --- Positions ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc     TEXT NOT NULL,
                symbol            VARCHAR NOT NULL,
                con_id            INTEGER,
                exchange          VARCHAR,
                currency          VARCHAR NOT NULL DEFAULT 'USD',
                position          DOUBLE NOT NULL,
                avg_cost          DOUBLE NOT NULL DEFAULT 0,
                market_price      DOUBLE NOT NULL DEFAULT 0,
                market_value      DOUBLE NOT NULL DEFAULT 0,
                unrealized_pnl    DOUBLE NOT NULL DEFAULT 0,
                unrealized_pnl_pct DOUBLE NOT NULL DEFAULT 0,
                asset_class       VARCHAR NOT NULL DEFAULT 'equity',
                source            VARCHAR NOT NULL DEFAULT 'IBKR'
            )
            """
        )

        # --- Orders ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id          INTEGER NOT NULL,
                timestamp_utc     TEXT NOT NULL,
                symbol            VARCHAR NOT NULL,
                con_id            INTEGER,
                exchange          VARCHAR,
                side              VARCHAR NOT NULL,
                order_type        VARCHAR NOT NULL,
                quantity          DOUBLE NOT NULL,
                filled_quantity   DOUBLE NOT NULL DEFAULT 0,
                limit_price       DOUBLE,
                stop_price        DOUBLE,
                avg_fill_price    DOUBLE,
                status            VARCHAR NOT NULL,
                time_in_force     VARCHAR,
                account_id        VARCHAR,
                reason            VARCHAR,
                source            VARCHAR NOT NULL DEFAULT 'IBKR'
            )
            """
        )

        # --- Executions ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS executions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                exec_id      VARCHAR NOT NULL,
                order_id     INTEGER NOT NULL,
                timestamp_utc TEXT NOT NULL,
                symbol       VARCHAR NOT NULL,
                side         VARCHAR NOT NULL,
                quantity     DOUBLE NOT NULL,
                price        DOUBLE NOT NULL,
                commission   DOUBLE NOT NULL DEFAULT 0,
                currency     VARCHAR NOT NULL DEFAULT 'USD',
                source       VARCHAR NOT NULL DEFAULT 'IBKR'
            )
            """
        )

        # --- Market quotes ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS market_quotes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc TEXT NOT NULL,
                symbol       VARCHAR NOT NULL,
                con_id       INTEGER,
                bid          DOUBLE,
                ask          DOUBLE,
                last         DOUBLE,
                bid_size     DOUBLE,
                ask_size     DOUBLE,
                volume       INTEGER,
                high         DOUBLE,
                low          DOUBLE,
                close        DOUBLE,
                source       VARCHAR NOT NULL DEFAULT 'IBKR'
            )
            """
        )

        # --- Contracts ---
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS contracts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      VARCHAR NOT NULL,
                con_id      INTEGER NOT NULL,
                exchange    VARCHAR NOT NULL,
                currency    VARCHAR NOT NULL DEFAULT 'USD',
                asset_class VARCHAR NOT NULL DEFAULT 'equity',
                sector      VARCHAR,
                industry    VARCHAR,
                market_cap  DOUBLE,
                multiplier  DOUBLE NOT NULL DEFAULT 1,
                min_tick    DOUBLE,
                trading_class VARCHAR,
                source      VARCHAR NOT NULL DEFAULT 'IBKR',
                UNIQUE(con_id)
            )
            """
        )

        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_bars_symbol "
            "ON price_bars(symbol, timestamp)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_fundamental_symbol "
            "ON fundamental_data(symbol)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_screen_symbol "
            "ON screen_results(symbol)"
        )

        logger.info("DataStore initialized at %s", self.db_path)

    # ------------------------------------------------------------------ #
    # Price bars
    # ------------------------------------------------------------------ #

    def insert_price_bars(self, bars: List[PriceBar]) -> int:
        """Insert price bars (deduplicated by primary key). Returns rows inserted."""
        if not bars:
            return 0

        c = self.conn
        now_iso = _utc_naive().isoformat() + 'Z'
        rows = [
            (
                bar.symbol,
                bar.timestamp.isoformat() + 'Z' if hasattr(bar.timestamp, 'isoformat') else str(bar.timestamp),
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.vwap,
                bar.bar_size,
                bar.what_to_show,
                bar.source,
                now_iso,  # ingested_at — when the row was inserted
            )
            for bar in bars
        ]
        c.executemany(
            "INSERT OR IGNORE INTO price_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        c.commit()
        return len(rows)

    def get_latest_price(self, symbol: str) -> Optional[PriceBar]:
        """Get the most recent price bar for a symbol."""
        c = self.conn
        row = c.execute(
            """
            SELECT symbol, timestamp, open, high, low, close, volume,
                   vwap, bar_size, what_to_show, source
            FROM price_bars
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            [symbol],
        ).fetchone()
        if row is None:
            return None
        from datetime import datetime as _dt
        ts_str = row[1]
        try:
            ts = _dt.fromisoformat(ts_str.replace("Z", ""))
        except (ValueError, AttributeError):
            ts = _utc_naive()
        return PriceBar(
            symbol=row[0],
            timestamp=ts,
            open=row[2],
            high=row[3],
            low=row[4],
            close=row[5],
            volume=row[6],
            vwap=row[7],
            bar_size=row[8],
            what_to_show=row[9],
            source=row[10],
        )

    def get_price_history(
        self,
        symbol: str,
        bar_size: str = "1 day",
        limit: int = 504,
    ) -> List[PriceBar]:
        """Get historical price bars for a symbol, oldest first."""
        c = self.conn
        rows = c.execute(
            """
            SELECT symbol, timestamp, open, high, low, close, volume,
                   vwap, bar_size, what_to_show, source
            FROM price_bars
            WHERE symbol = ? AND bar_size = ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            [symbol, bar_size, limit],
        ).fetchall()
        from datetime import datetime as _dt
        bars = []
        for row in rows:
            ts_str = row[1]
            try:
                ts = _dt.fromisoformat(ts_str.replace("Z", ""))
            except (ValueError, AttributeError):
                ts = _utc_naive()
            bars.append(PriceBar(
                symbol=row[0],
                timestamp=ts,
                open=row[2],
                high=row[3],
                low=row[4],
                close=row[5],
                volume=row[6],
                vwap=row[7],
                bar_size=row[8],
                what_to_show=row[9],
                source=row[10],
            ))
        return bars

    # ------------------------------------------------------------------ #
    # Fundamental data
    # ------------------------------------------------------------------ #

    def get_fundamental_data(self, symbol: str) -> Optional[FundamentalData]:
        """Get fundamental data for a symbol."""
        c = self.conn
        row = c.execute(
            """
            SELECT symbol, sector, industry, market_cap, total_debt,
                   debt_to_market_cap, pe_ratio, pb_ratio, dividend_yield,
                   country, currency, last_updated, source, ingested_at
            FROM fundamental_data WHERE symbol = ?
            """,
            [symbol],
        ).fetchone()
        
        if row is None:
            return None
        
        return FundamentalData(
            symbol=row[0],
            sector=row[1],
            industry=row[2],
            market_cap=row[3],
            total_debt=row[4],
            debt_to_market_cap=row[5],
            pe_ratio=row[6],
            pb_ratio=row[7],
            dividend_yield=row[8],
            country=row[9],
            currency=row[10],
            last_updated=row[11],
            source=row[12],
        )

    def insert_fundamental_data(self, data: List[FundamentalData]) -> int:
        """Insert fundamental data (deduplicated by primary key). Returns rows inserted."""
        if not data:
            return 0

        c = self.conn
        now_iso = _utc_naive().isoformat() + 'Z'
        rows = [
            (
                d.symbol,
                d.sector,
                d.industry,
                d.market_cap,
                d.total_debt,
                d.debt_to_market_cap,
                d.pe_ratio,
                d.pb_ratio,
                d.dividend_yield,
                d.country,
                d.currency,
                d.last_updated.isoformat() + 'Z' if hasattr(d.last_updated, 'isoformat') else str(d.last_updated),
                d.source,
                now_iso,  # ingested_at
            )
            for d in data
        ]
        c.executemany(
            "INSERT OR IGNORE INTO fundamental_data VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        c.commit()
        return len(rows)

    def upsert_fundamental_data(self, data: FundamentalData) -> int:
        """Insert or replace a single fundamental data record (upsert by symbol)."""
        c = self.conn
        now_iso = _utc_naive().isoformat() + 'Z'
        last_updated_iso = (
            data.last_updated.isoformat() + 'Z'
            if hasattr(data.last_updated, 'isoformat')
            else str(data.last_updated)
        )
        cur = c.execute(
            """
            INSERT OR REPLACE INTO fundamental_data
            (symbol, sector, industry, market_cap, total_debt,
             debt_to_market_cap, pe_ratio, pb_ratio, dividend_yield,
             country, currency, last_updated, source, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.symbol,
                data.sector,
                data.industry,
                data.market_cap,
                data.total_debt,
                data.debt_to_market_cap,
                data.pe_ratio,
                data.pb_ratio,
                data.dividend_yield,
                data.country,
                data.currency,
                last_updated_iso,
                data.source,
                now_iso,
            ),
        )
        c.commit()
        return cur.lastrowid or 1

    # ------------------------------------------------------------------ #
    # Screen results
    # ------------------------------------------------------------------ #

    def insert_screen_results(self, results: List[ScreenResult]) -> int:
        """Insert screen results (deduplicated by primary key). Returns rows inserted."""
        if not results:
            return 0

        import json

        c = self.conn
        now_iso = _utc_naive().isoformat() + 'Z'
        rows = [
            (
                r.symbol,
                r.asset_class.value if hasattr(r.asset_class, 'value') else str(r.asset_class),
                r.passed,
                json.dumps(r.reasons),  # serialize list[str] to JSON for TEXT column
                r.sector,
                r.debt_to_market_cap,
                None,  # pe_ratio — ScreenResult doesn't carry it
                r.screened_at.isoformat() + 'Z' if hasattr(r.screened_at, 'isoformat') else str(r.screened_at),
                now_iso,  # ingested_at
            )
            for r in results
        ]
        c.executemany(
            "INSERT OR IGNORE INTO screen_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        c.commit()
        return len(rows)

    def insert_screen_result(self, result: ScreenResult) -> int:
        """Insert a single screen result (convenience wrapper)."""
        return self.insert_screen_results([result])

    # ------------------------------------------------------------------ #
    # Audit log
    # ------------------------------------------------------------------ #

    def log_data_update(self, update: DataUpdate) -> None:
        """Record a data update operation for auditability.

        Timestamps are written as explicit ISO strings: Python 3.12+ has
        deprecated sqlite3's implicit datetime adapters, and ``coverage()``
        parses these columns back with ``datetime.fromisoformat``.
        """
        def iso(value):
            if value is None:
                return None
            return value.isoformat() if hasattr(value, "isoformat") else str(value)

        c = self.conn
        c.execute(
            """
            INSERT INTO data_updates
            (symbol, update_type, records_inserted, started_at,
             completed_at, status, error_message, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                update.symbol,
                update.update_type,
                update.records_inserted,
                iso(update.started_at),
                iso(update.completed_at),
                update.status,
                update.error_message,
                update.source,
            ],
        )
        c.commit()

    # ------------------------------------------------------------------ #
    # Summary statistics
    # ------------------------------------------------------------------ #

    def get_storage_stats(self) -> Dict[str, Any]:
        """Return summary statistics about stored data."""
        c = self.conn
        stats: Dict[str, Any] = {}

        stats["price_bars_count"] = c.execute(
            "SELECT COUNT(*) FROM price_bars"
        ).fetchone()[0]
        stats["unique_symbols"] = c.execute(
            "SELECT COUNT(DISTINCT symbol) FROM price_bars"
        ).fetchone()[0]
        stats["fundamental_count"] = c.execute(
            "SELECT COUNT(*) FROM fundamental_data"
        ).fetchone()[0]
        stats["screen_count"] = c.execute(
            "SELECT COUNT(*) FROM screen_results"
        ).fetchone()[0]
        stats["passed_symbols"] = len(self.get_passed_symbols())

        date_range = c.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM price_bars"
        ).fetchone()
        stats["data_start"] = str(date_range[0]) if date_range[0] else None
        stats["data_end"] = str(date_range[1]) if date_range[1] else None

        return stats

    # ------------------------------------------------------------------ #
    # Passed symbols
    # ------------------------------------------------------------------ #

    def get_passed_symbols(self, asset_class: Optional[str] = None) -> List[str]:
        """Get all symbols that passed the latest compliance screen."""
        c = self.conn
        if asset_class is not None:
            rows = c.execute(
                """
                SELECT DISTINCT symbol
                FROM screen_results sr1
                WHERE sr1.passed = TRUE
                  AND sr1.screened_at = (
                    SELECT MAX(sr2.screened_at)
                    FROM screen_results sr2
                    WHERE sr2.symbol = sr1.symbol
                  )
                  AND sr1.asset_class = ?
                """,
                [asset_class],
            ).fetchall()
        else:
            rows = c.execute(
                """
                SELECT DISTINCT symbol
                FROM screen_results sr1
                WHERE sr1.passed = TRUE
                  AND sr1.screened_at = (
                    SELECT MAX(sr2.screened_at)
                    FROM screen_results sr2
                    WHERE sr2.symbol = sr1.symbol
                  )
                """
            ).fetchall()

        return [r[0] for r in rows]

    # ------------------------------------------------------------------ #
    # Trades
    # ------------------------------------------------------------------ #

    def insert_trades(self, trades: List) -> int:
        """Insert trades."""
        if not trades:
            return 0

        c = self.conn
        rows = [
            (
                trade.timestamp_utc.isoformat() + 'Z' if hasattr(trade, 'timestamp_utc') and hasattr(trade.timestamp_utc, 'isoformat') else str(getattr(trade, 'timestamp_utc', '')),
                trade.symbol,
                trade.order_id if hasattr(trade, 'order_id') else None,
                trade.order_type.value if hasattr(trade.order_type, 'value') else str(trade.order_type) if hasattr(trade, 'order_type') else None,
                trade.side.value if hasattr(trade.side, 'value') else str(trade.side) if hasattr(trade, 'side') else None,
                trade.quantity,
                trade.price,
                getattr(trade, 'commission', 0),
                getattr(trade, 'status', 'pending'),
                getattr(trade, 'reason', None),
            )
            for trade in trades
        ]
        c.executemany(
            "INSERT INTO trades (timestamp_utc, symbol, order_id, order_type, side, quantity, price, commission, status, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        c.commit()
        return len(rows)

    # ------------------------------------------------------------------ #
    # Portfolio equity
    # ------------------------------------------------------------------ #

    def insert_portfolio_equity(self, records: List) -> int:
        """Insert portfolio equity records."""
        if not records:
            return 0

        c = self.conn
        rows = [
            (
                getattr(record, 'timestamp_utc', _utc_naive().strftime('%Y-%m-%dT%H:%M:%fZ')),
                getattr(record, 'portfolio_value', 0.0),
                getattr(record, 'paper_trading', True),
            )
            for record in records
        ]
        c.executemany(
            "INSERT INTO portfolio_equity (timestamp_utc, portfolio_value, paper_trading) VALUES (?, ?, ?)",
            rows
        )
        c.commit()
        return len(rows)

    # ------------------------------------------------------------------ #
    # Account snapshots
    # ------------------------------------------------------------------ #

    def insert_account_snapshot(self, record: AccountSnapshot) -> int:
        """Insert an account snapshot."""
        conn = self.conn
        cur = conn.execute(
            """
            INSERT INTO account_snapshots
            (timestamp_utc, account_id, net_liquidation, total_cash_value,
             cash_balance, buying_power, available_funds, excess_liquidity,
             gross_position_value, unrealized_pnl, realized_pnl,
             total_positions_value, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.timestamp_utc.isoformat(),
                record.account_id,
                record.net_liquidation,
                record.total_cash_value,
                record.cash_balance,
                record.buying_power,
                record.available_funds,
                record.excess_liquidity,
                record.gross_position_value,
                record.unrealized_pnl,
                record.realized_pnl,
                record.total_positions_value,
                record.source,
            ),
        )
        conn.commit()
        return getattr(cur, "lastrowid", None) or 1

    def get_latest_account_snapshot(self):
        """Get the most recent account snapshot."""
        c = self.conn
        cursor = c.execute(
            """
            SELECT * FROM account_snapshots
            ORDER BY id DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row:
            columns = [d[0] for d in cursor.description]
            return dict(zip(columns, row))
        return None

    def get_account_snapshots(self, limit: int = 100):
        """Get recent account snapshots."""
        c = self.conn
        cursor = c.execute(
            """
            SELECT * FROM account_snapshots
            ORDER BY id DESC LIMIT ?
            """,
            [limit],
        )
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, r)) for r in rows]

    def load_table(self, table_name: str, limit: int = 100):
        """Load rows from a table, newest first."""
        c = self.conn
        cursor = c.execute(
            f"SELECT * FROM {table_name} ORDER BY rowid DESC LIMIT ?",
            [limit],
        )
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #

    def insert_position(self, record: PositionRecord) -> int:
        """Insert a position record."""
        c = self.conn
        cur = c.execute(
            """
            INSERT INTO positions
            (timestamp_utc, symbol, con_id, exchange, currency, position,
             avg_cost, market_price, market_value, unrealized_pnl,
             unrealized_pnl_pct, asset_class, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.timestamp_utc.isoformat(),
                record.symbol_upper,
                record.con_id,
                record.exchange,
                record.currency,
                record.position,
                record.avg_cost,
                record.market_price,
                record.market_value,
                record.unrealized_pnl,
                record.unrealized_pnl_pct,
                record.asset_class.value if hasattr(record.asset_class, "value") else str(record.asset_class),
                record.source,
            ),
        )
        c.commit()
        return cur.lastrowid or 1

    def get_latest_positions(self):
        """Get the latest position for each symbol."""
        c = self.conn
        cursor = c.execute(
            """
            SELECT * FROM positions p1
            WHERE p1.id IN (
                SELECT MAX(p2.id) FROM positions p2
                WHERE p2.symbol = p1.symbol
            )
            ORDER BY p1.symbol
            """
        )
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #

    def insert_order(self, record: OrderRecord) -> int:
        """Insert an order record."""
        c = self.conn
        cur = c.execute(
            """
            INSERT INTO orders
            (order_id, timestamp_utc, symbol, con_id, exchange, side,
             order_type, quantity, filled_quantity, limit_price, stop_price,
             avg_fill_price, status, time_in_force, account_id, reason, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.order_id,
                record.timestamp_utc.isoformat(),
                record.symbol_upper,
                record.con_id,
                record.exchange,
                record.side.value if hasattr(record.side, "value") else str(record.side),
                record.order_type.value if hasattr(record.order_type, "value") else str(record.order_type),
                record.quantity,
                record.filled_quantity,
                record.limit_price,
                record.stop_price,
                record.avg_fill_price,
                record.status,
                record.time_in_force,
                record.account_id,
                record.reason,
                record.source,
            ),
        )
        c.commit()
        return cur.lastrowid or 1

    def get_orders(self, status=None, limit: int = 100):
        """Get orders, optionally filtered by status."""
        c = self.conn
        if status:
            cursor = c.execute(
                """
                SELECT * FROM orders WHERE status = ?
                ORDER BY id DESC LIMIT ?
                """,
                [status, limit],
            )
        else:
            cursor = c.execute(
                """
                SELECT * FROM orders ORDER BY id DESC LIMIT ?
                """,
                [limit],
            )
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Executions
    # ------------------------------------------------------------------ #

    def insert_execution(self, record: ExecutionRecord) -> int:
        """Insert an execution (fill) record."""
        c = self.conn
        cur = c.execute(
            """
            INSERT INTO executions
            (exec_id, order_id, timestamp_utc, symbol, side, quantity,
             price, commission, currency, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.exec_id,
                record.order_id,
                record.time.isoformat(),
                record.symbol_upper,
                record.side,
                record.quantity,
                record.price,
                record.commission,
                record.currency,
                record.source,
            ),
        )
        c.commit()
        return cur.lastrowid or 1

    def get_executions(self, limit: int = 100):
        """Get recent executions."""
        c = self.conn
        cursor = c.execute(
            """
            SELECT * FROM executions ORDER BY id DESC LIMIT ?
            """,
            [limit],
        )
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Market quotes
    # ------------------------------------------------------------------ #

    def insert_market_quote(self, record: MarketQuote) -> int:
        """Insert a market quote."""
        c = self.conn
        cur = c.execute(
            """
            INSERT INTO market_quotes
            (timestamp_utc, symbol, con_id, bid, ask, last, bid_size,
             ask_size, volume, high, low, close, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.timestamp_utc.isoformat(),
                record.symbol_upper,
                record.con_id,
                record.bid,
                record.ask,
                record.last,
                record.bid_size,
                record.ask_size,
                record.volume,
                record.high,
                record.low,
                record.close,
                record.source,
            ),
        )
        c.commit()
        return cur.lastrowid or 1

    def get_latest_quotes(self):
        """Get the latest quote for each symbol."""
        c = self.conn
        cursor = c.execute(
            """
            SELECT * FROM market_quotes mq1
            WHERE mq1.id IN (
                SELECT MAX(mq2.id) FROM market_quotes mq2
                WHERE mq2.symbol = mq1.symbol
            )
            ORDER BY mq1.symbol
            """
        )
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Contracts
    # ------------------------------------------------------------------ #

    def insert_contract(self, record: ContractInfo) -> int:
        """Insert or replace a contract info record."""
        c = self.conn
        cur = c.execute(
            """
            INSERT OR REPLACE INTO contracts
            (symbol, con_id, exchange, currency, asset_class, sector,
             industry, market_cap, multiplier, min_tick, trading_class, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.symbol_upper,
                record.con_id,
                record.exchange,
                record.currency,
                record.asset_class.value if hasattr(record.asset_class, "value") else str(record.asset_class),
                record.sector,
                record.industry,
                record.market_cap,
                record.multiplier,
                record.min_tick,
                record.trading_class,
                record.source,
            ),
        )
        c.commit()
        return cur.lastrowid or 1

    def get_contracts(self):
        """Get all contract info records."""
        c = self.conn
        cursor = c.execute(
            """
            SELECT * FROM contracts ORDER BY symbol
            """
        )
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, r)) for r in rows]
