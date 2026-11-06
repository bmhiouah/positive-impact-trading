"""IBKR market-data ingestion and coverage tracking.

One Gateway session, one pass over the universe, three clean tables:

  * ``contracts`` / ``instruments``  — what the instrument IS (conId, exchange,
    long name, category, industry). Enriches the universe automatically.
  * ``price_bars``                  — daily OHLCV history, idempotent upserts.
  * ``market_quotes``               — latest delayed bid/ask/last snapshot.

Fundamentals via ``reqFundamentalData`` are NOT available on this paper account
(IB error 10358), so sector/industry come from ``reqContractDetails`` instead
and debt ratios must be supplied explicitly from the cockpit. Components
downstream read ``coverage()`` to know which strategies the data can actually
support — that is what makes the lab data-driven rather than aspirational.
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from marketmind import config
from marketmind.compliance.rules import AssetClass
from marketmind.data.models import (
    ContractInfo, DataUpdate, FundamentalData, MarketQuote, PriceBar,
)
from marketmind.data.storage import DataStore
from marketmind.universe import Universe, normalize_symbol

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_stamp(value: Any) -> Optional[datetime]:
    """Parse a journal/log timestamp. ``None`` means genuinely unparseable."""
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_bar_date(value: Any) -> Optional[datetime]:
    """Normalize IBKR bar dates into a naive UTC datetime.

    IBKR returns several shapes depending on bar size and ``formatDate``:
    ``date`` objects, ``YYYYMMDD``, ``YYYYMMDD HH:MM:SS TZ`` or ISO strings.
    A fabricated "now" would collide on the ``(symbol, timestamp, ...)``
    primary key, so unparseable dates are dropped instead.
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) \
            if value.tzinfo else value
    if isinstance(value, date_type):
        return datetime.combine(value, time.min)
    text = str(value or "").strip()
    if not text:
        return None
    # "20261105 16:00:00 US/Eastern" — the exchange suffix is not ISO.
    head = text.split(" ")[0] if " " in text and not text[10:11] == "T" else text
    try:
        if len(head) == 8 and head.isdigit():
            return datetime.strptime(head, "%Y%m%d")
        parsed = datetime.fromisoformat(head.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


class MarketDataService:
    """Synchronous orchestrator; all IBKR calls go through one adapter."""

    def __init__(self, store: DataStore, universe: Optional[Universe] = None) -> None:
        self._store = store
        self._universe = universe or Universe(store)

    @property
    def universe(self) -> Universe:
        return self._universe

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def sync(self, adapter, symbols: Optional[List[str]] = None,
             duration: Optional[str] = None,
             bar_size: Optional[str] = None,
             with_quotes: bool = True) -> Dict[str, Any]:
        """Refresh every requested symbol through the supplied IBKR session.

        Never aborts the whole batch because one symbol fails: each result
        carries its own status and error message, and each gets an audit row.
        """
        targets = symbols or self._universe.symbols()
        duration = duration or config.HISTORY_DURATION
        bar_size = bar_size or config.HISTORY_BAR_SIZE
        results: List[Dict[str, Any]] = []
        total_bars = 0

        for symbol in targets:
            started = _utcnow()
            status, error, bars_count, quote_ok = "ok", None, 0, False
            try:
                self._ingest_contract(adapter, symbol)
                bars_count = self._ingest_bars(
                    adapter, symbol, duration, bar_size
                )
                total_bars += bars_count
                if with_quotes:
                    quote_ok = self._ingest_quote(adapter, symbol)
            except Exception as exc:  # noqa: BLE001 - one bad ticker is not fatal
                status, error = "error", str(exc)
                logger.warning("Data sync failed for %s: %s", symbol, exc)

            self._store.log_data_update(DataUpdate(
                symbol=symbol,
                update_type="full_sync",
                records_inserted=bars_count,
                started_at=started,
                completed_at=_utcnow(),
                status=status,
                error_message=error,
                source="IBKR",
            ))
            results.append({
                "symbol": symbol, "status": status, "bars": bars_count,
                "quote": quote_ok, "error": error,
            })

        return {
            "at": _utcnow().isoformat(),
            "symbols": len(targets),
            "bars": total_bars,
            "ok": sum(1 for r in results if r["status"] == "ok"),
            "results": results,
        }

    def _ingest_contract(self, adapter, symbol: str) -> None:
        info = adapter.get_contract_details(symbol)
        if not info or not info.get("con_id"):
            return
        self._store.insert_contract(ContractInfo(
            symbol=symbol,
            con_id=int(info["con_id"]),
            exchange=info.get("exchange") or "SMART",
            currency=info.get("currency") or "USD",
            asset_class=AssetClass.EQUITY,
            sector=info.get("category"),
            industry=info.get("industry"),
            multiplier=float(info.get("multiplier") or 1.0),
            min_tick=info.get("min_tick"),
            trading_class=info.get("trading_class"),
            source="IBKR",
        ))
        self._universe.update_meta(
            symbol,
            name=info.get("long_name"),
            exchange=info.get("exchange"),
            currency=info.get("currency"),
            sector=info.get("category"),
            industry=info.get("industry"),
        )
        # Preserve any debt ratio/sector entered by hand: API metadata must
        # never wipe the data the compliance gate depends on.
        existing = self._store.get_fundamental_data(symbol)
        if existing is None:
            self._store.upsert_fundamental_data(FundamentalData(
                symbol=symbol,
                sector=info.get("category"),
                industry=info.get("industry"),
                currency=info.get("currency") or "USD",
                source="IBKR-CONTRACT",
            ))
        elif not existing.sector and info.get("category"):
            self._store.upsert_fundamental_data(existing.model_copy(
                update={"sector": info["category"],
                        "industry": existing.industry or info.get("industry")}
            ))

    def _ingest_bars(self, adapter, symbol: str,
                     duration: str, bar_size: str) -> int:
        raw = adapter.fetch_historical_bars(symbol, duration, bar_size)
        bars: List[PriceBar] = []
        for item in raw or []:
            stamp = _parse_bar_date(item.get("timestamp"))
            if stamp is None:
                logger.debug("Skipping bar with unparseable date for %s: %r",
                             symbol, item.get("timestamp"))
                continue
            try:
                bars.append(PriceBar(
                    symbol=symbol,
                    timestamp=stamp,
                    open=float(item["open"]), high=float(item["high"]),
                    low=float(item["low"]), close=float(item["close"]),
                    volume=int(item["volume"] or 0),
                    bar_size=bar_size, what_to_show="TRADES", source="IBKR",
                ))
            except (ValueError, KeyError, TypeError) as exc:
                logger.debug("Skipping malformed bar for %s: %s", symbol, exc)
        if bars:
            self._store.insert_price_bars(bars)
        return len(bars)

    def _ingest_quote(self, adapter, symbol: str) -> bool:
        quote = adapter.request_market_data(symbol)
        if not quote:
            return False
        volume = quote.get("volume")
        self._store.insert_market_quote(MarketQuote(
            symbol=symbol,
            con_id=quote.get("con_id"),
            bid=quote.get("bid"), ask=quote.get("ask"),
            last=quote.get("last"),
            bid_size=quote.get("bid_size"), ask_size=quote.get("ask_size"),
            volume=int(volume) if volume else None,
            high=quote.get("high"), low=quote.get("low"),
            close=quote.get("close"),
            timestamp_utc=_utcnow(), source="IBKR",
        ))
        return True

    # ------------------------------------------------------------------
    # Compliance data the IBKR paper account cannot supply
    # ------------------------------------------------------------------

    def set_fundamental(
        self,
        symbol: str,
        *,
        sector: Optional[str] = None,
        industry: Optional[str] = None,
        debt_to_market_cap: Optional[float] = None,
        market_cap: Optional[float] = None,
        total_debt: Optional[float] = None,
    ) -> None:
        """Attach/override the impact screen's inputs for one instrument.

        ``reqFundamentalData`` is disabled on this paper account (IB error
        10358), so debt ratios and excluded sectors are entered from the
        cockpit. Every value is stored in the same ``fundamental_data`` table
        the compliance gate reads — no parallel source of truth.
        """
        value = normalize_symbol(symbol)
        ratio = (
            float(debt_to_market_cap)
            if debt_to_market_cap is not None else
            float(total_debt) / float(market_cap)
            if total_debt is not None and market_cap else None
        )
        now = _utcnow().replace(tzinfo=None)
        existing = self._store.get_fundamental_data(value)
        supplied = {
            "sector": sector or None,
            "industry": industry or None,
            "debt_to_market_cap": ratio,
            "market_cap": float(market_cap) if market_cap else None,
            "total_debt": float(total_debt) if total_debt else None,
        }
        if existing is None:
            data = FundamentalData(
                symbol=value, source="COCKPIT", last_updated=now, **supplied
            )
        else:
            # Keep API-discovered metadata when the user only fills the ratio.
            updates = {
                key: val for key, val in supplied.items() if val is not None
            }
            updates.update({"last_updated": now, "source": "COCKPIT"})
            data = existing.model_copy(update=updates)
        self._store.upsert_fundamental_data(data)

    def price_series(self, symbol: str,
                     limit: int = 300) -> List[Dict[str, Any]]:
        """Daily OHLCV rows, oldest first, ready for a chart or a dataframe."""
        rows = self._store.conn.execute(
            """SELECT timestamp, open, high, low, close, volume
               FROM price_bars WHERE symbol = ? AND bar_size = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (symbol, config.HISTORY_BAR_SIZE, int(limit)),
        ).fetchall()
        keys = ("timestamp", "open", "high", "low", "close", "volume")
        return [dict(zip(keys, row)) for row in reversed(rows)]

    # ------------------------------------------------------------------
    # Coverage — what data exists, how fresh it is, what it enables
    # ------------------------------------------------------------------

    def coverage(self) -> List[Dict[str, Any]]:
        """One row per active instrument with its complete data status."""
        c = self._store.conn
        bars = {
            row[0]: {"count": row[1], "first": row[2], "last": row[3]}
            for row in c.execute(
                """SELECT symbol, COUNT(*), MIN(timestamp), MAX(timestamp)
                   FROM price_bars WHERE bar_size = ? GROUP BY symbol""",
                (config.HISTORY_BAR_SIZE,),
            )
        }
        last_close = {
            row[0]: row[1]
            for row in c.execute(
                """SELECT p.symbol, p.close FROM price_bars p
                   JOIN (SELECT symbol, MAX(timestamp) AS mt FROM price_bars
                         GROUP BY symbol) x
                     ON x.symbol = p.symbol AND x.mt = p.timestamp"""
            )
        }
        quotes = {
            row[0]: {"bid": row[1], "ask": row[2], "last": row[3],
                     "at": row[4]}
            for row in c.execute(
                """SELECT q.symbol, q.bid, q.ask, q.last, q.timestamp_utc
                   FROM market_quotes q
                   WHERE q.id IN (SELECT MAX(id) FROM market_quotes
                                  GROUP BY symbol)"""
            )
        }
        syncs = {
            row[0]: row[1]
            for row in c.execute(
                """SELECT symbol, MAX(completed_at) FROM data_updates
                   WHERE status = 'ok' GROUP BY symbol"""
            )
        }
        fundamentals = {
            row[0]: row[1] is not None
            for row in c.execute(
                "SELECT symbol, debt_to_market_cap FROM fundamental_data"
            )
        }

        stale_after = timedelta(hours=config.DATA_STALE_HOURS)
        now = datetime.now(timezone.utc)
        rows: List[Dict[str, Any]] = []
        for instrument in self._universe.instruments():
            if not instrument.active:
                continue
            symbol = instrument.symbol
            bar_info = bars.get(symbol, {"count": 0, "first": None, "last": None})
            quote = quotes.get(symbol, {})
            synced = syncs.get(symbol)
            synced_dt = _parse_stamp(synced) if synced else None
            fresh = bool(
                synced_dt
                and now - synced_dt.replace(tzinfo=timezone.utc) <= stale_after
            )
            count = int(bar_info["count"])
            if count >= config.MIN_BARS_FOR_SIGNALS and fresh:
                status = "ready"
            elif count > 0:
                status = "partial"
            else:
                status = "empty"
            rows.append({
                "symbol": symbol,
                "name": instrument.name or symbol,
                "sector": instrument.sector,
                "industry": instrument.industry,
                "exchange": instrument.exchange,
                "bars": count,
                "first_bar": bar_info["first"],
                "last_bar": bar_info["last"],
                "last_close": last_close.get(symbol),
                "quote": quote,
                "fundamentals": fundamentals.get(symbol, False),
                "last_sync": synced,
                "fresh": fresh,
                "status": status,
            })
        return rows

    def sync_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._store.conn.execute(
            """SELECT symbol, update_type, records_inserted, started_at,
                      completed_at, status, error_message
               FROM data_updates ORDER BY id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        keys = ("symbol", "update_type", "records", "started_at",
                "completed_at", "status", "error")
        return [dict(zip(keys, row)) for row in rows]

    def summary(self) -> Dict[str, Any]:
        rows = self.coverage()
        return {
            "instruments": len(rows),
            "ready": sum(1 for r in rows if r["status"] == "ready"),
            "bars": sum(r["bars"] for r in rows),
            "with_quote": sum(1 for r in rows if r["quote"]),
            "with_fundamentals": sum(1 for r in rows if r["fundamentals"]),
        }
