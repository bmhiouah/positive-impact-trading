"""IBKR Gateway adapter — one dedicated event loop owns the whole session.

Design
------
There is exactly ONE paper session: ``127.0.0.1:4002``, client id ``1``. The
host, port and client id are fixed constants in ``marketmind/config.py`` — they
are not configuration and are never exposed to the user. Capital is likewise
never a parameter: it is read back from the account through the API.

``ib_insync`` is not thread-safe and, on Python 3.14, is picky about which
event loop created its Futures. A single background thread therefore owns ONE
loop for the entire session: ``IB()``, connect, data requests, order placement
and disconnect all happen on that same loop. Callers (Streamlit, the runner
thread, tests) only ever call synchronous methods; those methods submit a
*factory* to the IB loop and block on the result with a hard timeout.

A long entry is a parent buy plus a GTC stop child. There is no take-profit.
The stop price is chosen by the caller (two average ranges under the entry).
A close is a market sell of shares already held. A buy with no reference
price, or a stop that is not below that price, is refused.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from marketmind import ib_compat  # noqa: F401 - must be first (Py3.14 import fix)
from marketmind import config

from ib_insync import (
    IB,
    LimitOrder,
    MarketOrder,
    Order as IBOrder,
    StopOrder,
)

from marketmind.broker.contracts import Listing, ListingBook, make_stock
from marketmind.compliance.engine import ExecutionDecision, ImpactExecutionEngine
from marketmind.data.models import AccountSnapshot

logger = logging.getLogger(__name__)

# IB error codes that mean "the order was rejected".
_ORDER_REJECT_CODES = {201, 202, 203, 321, 404, 10268}
_ACCEPTED_STATUSES = {"Submitted", "PreSubmitted", "Filled"}

_CLIENT_ID_BUSY_CODES = frozenset({326, 502, 504, 1100, 2110})


def _exception_text(exc: BaseException) -> str:
    """Flatten an exception and its causes into one searchable string."""
    parts: List[str] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        parts.append(type(current).__name__ if not text else f"{type(current).__name__}: {text}")
        current = current.__cause__ or current.__context__
    return " | ".join(parts)


def format_gateway_error(exc: BaseException) -> str:
    """User-facing IB Gateway connect error — never an empty ``TimeoutError()``.

    Read Account / Start Algo surface this string in the cockpit. Classify the
    common paper-session failures (port refused, handshake timeout, client id
    busy) so the Trading tab tells Badre what to do next.
    """
    host = config.IBKR_HOST
    port = config.IBKR_PORT
    client_id = config.IBKR_CLIENT_ID
    chain = _exception_text(exc)
    lowered = chain.lower()
    raw = str(exc).strip()

    if (
        "already in use" in lowered
        or "client id 1 is already in use" in lowered
        or " error: 326:" in lowered
        or ": 326:" in lowered
    ):
        return (
            f"IBKR client id {client_id} is already in use on {host}:{port}. "
            "Stop the algo (or close any other API client), wait a second, "
            "then click Read account again."
        )

    refused = isinstance(exc, ConnectionRefusedError)
    cause: Optional[BaseException] = exc
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ConnectionRefusedError):
            refused = True
            break
        cause = cause.__cause__ or cause.__context__
    if refused or "connection refused" in lowered or "errno 61" in lowered:
        return (
            f"IB Gateway is not accepting connections on {host}:{port}. "
            "Open IB Gateway, log into the paper account, wait until the API "
            "socket is listening, then click Read account again."
        )

    timed_out = isinstance(exc, TimeoutError) or "timeout" in lowered
    if timed_out:
        detail = raw if raw and raw not in {".", ":", "()"} else "handshake timed out"
        return (
            f"IBKR Gateway connection timed out at {host}:{port} ({detail}). "
            "Confirm Gateway is logged into paper and client id 1 is free, "
            "then try Read account again."
        )

    # Avoid "…4002: . Check…" when str(exc) is empty (bare TimeoutError, etc.).
    detail = raw if raw else type(exc).__name__
    blank_wrap = ": . " in detail or detail.endswith(": .")
    already = (
        detail.startswith("IBKR Gateway ")
        or detail.startswith("IB Gateway is not accepting")
        or detail.startswith(f"IBKR client id {client_id}")
    )
    if already and not blank_wrap:
        return detail
    if blank_wrap:
        detail = type(exc).__name__

    return (
        f"IBKR Gateway connection failed at {host}:{port}: {detail}. "
        "Check that the Gateway is running, you are logged into the "
        "paper account, API sockets are enabled, and that no other "
        "client is using client id 1."
    )


@dataclass(frozen=True)
class OrderFill:
    """A confirmed order fill from IBKR."""

    order_id: int
    symbol: str
    side: str
    quantity: float
    fill_price: float
    filled_at: str


@dataclass(frozen=True)
class ConnectionInfo:
    """What a successful connect() returns."""

    account: Optional[str]
    accounts: List[str] = field(default_factory=list)
    server_version: int = 0
    connected_at: str = ""


def clean_legacy_order_fields(order: IBOrder) -> IBOrder:
    """Neutralise legacy order attributes that modern Gateways reject.

    ib_insync 0.9.50 defaults ``eTradeOnly=True`` and ``firmQuoteOnly=True``.
    Gateway serverVersion >= 145 encodes them unconditionally and replies with
    error 10268 ("The 'EtradeOnly' order attribute is not supported"). Every
    leg of every order must be sanitised before submission.
    """
    order.eTradeOnly = False
    order.firmQuoteOnly = False
    order.nbboPriceCap = None
    return order


def protective_stop(reference: float, stop: float) -> float:
    """A sell-stop that sits strictly below the reference price."""
    try:
        ref = float(reference)
        level = round(float(stop), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid stop against reference {reference!r}") from exc
    if not math.isfinite(ref) or ref <= 0 or not math.isfinite(level):
        raise ValueError(f"invalid stop {stop!r} against reference {reference!r}")
    if not 0 < level < ref:
        raise ValueError(
            f"stop {level} must sit strictly below the reference {ref}"
        )
    return level


def limit_target(reference: float, target: float) -> float:
    """A sell-limit that sits strictly above the reference price."""
    try:
        ref = float(reference)
        level = round(float(target), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid target against reference {reference!r}") from exc
    if not math.isfinite(ref) or ref <= 0 or not math.isfinite(level) or level <= ref:
        raise ValueError(
            f"target {target!r} must sit strictly above the reference {reference!r}"
        )
    return level


def build_protected_entry(ib: IB, action: str, quantity: float,
                          entry_limit: Optional[float], stop_price: float,
                          account: Optional[str] = None,
                          target_price: Optional[float] = None
                          ) -> Tuple[IBOrder, IBOrder, Optional[IBOrder]]:
    """Parent buy, a GTC stop, and an optional GTC target.

    The stop is good-til-cancelled because the position is held for weeks.
    A day order would expire and leave the shares unprotected overnight.
    When a target is present it is transmitted before the stop, and the stop
    is the leg that releases the bracket.
    """
    parent_id = ib.client.getReqId()
    reverse = "SELL" if action == "BUY" else "BUY"

    if entry_limit and entry_limit > 0:
        parent = LimitOrder(action, quantity, float(entry_limit))
    else:
        parent = MarketOrder(action, quantity)
    parent.orderId = parent_id
    parent.transmit = False

    target = None
    if target_price and target_price > 0:
        target = LimitOrder(
            reverse, quantity, float(target_price),
            orderId=ib.client.getReqId(), parentId=parent_id, transmit=False,
        )
    stop_loss = StopOrder(
        reverse, quantity, float(stop_price),
        orderId=ib.client.getReqId(), parentId=parent_id, transmit=True,
    )
    legs = (parent, stop_loss) if target is None else (parent, target, stop_loss)
    for leg in legs:
        clean_legacy_order_fields(leg)
        leg.tif = "GTC"
        leg.outsideRth = False
        if account:
            leg.account = account
    return parent, stop_loss, target


def _num(value: Any, default: float = 0.0) -> float:
    """Convert an IB value to a finite float (IB uses NaN for missing ticks)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _maybe_num(value: Any) -> Optional[float]:
    """Like _num, but returns None for missing/non-finite values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _non_negative(value: Any) -> Optional[float]:
    """IBKR uses ``-1`` / NaN for unavailable ticks (e.g. bid/ask when closed).

    A sentinel is *missing*, not a price: storing it would fail the
    ``MarketQuote`` model and could anchor a TP/SL bracket at nonsense.
    """
    number = _maybe_num(value)
    return number if number is not None and number >= 0 else None


def _pick_tag(values, tags, default: float = 0.0) -> float:
    """Read the first available account tag, preferring BASE/USD currencies."""
    tag_list = tags if isinstance(tags, (tuple, list)) else (tags,)
    matches = [v for v in values if v.tag in tag_list]
    for currency in ("BASE", "USD", "EUR", ""):
        for value in matches:
            if value.currency == currency:
                return _num(value.value, default)
    return _num(matches[0].value, default) if matches else default


_TEXT_SENTINELS = frozenset({"BASE", "TOTAL", "ALL", "NONE", "N/A", "-"})


def _pick_text_tag(values, tags, default: str = "USD") -> str:
    """Read a non-numeric account tag such as the base currency.

    IBKR repeats the ``$LEDGER-*`` tags once per currency plus a synthetic
    ``BASE`` summary row. On that row ``$LEDGER-Currency`` holds the literal
    string ``BASE`` while ``$LEDGER-RealCurrency`` holds the real code (``EUR``
    here). Respect the caller's tag order and discard sentinels, otherwise the
    summary label is mistaken for a currency and the whole UI shows "BASE".
    """
    tag_list = tags if isinstance(tags, (tuple, list)) else (tags,)
    matches = [v for v in values if v.tag in tag_list]

    def clean(value) -> Optional[str]:
        text = str(value.value).strip().upper()
        return text if text and text not in _TEXT_SENTINELS else None

    for tag in tag_list:
        for currency in ("BASE", "USD", "EUR", ""):
            for value in matches:
                if value.tag == tag and value.currency == currency:
                    text = clean(value)
                    if text:
                        return text
    return default


_ACCOUNT_TAGS: Dict[str, Any] = {
    "net_liquidation": ("NetLiquidation", "$LEDGER-NetLiquidationByCurrency"),
    "total_cash_value": ("TotalCashValue", "$LEDGER-TotalCashBalance"),
    "cash_balance": ("CashBalance", "$LEDGER-CashBalance"),
    "buying_power": ("BuyingPower",),
    "available_funds": ("AvailableFunds",),
    "excess_liquidity": ("ExcessLiquidity",),
    "gross_position_value": (
        "GrossPositionValue", "StockMarketValue", "$LEDGER-StockMarketValue",
    ),
    "unrealized_pnl": ("UnrealizedPnL", "$LEDGER-UnrealizedPnL"),
    "realized_pnl": ("RealizedPnL", "$LEDGER-RealizedPnL"),
    "accrued_cash": ("AccruedCash", "$LEDGER-AccruedCash"),
}


class IBKRAdapter:
    """Thread-safe, synchronous facade over the single IBKR paper session.

    ``connect()`` always tears down any previous session first and makes ONE
    handshake attempt with the fixed client id. There is no client-id fallback:
    this project uses one connection at a time, period.
    """

    def __init__(
        self,
        engine: Optional[ImpactExecutionEngine] = None,
        connect_timeout: float = 8.0,
        op_timeout: float = 15.0,
        listings: Optional[ListingBook] = None,
    ) -> None:
        self._engine = engine or ImpactExecutionEngine(auto_submit=False)
        self._account: Optional[str] = None   # discovered from managedAccounts()
        self._connect_timeout = float(connect_timeout)
        self._op_timeout = float(op_timeout)
        # Per-symbol currency/exchange (stock specs / instrument meta). Default
        # USD/SMART remains for names with no listing — that is the US seed path.
        self._listings = listings if listings is not None else ListingBook()

        self._ib: Optional[IB] = None            # owned by the loop thread only
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()
        self._lock = threading.RLock()
        self._connected = False
        self._connection_info: Optional[ConnectionInfo] = None
        self._last_error: Optional[str] = None

    @property
    def listings(self) -> ListingBook:
        """Mutable book of currency/exchange per ticker for Stock contracts."""
        return self._listings

    def listing_for(self, symbol: str) -> Listing:
        """Resolved listing for *symbol* (USD/SMART when unknown)."""
        return self._listings.resolve(symbol)

    def _stock(self, symbol: str):
        """Currency-aware IBKR Stock contract for *symbol*."""
        listing = self.listing_for(symbol)
        return make_stock(
            listing.symbol, currency=listing.currency, exchange=listing.exchange,
        )

    # ------------------------------------------------------------------
    # Properties (safe to read from any thread)
    # ------------------------------------------------------------------

    @property
    def account(self) -> Optional[str]:
        return self._account

    @property
    def connection_info(self) -> Optional[ConnectionInfo]:
        return self._connection_info

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ib is not None

    # ------------------------------------------------------------------
    # Loop-thread lifecycle
    # ------------------------------------------------------------------

    def _start_loop(self) -> None:
        """Create the single event loop that will own the IB session."""
        if self._loop is not None and self._loop.is_running():
            return
        self._loop = asyncio.new_event_loop()
        self._started.clear()
        loop = self._loop

        def _run() -> None:
            asyncio.set_event_loop(loop)
            ib_compat.ensure_event_loop()
            self._started.set()
            loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True,
                                        name="marketmind-ibkr-loop")
        self._thread.start()
        if not self._started.wait(timeout=5.0) or not loop.is_running():
            raise RuntimeError("IBKR event-loop thread failed to start")

    def _stop_loop(self) -> None:
        """Cancel leftovers, stop and close the loop thread."""
        loop, thread = self._loop, self._thread
        if loop is not None and loop.is_running():
            try:
                self._submit(self._shutdown_on_loop, timeout=5.0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Loop shutdown note: %s", exc)
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5.0)
        if loop is not None and not loop.is_closed():
            try:
                loop.close()
            except RuntimeError as exc:
                logger.debug("Loop close note: %s", exc)
        self._loop = None
        self._thread = None
        self._ib = None

    async def _shutdown_on_loop(self) -> None:
        current = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks() if t is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)

    def _submit(self, factory: Callable[[], Any],
                timeout: Optional[float] = None) -> Any:
        """Run ``factory()`` ON the IB loop and return its result."""
        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("Not connected to IBKR Gateway")
        limit = timeout if timeout is not None else self._op_timeout

        async def _wrapper() -> Any:
            result = factory()
            if inspect.isawaitable(result):
                return await result
            return result

        future = asyncio.run_coroutine_threadsafe(_wrapper(), loop)
        try:
            return future.result(timeout=limit)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"IBKR operation timed out after {limit}s") from exc

    def _require_ib(self) -> IB:
        if not self._connected or self._ib is None:
            raise RuntimeError("Not connected to IBKR Gateway")
        return self._ib

    # ------------------------------------------------------------------
    # Connect / disconnect / reconnect
    # ------------------------------------------------------------------

    async def _connect_on_loop(self) -> ConnectionInfo:
        """Handshake, executed entirely on the IB loop."""
        ib = IB()
        errors: List[tuple[int, str]] = []

        def on_error(_req_id, code, message, _contract):
            errors.append((int(code), str(message)))

        ib.errorEvent += on_error
        try:
            await asyncio.wait_for(
                ib.connectAsync(
                    config.IBKR_HOST, config.IBKR_PORT,
                    clientId=config.IBKR_CLIENT_ID,
                    timeout=self._connect_timeout, readonly=False,
                ),
                timeout=self._connect_timeout + 5,
            )
        except Exception as exc:  # noqa: BLE001
            self._safe_disconnect(ib)
            # Error 326 often arrives as a peer-close + empty TimeoutError();
            # classify the IB code before the generic handshake wrapper.
            if any(code in _CLIENT_ID_BUSY_CODES for code, _ in errors):
                raise RuntimeError(
                    "client id 1 is already in use — close the other IBKR API "
                    "client before starting the algo (only one connection is "
                    "allowed)"
                ) from exc
            detail = "; ".join(f"{c}: {m}" for c, m in errors[:3])
            kind = type(exc).__name__
            body = str(exc).strip() or kind
            suffix = f" IB error: {detail}" if detail else ""
            raise RuntimeError(
                f"handshake failed ({kind}: {body}).{suffix}"
            ) from exc
        finally:
            ib.errorEvent -= on_error

        if not ib.isConnected():
            self._safe_disconnect(ib)
            raise RuntimeError("socket closed immediately after handshake")
        if any(code in _CLIENT_ID_BUSY_CODES for code, _ in errors):
            self._safe_disconnect(ib)
            raise RuntimeError(
                "client id 1 is already in use — close the other IBKR API client "
                "before starting the algo (only one connection is allowed)"
            )

        ib.disconnectedEvent += self._on_disconnected
        ib.reqMarketDataType(config.MARKET_DATA_TYPE)
        self._ib = ib
        self._connected = True

        accounts = list(ib.managedAccounts() or [])
        if not self._account and accounts:
            self._account = accounts[0]
        info = ConnectionInfo(
            account=self._account,
            accounts=accounts,
            server_version=int(ib.client.serverVersion() or 0),
            connected_at=datetime.now(timezone.utc).isoformat(),
        )
        self._connection_info = info
        logger.info("Connected to IBKR paper account %s (serverVersion=%s)",
                    info.account, info.server_version)
        return info

    def _on_disconnected(self) -> None:
        """Gateway dropped the socket (daily restart, logout, network)."""
        logger.warning("IBKR Gateway disconnected the API session")
        self._connected = False

    @staticmethod
    def _safe_disconnect(ib: IB) -> None:
        try:
            ib.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.debug("disconnect note: %s", exc)
        finally:
            try:
                ib.client.conn = None
            except AttributeError:
                pass

    async def _disconnect_on_loop(self) -> None:
        ib = self._ib
        self._ib = None
        self._connected = False
        self._connection_info = None
        if ib is None:
            return
        try:
            try:
                ib.disconnectedEvent -= self._on_disconnected
            except (KeyError, ValueError):
                pass
            ib.disconnect()
            # Let asyncio process connection_lost so the client id is freed.
            await asyncio.sleep(0.25)
        finally:
            try:
                ib.client.conn = None
            except AttributeError:
                pass
        logger.info("Disconnected from IBKR Gateway")

    def connect(self) -> ConnectionInfo:
        """Open the one paper session. Mandatory — raises on failure.

        Always tears down any previous session first, then makes a single
        handshake attempt and proves the session really serves account data.
        """
        with self._lock:
            self.disconnect()
            self._last_error = None
            try:
                self._start_loop()
                info = self._submit(
                    self._connect_on_loop,
                    timeout=self._connect_timeout + 8,
                )
                # A socket alone is not proof: read real account values.
                self.get_account_snapshot()
                return info
            except Exception as exc:  # noqa: BLE001
                self._teardown_after_failed_connect()
                message = format_gateway_error(exc)
                self._last_error = message
                raise RuntimeError(message) from exc

    def _teardown_after_failed_connect(self) -> None:
        try:
            self._submit(self._disconnect_on_loop, timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("cleanup note: %s", exc)
        self._stop_loop()

    def disconnect(self) -> None:
        """Gracefully close the socket and stop the loop thread."""
        with self._lock:
            if self._loop is None:
                self._connected = False
                self._ib = None
                self._connection_info = None
                return
            try:
                self._submit(self._disconnect_on_loop, timeout=6.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("IBKR disconnect note: %s", exc)
            self._stop_loop()

    def reconnect(self) -> ConnectionInfo:
        """Full stop/start cycle — exactly what a repeated Start Algo does."""
        self.disconnect()
        return self.connect()

    def health_check(self) -> bool:
        """True only when the socket is really up (not just a stale flag)."""
        if not self.is_connected:
            return False
        try:
            return bool(self._submit(lambda: self._ib.isConnected(), timeout=3.0))
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # Account & market data
    # ------------------------------------------------------------------

    def _read_account_snapshot(self) -> AccountSnapshot:
        ib = self._require_ib()
        values = ib.accountValues()
        if not values:
            raise RuntimeError("IBKR returned no account values")
        kwargs = {name: _pick_tag(values, tags)
                  for name, tags in _ACCOUNT_TAGS.items()}
        kwargs["total_positions_value"] = kwargs["gross_position_value"]
        kwargs["currency"] = _pick_text_tag(
            values, ("$LEDGER-RealCurrency", "$LEDGER-Currency"), "USD"
        )
        return AccountSnapshot(
            timestamp_utc=datetime.now(timezone.utc),
            account_id=self._account,
            source="IBKR",
            **kwargs,
        )

    def get_account_snapshot(self) -> AccountSnapshot:
        """Return a typed account snapshot (NetLiquidation, cash, P&L...)."""
        return self._submit(self._read_account_snapshot,
                            timeout=self._op_timeout)

    def get_account_summary(self) -> Dict[str, float]:
        """Account tags as a plain dict, for quick inspection and tests."""
        snap = self.get_account_snapshot()
        return {
            "NetLiquidation": snap.net_liquidation,
            "TotalCashValue": snap.total_cash_value,
            "CashBalance": snap.cash_balance,
            "BuyingPower": snap.buying_power,
            "AvailableFunds": snap.available_funds,
            "ExcessLiquidity": snap.excess_liquidity,
            "GrossPositionValue": snap.gross_position_value,
            "UnrealizedPnL": snap.unrealized_pnl,
            "RealizedPnL": snap.realized_pnl,
            "TotalPositionsValue": snap.total_positions_value,
            "AccruedCash": snap.accrued_cash,
        }

    @property
    def account_currency(self) -> str:
        """Base currency of the paper account, read from IBKR tags."""
        return self.get_account_snapshot().currency

    async def _history_on_loop(self, symbol: str, duration: str,
                               bar_size: str) -> List[Dict[str, Any]]:
        """Daily OHLCV bars, executed on the IB event loop."""
        ib = self._require_ib()
        contract = self._stock(symbol)
        await ib.qualifyContractsAsync(contract)
        if not contract.conId:
            return []
        bars = await asyncio.wait_for(
            ib.reqHistoricalDataAsync(
                contract, endDateTime="", durationStr=duration,
                barSizeSetting=bar_size, whatToShow="TRADES",
                useRTH=True, formatDate=1, keepUpToDate=False,
            ),
            timeout=max(self._op_timeout, 25.0),
        )
        result: List[Dict[str, Any]] = []
        for bar in bars or []:
            date = bar.date
            stamp = date.isoformat() if hasattr(date, "isoformat") else str(date)
            result.append({
                "symbol": symbol.upper(),
                "timestamp": stamp,
                "open": _num(bar.open), "high": _num(bar.high),
                "low": _num(bar.low), "close": _num(bar.close),
                "volume": int(_num(bar.volume)),
                "bar_size": bar_size, "what_to_show": "TRADES",
            })
        return result

    def fetch_historical_bars(self, symbol: str,
                              duration: Optional[str] = None,
                              bar_size: Optional[str] = None
                              ) -> List[Dict[str, Any]]:
        """Public, thread-safe wrapper around the historical data request."""
        return self._submit(
            lambda: self._history_on_loop(
                symbol, duration or config.HISTORY_DURATION,
                bar_size or config.HISTORY_BAR_SIZE,
            ),
            timeout=max(self._op_timeout, 30.0),
        )

    @staticmethod
    async def _snapshot_ticker(ib: IB, contract) -> Any:
        # snapshot=True ends the subscription itself; cancelMktData afterwards
        # would only produce a spurious IB error 300.
        ticker = ib.reqMktData(contract, "", True, False)
        for _ in range(25):
            await asyncio.sleep(0.2)
            if _num(ticker.marketPrice()) > 0:
                break
        return ticker

    @staticmethod
    def reference_price(ticker, fallback: Optional[float] = None) -> Optional[float]:
        """Best available price to anchor TP / SL (mid, last, close, market)."""
        bid, ask = _maybe_num(ticker.bid), _maybe_num(ticker.ask)
        if bid and ask and bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        for value in (ticker.last, ticker.close, ticker.marketPrice()):
            number = _maybe_num(value)
            if number and number > 0:
                return number
        return fallback if fallback and fallback > 0 else None

    async def _quote_on_loop(self, symbol: str) -> Dict[str, Any]:
        ib = self._require_ib()
        contract = self._stock(symbol)
        await ib.qualifyContractsAsync(contract)
        ticker = await self._snapshot_ticker(ib, contract)
        return {
            "symbol": symbol,
            "con_id": contract.conId,
            "bid": _non_negative(ticker.bid),
            "ask": _non_negative(ticker.ask),
            "last": _non_negative(ticker.last),
            "bid_size": _non_negative(ticker.bidSize),
            "ask_size": _non_negative(ticker.askSize),
            "volume": _non_negative(ticker.volume),
            "high": _non_negative(ticker.high),
            "low": _non_negative(ticker.low),
            "close": _non_negative(ticker.close),
        }

    def request_market_data(self, symbol: str) -> Dict[str, Any]:
        """Request a snapshot quote for *symbol*."""
        return self._submit(lambda: self._quote_on_loop(symbol),
                            timeout=self._op_timeout)

    async def _contract_info_on_loop(self, symbol: str) -> Dict[str, Any]:
        ib = self._require_ib()
        contract = self._stock(symbol)
        details = await ib.reqContractDetailsAsync(contract)
        if not details:
            return {}
        c = details[0].contract
        detail = details[0]
        return {
            "con_id": c.conId,
            "symbol": c.symbol,
            "exchange": c.primaryExchange or c.exchange,
            "currency": c.currency,
            "multiplier": _num(getattr(c, "multiplier", 1.0), 1.0),
            "min_tick": _maybe_num(detail.minTick),
            "trading_class": getattr(c, "tradingClass", None),
            "long_name": getattr(detail, "longName", None),
            "category": getattr(detail, "category", None),
            "industry": getattr(detail, "industry", None),
        }

    def get_contract_details(self, symbol: str) -> Dict[str, Any]:
        return self._submit(lambda: self._contract_info_on_loop(symbol),
                            timeout=self._op_timeout)

    def get_positions(self) -> List[Dict[str, Any]]:
        ib = self._require_ib()
        rows = self._submit(lambda: list(ib.positions()), timeout=self._op_timeout)
        return [
            {
                "symbol": p.contract.symbol,
                "con_id": p.contract.conId,
                "exchange": getattr(p.contract, "primaryExchange", None),
                "currency": p.contract.currency,
                "position": _num(p.position),
                "avg_cost": _num(p.avgCost),
                "market_price": _num(getattr(p, "marketPrice", 0.0)),
                "market_value": _num(getattr(p, "marketValue", 0.0)),
                "unrealized_pnl": _num(getattr(p, "unrealizedPNL", 0.0)),
            }
            for p in rows
        ]

    def get_open_orders(self) -> List[Dict[str, Any]]:
        ib = self._require_ib()
        trades = self._submit(lambda: list(ib.openTrades()),
                              timeout=self._op_timeout)
        result = []
        for trade in trades:
            order = trade.order
            contract = trade.contract
            result.append({
                "order_id": order.orderId,
                "parent_id": getattr(order, "parentId", 0),
                "symbol": getattr(contract, "symbol", "") if contract else "",
                "con_id": getattr(contract, "conId", None) if contract else None,
                "exchange": getattr(contract, "exchange", None) if contract else None,
                "side": order.action,
                "order_type": order.orderType,
                "quantity": _num(order.totalQuantity),
                "filled_quantity": _num(trade.orderStatus.filled),
                "limit_price": _maybe_num(getattr(order, "lmtPrice", None)),
                "stop_price": _maybe_num(getattr(order, "auxPrice", None)),
                "avg_fill_price": _maybe_num(getattr(trade.orderStatus, "avgFillPrice", None)),
                "status": trade.orderStatus.status,
                "time_in_force": getattr(order, "tif", None),
            })
        return result

    def get_executions(self) -> List[Dict[str, Any]]:
        ib = self._require_ib()
        fills = self._submit(lambda: list(ib.fills()), timeout=self._op_timeout)
        return [
            {
                "exec_id": f.execution.execId,
                "order_id": f.execution.orderId,
                "symbol": f.contract.symbol,
                "side": f.execution.side,
                "quantity": _num(f.execution.shares),
                "price": _num(f.execution.price),
                "commission": _num(getattr(f.commissionReport, "commission", 0.0)),
                "currency": getattr(f.commissionReport, "currency", "USD") or "USD",
                "time": datetime.now(timezone.utc),
            }
            for f in fills
        ]

    async def _executions_since_on_loop(self, since: datetime) -> List[Dict[str, Any]]:
        """Fills since ``since``, requested on the IB loop."""
        from ib_insync import ExecutionFilter

        ib = self._require_ib()
        window = ExecutionFilter()
        # IB reads this as local gateway time: yyyymmdd-hh:mm:ss.
        moment = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        window.time = moment.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S")
        fills = await ib.reqExecutionsAsync(window)
        rows = []
        for fill in fills:
            execution = fill.execution
            rows.append({
                "order_id": execution.orderId,
                "symbol": fill.contract.symbol,
                "side": execution.side,
                "quantity": _num(execution.shares),
                "price": _num(execution.price),
            })
        return rows

    def executions_since(self, since: datetime) -> List[Dict[str, Any]]:
        """Executions from ``since`` onward. Empty when the Gateway has none."""
        return self._submit(
            lambda: self._executions_since_on_loop(since),
            timeout=self._op_timeout,
        )

    # ------------------------------------------------------------------
    # Order submission — compliance-gated. A buy always carries a GTC stop.
    # ------------------------------------------------------------------

    async def _await_status(self, trade, errors: List[tuple[int, str]],
                            label: str) -> str:
        status = ""
        for _ in range(50):  # up to ~10 s for the acknowledgement
            await asyncio.sleep(0.2)
            status = trade.orderStatus.status
            if status in _ACCEPTED_STATUSES or status in {
                "Cancelled", "Inactive", "ApiCancelled"
            }:
                break
        for code, message in errors:
            if code in _ORDER_REJECT_CODES:
                raise RuntimeError(f"IBKR rejected the {label} ({code}: {message})")
        if status not in _ACCEPTED_STATUSES:
            raise RuntimeError(f"IBKR did not accept the {label} (status={status!r})")
        return status

    async def _place_order_on_loop(self, order_intent) -> Dict[str, Any]:
        ib = self._require_ib()
        contract = self._stock(order_intent.symbol)
        await ib.qualifyContractsAsync(contract)
        if not contract.conId:
            raise RuntimeError(f"unknown contract {order_intent.symbol}")

        ticker = await self._snapshot_ticker(ib, contract)
        reference = self.reference_price(ticker, order_intent.limit_price)
        if reference is None:
            raise RuntimeError(
                f"no market price for {order_intent.symbol} — refusing to send "
                "a buy without a stop"
            )
        stop = protective_stop(reference, order_intent.stop_price)
        target_level = None
        if getattr(order_intent, "target_price", None):
            target_level = limit_target(reference, order_intent.target_price)
        parent, stop_loss, target = build_protected_entry(
            ib, order_intent.side.value, order_intent.quantity,
            order_intent.limit_price, stop, self._account, target_level,
        )

        errors: List[tuple[int, str]] = []

        def on_error(_req_id, code, message, _contract):
            errors.append((int(code), str(message)))

        ib.errorEvent += on_error
        try:
            parent_trade = ib.placeOrder(contract, parent)
            if target is not None:
                ib.placeOrder(contract, target)
            ib.placeOrder(contract, stop_loss)
            logger.info(
                "ENTRY SENT %s %s %s: id=%s ref=%.2f stop=%.2f (id=%s) target=%s",
                order_intent.side.value, order_intent.symbol,
                order_intent.quantity, parent.orderId, reference,
                stop, stop_loss.orderId,
                f"{target_level:.2f}" if target_level else "none",
            )
            status = await self._await_status(parent_trade, errors, "entry")
            return {
                "order_id": parent.orderId,
                "perm_id": parent_trade.order.permId,
                "status": status,
                "entry_type": parent.orderType,
                "entry_price": _maybe_num(getattr(parent, "lmtPrice", None)),
                "reference_price": reference,
                "take_profit_order_id": None if target is None else target.orderId,
                "take_profit_price": target_level,
                "stop_loss_order_id": stop_loss.orderId,
                "stop_loss_price": stop,
            }
        finally:
            ib.errorEvent -= on_error

    async def _place_close_on_loop(self, order_intent) -> Dict[str, Any]:
        """Market sell of a long already held. The resting stop is cancelled first."""
        ib = self._require_ib()
        contract = self._stock(order_intent.symbol)
        await ib.qualifyContractsAsync(contract)
        if not contract.conId:
            raise RuntimeError(f"unknown contract {order_intent.symbol}")
        order = MarketOrder("SELL", order_intent.quantity)
        order.orderId = ib.client.getReqId()
        order.transmit = True
        clean_legacy_order_fields(order)
        order.tif = "GTC"
        order.outsideRth = False
        if self._account:
            order.account = self._account

        errors: List[tuple[int, str]] = []

        def on_error(_req_id, code, message, _contract):
            errors.append((int(code), str(message)))

        ib.errorEvent += on_error
        try:
            trade = ib.placeOrder(contract, order)
            logger.info("EXIT SENT SELL %s %s id=%s",
                        order_intent.symbol, order_intent.quantity, order.orderId)
            status = await self._await_status(trade, errors, "exit")
            return {"order_id": order.orderId, "status": status}
        finally:
            ib.errorEvent -= on_error

    def submit_order(self, order_intent) -> Dict[str, Any]:
        """Validate through compliance, then send a buy with its GTC stop."""
        decision: ExecutionDecision = self._engine.submit(order_intent)
        if not decision.verdict.approved:
            return {
                "approved": False,
                "submitted": False,
                "order_id": None,
                "rejection_reason": decision.rejection_reason,
            }

        self._require_ib()
        try:
            result = self._submit(
                lambda: self._place_order_on_loop(order_intent),
                timeout=max(self._op_timeout, 20.0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Bracket submission failed for %s: %s",
                         order_intent.symbol, exc)
            raise RuntimeError(
                f"Failed to submit entry for {order_intent.symbol} to IBKR: {exc}"
            ) from exc
        return {
            "approved": True,
            "submitted": True,
            "rejection_reason": None,
            **result,
        }

    def submit_close(self, order_intent) -> Dict[str, Any]:
        """Validate a SELL, then send a market order that closes the long."""
        decision: ExecutionDecision = self._engine.submit(order_intent)
        if not decision.verdict.approved:
            return {
                "approved": False,
                "submitted": False,
                "order_id": None,
                "rejection_reason": decision.rejection_reason,
            }
        self._require_ib()
        try:
            result = self._submit(
                lambda: self._place_close_on_loop(order_intent),
                timeout=max(self._op_timeout, 20.0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Exit failed for %s: %s", order_intent.symbol, exc)
            raise RuntimeError(
                f"Failed to close {order_intent.symbol} at IBKR: {exc}"
            ) from exc
        return {
            "approved": True,
            "submitted": True,
            "rejection_reason": None,
            **result,
        }

    def modify_stop(self, order_id: int, stop_price: float) -> bool:
        """Raise a resting stop. Returns False when that order is not open."""
        level = round(float(stop_price), 2)

        def _modify() -> bool:
            ib = self._require_ib()
            for trade in list(ib.openTrades()):
                if trade.order.orderId != order_id or trade.isDone():
                    continue
                trade.order.auxPrice = level
                trade.order.tif = "GTC"
                clean_legacy_order_fields(trade.order)
                ib.placeOrder(trade.contract, trade.order)
                return True
            return False

        return bool(self._submit(_modify, timeout=self._op_timeout))

    def cancel_order(self, order_id: int) -> bool:
        """Cancel a single open order by id. Returns False if it is unknown."""
        def _cancel() -> bool:
            ib = self._require_ib()
            for trade in list(ib.trades()):
                if trade.order.orderId == order_id and not trade.isDone():
                    ib.cancelOrder(trade.order)
                    return True
            return False

        return bool(self._submit(_cancel, timeout=self._op_timeout))

    def cancel_orders(self, order_ids: List[int]) -> int:
        """Cancel a whole bracket (parent + TP + SL) by its order ids."""
        wanted = set(order_ids)

        def _cancel() -> int:
            ib = self._require_ib()
            count = 0
            for trade in list(ib.openTrades()):
                if trade.order.orderId in wanted and not trade.isDone():
                    ib.cancelOrder(trade.order)
                    count += 1
            return count

        return int(self._submit(_cancel, timeout=self._op_timeout))

    def cancel_all_orders(self) -> int:
        """Cancel every open order belonging to this client (safety helper).

        NOTE: with systematic TP/SL brackets, calling this on Stop Algo would
        strip the protection from open positions. The runner therefore leaves
        brackets resting at IBKR when it stops.
        """
        def _cancel_all() -> int:
            ib = self._require_ib()
            open_trades = list(ib.openTrades())
            for trade in open_trades:
                ib.cancelOrder(trade.order)
            return len(open_trades)

        return int(self._submit(_cancel_all, timeout=self._op_timeout))

    # ------------------------------------------------------------------
    # Async compatibility wrappers (never call these FROM the IB loop)
    # ------------------------------------------------------------------

    async def connect_async(self) -> ConnectionInfo:
        return await asyncio.to_thread(self.connect)

    async def disconnect_async(self) -> None:
        return await asyncio.to_thread(self.disconnect)

    async def get_account_snapshot_async(self) -> AccountSnapshot:
        return await asyncio.to_thread(self.get_account_snapshot)

    async def get_account_summary_async(self) -> Dict[str, float]:
        return await asyncio.to_thread(self.get_account_summary)

    async def submit_order_async(self, order_intent) -> Dict[str, Any]:
        return await asyncio.to_thread(self.submit_order, order_intent)

    async def get_positions_async(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_positions)

    async def get_open_orders_async(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_open_orders)

    async def get_executions_async(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_executions)

    async def req_market_data_async(self, symbol: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self.request_market_data, symbol)

    async def get_contract_details_async(self, symbol: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_contract_details, symbol)

    def __enter__(self) -> "IBKRAdapter":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.disconnect()
