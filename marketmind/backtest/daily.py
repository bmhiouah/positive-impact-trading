"""Six-month daily-bar backtest using local SQLite ``price_bars``.

Loads history from the lab database (no IB Gateway). Signal rules and
per-stock ATR / optional fixed TP–SL come from a ``StockSpec``. Costs use
IBKR Pro Fixed (min-aware) from ``marketmind.fees``.

The live paper runner is untouched: this module is a study path only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from marketmind import config
from marketmind import contest
from marketmind import momentum
from marketmind.fees.ibkr_us_equity import (
    DEFAULT_IBKR_PRO_FIXED,
    IbkrProFixed,
    buy_fees,
    sell_fees,
)
from marketmind.stock_specs.loader import StockSpec, load_stock_specs

logger = logging.getLogger(__name__)

# ~6 months of US equity sessions. Signal lookback still needs earlier bars
# in the store (e.g. m12 needs ~252 sessions before the window starts).
BACKTEST_SESSIONS = config.BACKTEST_SESSIONS
_STATE_ENTRIES = contest.STATE_ENTRIES


@dataclass(frozen=True)
class BacktestTrade:
    entry_day: str
    exit_day: str
    shares: int
    entry_price: float
    exit_price: float
    stop: float
    target: Optional[float]
    gross_pnl: float
    fees: float
    net_pnl: float
    reason: str


@dataclass(frozen=True)
class BacktestResult:
    ticker: str
    entry: str
    atr_multiple: float
    window_start: str
    window_end: str
    bars_in_window: int
    trades: tuple[BacktestTrade, ...]
    gross_pnl: float
    total_fees: float
    net_pnl: float
    skipped_reason: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "ticker": self.ticker,
            "entry": self.entry,
            "atr_multiple": self.atr_multiple,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "bars_in_window": self.bars_in_window,
            "trade_count": len(self.trades),
            "gross_pnl": round(self.gross_pnl, 4),
            "total_fees": round(self.total_fees, 4),
            "net_pnl": round(self.net_pnl, 4),
            "skipped_reason": self.skipped_reason,
        }


def _blank(spec: StockSpec, reason: str) -> BacktestResult:
    return BacktestResult(
        ticker=spec.ticker,
        entry=spec.entry,
        atr_multiple=spec.atr_multiple,
        window_start="",
        window_end="",
        bars_in_window=0,
        trades=tuple(),
        gross_pnl=0.0,
        total_fees=0.0,
        net_pnl=0.0,
        skipped_reason=reason,
    )


def _exit_levels(
    entry_px: float,
    atr_value: float,
    spec: StockSpec,
) -> tuple[float, Optional[float], float]:
    """Initial stop, optional target, and risk distance in price units."""
    if spec.fixed_stop_pct is not None:
        stop = round(entry_px * (1.0 - spec.fixed_stop_pct), 2)
    else:
        stop = round(entry_px - spec.atr_multiple * atr_value, 2)

    target: Optional[float] = None
    if spec.fixed_take_profit_pct is not None:
        target = round(entry_px * (1.0 + spec.fixed_take_profit_pct), 2)
    elif spec.target_r is not None:
        risk = entry_px - stop
        if risk > 0:
            target = round(entry_px + spec.target_r * risk, 2)

    risk = entry_px - stop
    return stop, target, risk


def _simulate_fee_aware(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
    signal: np.ndarray,
    known: np.ndarray,
    dates: Sequence[str],
    spec: StockSpec,
    *,
    start_index: int,
    equity: float,
    schedule: IbkrProFixed,
) -> List[BacktestTrade]:
    """Trade one name inside ``[start_index, n)`` with IBKR Fixed costs.

    Signals may use bars before ``start_index`` (already in the arrays).
    Entries only arm when the signal bar index is ``>= start_index - 1`` so
    the fill (next open) lands inside the six-month window.
    """
    trades: List[BacktestTrade] = []
    state = spec.entry in _STATE_ENTRIES
    in_pos = False
    cooled = False
    pending_enter = False
    pending_reason: Optional[str] = None
    signal_atr = 0.0
    entry_i = 0
    entry_px = stop = risk = peak = 0.0
    shares = 0
    target: Optional[float] = None
    trail = spec.target_r is None and spec.fixed_take_profit_pct is None
    n = len(close)
    cash = float(equity)

    def finish(index: int, price: float, reason: str) -> None:
        nonlocal in_pos, cooled, pending_reason, target, cash, shares
        fee_in = buy_fees(shares, entry_px, schedule)
        fee_out = sell_fees(shares, price, schedule)
        fees = fee_in + fee_out
        gross = (price - entry_px) * shares
        net = gross - fees
        trades.append(
            BacktestTrade(
                entry_day=str(dates[entry_i])[:10],
                exit_day=str(dates[index])[:10],
                shares=shares,
                entry_price=float(entry_px),
                exit_price=float(price),
                stop=float(stop),
                target=None if target is None else float(target),
                gross_pnl=float(gross),
                fees=float(fees),
                net_pnl=float(net),
                reason=reason,
            )
        )
        cash += price * shares - fee_out
        in_pos = False
        pending_reason = None
        target = None
        shares = 0
        cooled = reason == "stop" and state

    for index in range(n):
        if pending_enter and not in_pos:
            px = float(open_[index])
            stop_px, tgt, risk_px = _exit_levels(px, signal_atr, spec)
            qty = momentum.share_quantity(cash, px, stop_px)
            fee_in = buy_fees(qty, px, schedule) if qty > 0 else 0.0
            if (
                index >= start_index
                and qty > 0
                and risk_px > 0
                and stop_px > 0
                and stop_px < px
                and cash >= px * qty + fee_in
            ):
                in_pos = True
                entry_i = index
                entry_px = px
                risk = risk_px
                stop = stop_px
                peak = px
                target = tgt
                shares = qty
                cash -= px * qty + fee_in
            pending_enter = False

        if in_pos:
            bar_open = float(open_[index])
            bar_high = float(high[index])
            bar_low = float(low[index])
            if index == entry_i:
                if bar_low <= stop:
                    finish(index, stop, "stop")
                elif target is not None and bar_high >= target:
                    finish(index, target, "target")
            elif bar_open <= stop:
                finish(index, bar_open, "stop")
            elif target is not None and bar_open >= target:
                finish(index, bar_open, "target")
            elif bar_low <= stop:
                finish(index, stop, "stop")
            elif target is not None and bar_high >= target:
                finish(index, target, "target")
            elif pending_reason:
                finish(index, bar_open, pending_reason)

        if in_pos:
            peak = max(peak, float(close[index]))
            if trail and atr[index] > 0:
                if spec.fixed_stop_pct is not None:
                    trailed = round(peak * (1.0 - spec.fixed_stop_pct), 2)
                else:
                    trailed = round(peak - spec.atr_multiple * float(atr[index]), 2)
                if trailed > stop:
                    stop = trailed
            if state and known[index] and not signal[index]:
                pending_reason = "signal"
            elif spec.max_hold and (index - entry_i) >= spec.max_hold:
                pending_reason = "time"
        else:
            if cooled and state and known[index] and not signal[index]:
                cooled = False
            # Arm only when the next open (fill) can land in the window.
            can_buy = bool(
                known[index]
                and signal[index]
                and not cooled
                and atr[index] > 0
                and index + 1 < n
                and index + 1 >= start_index
            )
            pending_enter = can_buy
            if can_buy:
                signal_atr = float(atr[index])

    if in_pos:
        finish(n - 1, float(close[-1]), "mark")
    return trades


def run_spec_backtest(
    store,
    spec: StockSpec,
    *,
    sessions: int = BACKTEST_SESSIONS,
    equity: float = 100_000.0,
    schedule: IbkrProFixed = DEFAULT_IBKR_PRO_FIXED,
) -> BacktestResult:
    """Backtest one stock spec on the last ``sessions`` daily bars in the store."""
    if not spec.enabled:
        return _blank(spec, "spec disabled")

    book = contest.load_book(store, spec.ticker)
    if book is None:
        return _blank(spec, "no price_bars in local store")

    n = len(book["close"])
    if n < sessions + 2:
        return _blank(
            spec,
            f"need at least {sessions + 2} daily bars, have {n}",
        )

    start_index = n - sessions
    atr = contest._atr_array(book["high"], book["low"], book["close"])
    signal, known = contest._signal_arrays(
        book["high"], book["low"], book["close"], book["volume"], spec.entry
    )

    # Warm-up: entry rules that need history before the window.
    if not known[start_index:].any():
        return _blank(
            spec,
            f"signal '{spec.entry}' never becomes known inside the window "
            f"(need more history before the last {sessions} bars)",
        )

    trades = _simulate_fee_aware(
        book["open"],
        book["high"],
        book["low"],
        book["close"],
        atr,
        signal,
        known,
        book["dates"],
        spec,
        start_index=start_index,
        equity=equity,
        schedule=schedule,
    )
    gross = sum(t.gross_pnl for t in trades)
    fees = sum(t.fees for t in trades)
    return BacktestResult(
        ticker=spec.ticker,
        entry=spec.entry,
        atr_multiple=spec.atr_multiple,
        window_start=str(book["dates"][start_index])[:10],
        window_end=str(book["dates"][-1])[:10],
        bars_in_window=sessions,
        trades=tuple(trades),
        gross_pnl=float(gross),
        total_fees=float(fees),
        net_pnl=float(gross - fees),
    )


def run_folder_backtest(
    store,
    directory: Optional[str] = None,
    *,
    enabled_only: bool = True,
    sessions: int = BACKTEST_SESSIONS,
    equity: float = 100_000.0,
    schedule: IbkrProFixed = DEFAULT_IBKR_PRO_FIXED,
) -> List[BacktestResult]:
    """Run the six-month fee-aware backtest for every stock file in a folder."""
    specs = load_stock_specs(directory, enabled_only=enabled_only)
    return [
        run_spec_backtest(
            store, spec, sessions=sessions, equity=equity, schedule=schedule
        )
        for spec in specs
    ]
