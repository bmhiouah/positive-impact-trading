"""The one instrument universe, shared by ingestion, strategies and the runner.

Scalability rule: no component owns a hardcoded list of tickers. ``config``
only seeds the initial universe; from then on the ``instruments`` table is the
single source of truth, and the cockpit can add or deactivate symbols at
runtime. Adding a stock never requires a code change.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from marketmind import config
from marketmind.data.storage import DataStore

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")


def normalize_symbol(symbol: str) -> str:
    """Uppercase and validate a ticker. Raises ValueError on junk."""
    value = str(symbol or "").strip().upper()
    if not _SYMBOL_RE.match(value):
        raise ValueError(
            f"Invalid symbol {symbol!r}: use 1-12 letters, digits, dots or dashes"
        )
    return value


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: Optional[str]
    exchange: Optional[str]
    currency: str
    sector: Optional[str]
    industry: Optional[str]
    active: bool
    added_at_utc: str


class Universe:
    """Thread-safe facade over the ``instruments`` table of a DataStore."""

    _META_FIELDS = {"name", "exchange", "currency", "sector", "industry"}

    def __init__(self, store: DataStore) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._init_schema()

    @property
    def conn(self):
        return self._store.conn

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instruments (
                symbol        TEXT PRIMARY KEY,
                name          TEXT,
                exchange      TEXT,
                currency      TEXT NOT NULL DEFAULT 'USD',
                sector        TEXT,
                industry      TEXT,
                active        INTEGER NOT NULL DEFAULT 1,
                added_at_utc  TEXT NOT NULL,
                source        TEXT NOT NULL DEFAULT 'IBKR'
            )
            """
        )
        self._seed()
        self.conn.commit()

    def _seed(self) -> None:
        """Seed once. Never re-add a symbol the user deactivated."""
        row = self.conn.execute("SELECT COUNT(*) FROM instruments").fetchone()
        if row and row[0]:
            return
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            """INSERT OR IGNORE INTO instruments
               (symbol, name, exchange, currency, active, added_at_utc, source)
               VALUES (?, NULL, NULL, 'USD', 1, ?, 'SEED')""",
            [(symbol, now) for symbol in config.DEFAULT_UNIVERSE],
        )

    def all_rows(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT symbol, name, exchange, currency, sector, industry,
                      active, added_at_utc
               FROM instruments ORDER BY symbol"""
        ).fetchall()
        keys = ("symbol", "name", "exchange", "currency", "sector",
                "industry", "active", "added_at_utc")
        return [dict(zip(keys, row)) for row in rows]

    def instruments(self) -> List[Instrument]:
        return [
            Instrument(
                symbol=r["symbol"], name=r["name"], exchange=r["exchange"],
                currency=r["currency"], sector=r["sector"],
                industry=r["industry"], active=bool(r["active"]),
                added_at_utc=r["added_at_utc"],
            )
            for r in self.all_rows()
        ]

    def symbols(self) -> List[str]:
        """Active tickers, in stable alphabetical order."""
        return [
            row[0] for row in self.conn.execute(
                "SELECT symbol FROM instruments WHERE active = 1 ORDER BY symbol"
            )
        ]

    def add(self, symbol: str) -> str:
        """Add (or re-activate) a ticker. Returns the normalized symbol."""
        value = normalize_symbol(symbol)
        with self._lock:
            self.conn.execute(
                """INSERT INTO instruments
                   (symbol, name, exchange, currency, active, added_at_utc, source)
                   VALUES (?, NULL, NULL, 'USD', 1, ?, 'COCKPIT')
                   ON CONFLICT(symbol) DO UPDATE SET active = 1""",
                (value, datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()
        return value

    def set_active(self, symbol: str, active: bool) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE instruments SET active = ? WHERE symbol = ?",
                (1 if active else 0, normalize_symbol(symbol)),
            )
            self.conn.commit()

    def remove(self, symbol: str) -> None:
        """Deactivate, never delete (history stays intact)."""
        self.set_active(symbol, False)

    def update_meta(self, symbol: str, **fields: Any) -> None:
        """Store contract metadata discovered through the IBKR API."""
        updates = {
            key: value for key, value in fields.items()
            if key in self._META_FIELDS and value not in (None, "")
        }
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._lock:
            self.conn.execute(
                f"UPDATE instruments SET {assignments} WHERE symbol = ?",
                (*updates.values(), normalize_symbol(symbol)),
            )
            self.conn.commit()

    def count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM instruments WHERE active = 1"
        ).fetchone()[0])
