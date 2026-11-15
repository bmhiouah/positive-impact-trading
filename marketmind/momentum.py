"""The live book: 12-month momentum, a trailing volatility stop, sized by risk.

Nothing in this module talks to the broker. The catalogue, the runner and the
tests all call the same functions, so the number that decides a buy is the
number that can be checked on past bars.

The signal is the return from ``MOMENTUM_FORMATION`` sessions ago to
``MOMENTUM_SKIP`` sessions ago. The last month is left out because prices
over a few weeks more often snap back than continue. A positive reading is a
buy. The position stays on until that reading turns flat or the close falls
through the stop.

The stop starts two average daily ranges under the entry and then follows the
highest close since the entry, minus the same two ranges. It never moves down.
A stop-out does not buy the name again while that same positive reading is
still on. The signal has to turn off and come back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from marketmind import config

_NY = ZoneInfo("America/New_York")
_HISTORY_LIMIT = 1600  # five years of sessions, with room to spare


@dataclass(frozen=True)
class Snapshot:
    """One name, read on its last completed session."""

    index: int
    day: date
    close: float
    momentum: float
    atr: float
    stop: float

    @property
    def buy(self) -> bool:
        return self.momentum > 0


@dataclass(frozen=True)
class HoldAction:
    """What to do with a position we already have, on the completed session."""

    kind: str          # "hold", "raise", "exit"
    reason: str        # "", "trail", "stop", "momentum"
    stop: float


def session_day(now: Optional[datetime] = None) -> date:
    """The New York calendar day. A bar dated today is not finished yet."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_NY).date()


def as_date(value) -> Optional[date]:
    """First ten characters of a bar timestamp, or None when it is not a day."""
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def return_12_1(close: np.ndarray, index: int,
                formation: int = config.MOMENTUM_FORMATION,
                skip: int = config.MOMENTUM_SKIP) -> Optional[float]:
    """Return from ``formation`` sessions ago to ``skip`` sessions ago.

    ``close[index]`` itself is not in the ratio. A crash inside the skipped
    month does not flip the signal. The stop is what reacts to that crash.
    """
    if index < formation or index - skip < 0:
        return None
    start = float(close[index - formation])
    end = float(close[index - skip])
    if not math.isfinite(start) or not math.isfinite(end) or start <= 0:
        return None
    return end / start - 1.0


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               index: int) -> Optional[float]:
    """Wilder's true range at ``index``: the bar's range, extended by gaps."""
    if index <= 0 or index >= len(close):
        return None
    previous = float(close[index - 1])
    bar_high = float(high[index])
    bar_low = float(low[index])
    if not all(math.isfinite(v) for v in (previous, bar_high, bar_low)):
        return None
    return max(bar_high - bar_low, abs(bar_high - previous), abs(bar_low - previous))


def atr_at(high: np.ndarray, low: np.ndarray, close: np.ndarray, index: int,
           period: int = config.ATR_PERIOD) -> Optional[float]:
    """Average true range of the last ``period`` sessions, ending at ``index``."""
    if index < period:
        return None
    ranges = []
    for cursor in range(index - period + 1, index + 1):
        value = true_range(high, low, close, cursor)
        if value is None or value < 0:
            return None
        ranges.append(value)
    return float(np.mean(ranges))


def completed_index(dates: Sequence, session: date) -> int:
    """Last bar whose date is strictly before ``session``. -1 when there is none."""
    found = -1
    for index, raw in enumerate(dates):
        day = as_date(raw)
        if day is not None and day < session:
            found = index
    return found


def index_of_day(dates: Sequence, day: date) -> Optional[int]:
    """Last bar on or before ``day``. None when every bar is later."""
    found = None
    for index, raw in enumerate(dates):
        current = as_date(raw)
        if current is None:
            continue
        if current <= day:
            found = index
        elif found is not None:
            break
    return found


def _stop_from(price: float, atr_value: float,
               multiple: float = config.ATR_MULTIPLE) -> Optional[float]:
    if not math.isfinite(price) or not math.isfinite(atr_value):
        return None
    if price <= 0 or atr_value <= 0:
        return None
    stop = round(price - multiple * atr_value, 2)
    if stop <= 0 or stop >= price:
        return None
    return stop


def assess(high: np.ndarray, low: np.ndarray, close: np.ndarray,
           dates: Sequence, session: Optional[date] = None) -> Optional[Snapshot]:
    """Read the last completed session. None when the history is too short."""
    session = session or session_day()
    index = completed_index(dates, session)
    if index < 0:
        return None
    momentum = return_12_1(close, index)
    atr_value = atr_at(high, low, close, index)
    if momentum is None or atr_value is None:
        return None
    price = float(close[index])
    stop = _stop_from(price, atr_value)
    if stop is None:
        return None
    day = as_date(dates[index])
    if day is None:
        return None
    return Snapshot(index=index, day=day, close=price, momentum=momentum,
                    atr=atr_value, stop=stop)


def hold_action(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                dates: Sequence, entry_day: date, previous_stop: float,
                session: Optional[date] = None) -> HoldAction:
    """Trail the stop, or leave, using only the last completed session.

    Missing history holds the position. A name is never sold because a bar
    failed to load.
    """
    session = session or session_day()
    floor = float(previous_stop) if previous_stop and previous_stop > 0 else 0.0
    parked = HoldAction(kind="hold", reason="", stop=round(floor, 2))
    index = completed_index(dates, session)
    if index < 0:
        return parked
    # A finished trend is an exit on its own. A wild range must not keep the
    # position open just because a new stop cannot be priced.
    reading = return_12_1(close, index)
    if reading is not None and reading <= 0:
        return HoldAction(kind="exit", reason="momentum", stop=round(floor, 2))
    atr_value = atr_at(high, low, close, index)
    if atr_value is None:
        return parked
    start = 0
    for cursor, raw in enumerate(dates):
        day = as_date(raw)
        if day is not None and day >= entry_day:
            start = cursor
            break
    else:
        return parked
    if start > index:
        return parked
    peak = float(np.max(close[start:index + 1]))
    candidate = _stop_from(peak, atr_value)
    if candidate is None:
        return parked
    new_stop = round(max(floor, candidate), 2)
    price = float(close[index])
    if price <= new_stop:
        return HoldAction(kind="exit", reason="stop", stop=new_stop)
    if new_stop > floor + 0.009 and new_stop < price:
        return HoldAction(kind="raise", reason="trail", stop=new_stop)
    return HoldAction(kind="hold", reason="", stop=new_stop if new_stop > 0 else floor)


def went_non_positive(close: np.ndarray, exit_index: int, now_index: int) -> bool:
    """True when 12-1 was flat or negative on a session after the exit."""
    if now_index <= exit_index:
        return False
    for index in range(exit_index + 1, now_index + 1):
        reading = return_12_1(close, index)
        if reading is not None and reading <= 0:
            return True
    return False


def should_enter(close: np.ndarray, index: int, momentum: float,
                 last_outcome: Optional[str],
                 exit_index: Optional[int]) -> bool:
    """A new buy. A stop-out waits until the positive reading has turned off."""
    if momentum <= 0:
        return False
    if not last_outcome or last_outcome == "open":
        return True
    if last_outcome == "stop_loss":
        if exit_index is None:
            return False
        return went_non_positive(close, exit_index, index)
    return True


def share_quantity(equity: float, entry: float, stop: float, *,
                   cash: Optional[float] = None,
                   risk_fraction: float = config.RISK_FRACTION,
                   max_fraction: float = config.MAX_POSITION_FRACTION) -> int:
    """Shares so the stop risks ``risk_fraction`` of equity, within two caps.

    The notional cap stops a very tight stop from buying the whole account.
    Cash, when it is known and positive, stops an order the account cannot
    pay. Equity and the stop distance are treated as the same unit. On a euro
    account buying dollar stocks that is slightly conservative.
    """
    try:
        equity_f = float(equity)
        entry_f = float(entry)
        stop_f = float(stop)
    except (TypeError, ValueError):
        return 0
    distance = entry_f - stop_f
    if (not math.isfinite(equity_f) or not math.isfinite(entry_f)
            or not math.isfinite(distance)):
        return 0
    if equity_f <= 0 or entry_f <= 0 or distance <= 0:
        return 0
    by_risk = math.floor(equity_f * risk_fraction / distance)
    by_book = math.floor(equity_f * max_fraction / entry_f)
    limits = [by_risk, by_book]
    if cash is not None:
        try:
            cash_f = float(cash)
        except (TypeError, ValueError):
            cash_f = 0.0
        if math.isfinite(cash_f) and cash_f > 0:
            limits.append(math.floor(cash_f * 0.98 / entry_f))
    return max(0, min(limits))


def load_bars(store, symbol: str) -> Optional[dict]:
    """Daily bars for one symbol, oldest first. None when there are not two."""
    rows = store.conn.execute(
        """SELECT timestamp, high, low, close
           FROM price_bars
           WHERE symbol = ? AND bar_size = ?
           ORDER BY timestamp DESC LIMIT ?""",
        (symbol, config.HISTORY_BAR_SIZE, _HISTORY_LIMIT),
    ).fetchall()
    if len(rows) < 2:
        return None
    rows.reverse()
    return {
        "high": np.array([float(row[1]) for row in rows]),
        "low": np.array([float(row[2]) for row in rows]),
        "close": np.array([float(row[3]) for row in rows]),
        "dates": [str(row[0]) for row in rows],
    }
