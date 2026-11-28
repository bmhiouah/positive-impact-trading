"""A small contest that may replace the live rule.

The grid is deliberately short: six entries, four stop distances, a trailing
stop or a fixed target, and an optional time limit. Each candidate is traded
on daily bars using only information known at the time. The favorite is chosen
on an earlier block of history. It replaces the live rule only when a later
block, which the choice never saw, still made more money than the rule that
is already running.

Money is measured in R: the result of a trade divided by the distance from
the entry to the initial stop. One R is the amount the stop would lose.
Costs of five basis points per side are taken out before the score.

Nothing here talks to the broker. The runner reads ``live_rule`` and sends
whatever that says.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from marketmind import config
from marketmind import momentum

logger = logging.getLogger(__name__)

# Entries whose buy condition stays true for many days. The position is
# closed when the condition turns off. Breakout and dip are one-day events:
# the stop, the target or the time limit closes them.
STATE_ENTRIES = frozenset({"m12", "m6", "m3", "trend"})
ENTRIES = ("m12", "m6", "m3", "trend", "breakout", "dip")
ENTRY_NAMES = {
    "m12": "12-month momentum",
    "m6": "6-month momentum",
    "m3": "3-month momentum",
    "trend": "Golden cross",
    "breakout": "20-day breakout",
    "dip": "Dip in an uptrend",
}
MULTIPLES = (1.5, 2.0, 2.5, 3.0)
TARGETS = (None, 2.0, 3.0)
HOLDS = (None, 63)
MIN_TRADES = 5
MIN_BARS = 400
SELECTION_SHARE = 0.60
COST = 0.0005  # five basis points per side
_HISTORY_LIMIT = 1600


@dataclass(frozen=True)
class Rule:
    """One candidate the contest is allowed to send."""

    entry: str
    atr_multiple: float
    target_r: Optional[float] = None
    max_hold: Optional[int] = None

    def key(self) -> str:
        target = "trail" if self.target_r is None else f"t{self.target_r:g}"
        hold = "open" if not self.max_hold else f"h{self.max_hold}"
        return f"{self.entry}|{self.atr_multiple:g}|{target}|{hold}"


INCUMBENT = Rule("m12", config.ATR_MULTIPLE, None, None)


def describe(rule: Rule) -> str:
    """One line a person can read on the Strategies page."""
    name = ENTRY_NAMES.get(rule.entry, rule.entry)
    stop = f"{rule.atr_multiple:g}× the 20-day range"
    if rule.target_r is None:
        exit_ = "stop trails, no target"
    else:
        exit_ = f"target at {rule.target_r:g}× the risk, stop stays put"
    hold = (f", close after {rule.max_hold} sessions"
            if rule.max_hold else "")
    return f"{name} · {stop} · {exit_}{hold}"


def grid() -> List[Rule]:
    """Every combination the contest is allowed to try. The incumbent is in it."""
    rules = [
        Rule(entry, multiple, target, hold)
        for entry in ENTRIES
        for multiple in MULTIPLES
        for target in TARGETS
        for hold in HOLDS
    ]
    if INCUMBENT not in rules:
        rules.append(INCUMBENT)
    return rules


@dataclass(frozen=True)
class Trade:
    entry_index: int
    exit_index: int
    entry_day: str
    exit_day: str
    r_multiple: float
    reason: str


@dataclass(frozen=True)
class Score:
    rule: Rule
    selection_r: float
    selection_trades: int
    selection_dd: float
    confirmation_r: float
    confirmation_trades: int
    confirmation_dd: float

    def as_row(self) -> Dict[str, object]:
        return {
            "rule": rule_to_dict(self.rule),
            "label": describe(self.rule),
            "selection_r": round(self.selection_r, 3),
            "selection_trades": self.selection_trades,
            "selection_dd": round(self.selection_dd, 3),
            "confirmation_r": round(self.confirmation_r, 3),
            "confirmation_trades": self.confirmation_trades,
            "confirmation_dd": round(self.confirmation_dd, 3),
        }


@dataclass(frozen=True)
class Report:
    """What the Strategies page and the runner both read."""

    ready: bool
    promoted: bool
    holder: Rule
    reason: str
    fingerprint: str
    selection_end: str
    confirmation_start: str
    rows: Tuple[Score, ...]
    updated_at: str


def rule_to_dict(rule: Rule) -> Dict[str, object]:
    return asdict(rule)


def rule_from_dict(payload: Dict[str, object]) -> Rule:
    hold = payload.get("max_hold")
    target = payload.get("target_r")
    return Rule(
        entry=str(payload["entry"]),
        atr_multiple=float(payload["atr_multiple"]),
        target_r=None if target is None else float(target),
        max_hold=None if hold is None else int(hold),
    )


# ---------------------------------------------------------------------------
# Signals. None means "this bar does not have the history yet".
# ---------------------------------------------------------------------------

def _rsi(close: np.ndarray, index: int, period: int = 14) -> Optional[float]:
    if index < period:
        return None
    deltas = np.diff(close[index - period:index + 1])
    gains = np.clip(deltas, 0, None).mean()
    losses = np.clip(-deltas, 0, None).mean()
    if losses == 0:
        return 100.0
    return float(100.0 - 100.0 / (1.0 + gains / losses))


def signal_at(high, low, close, volume, index: int, entry: str) -> Optional[bool]:
    """Whether ``entry`` says buy at the close of ``index``."""
    if entry in {"m12", "m6", "m3"}:
        formation = {"m12": 252, "m6": 126, "m3": 63}[entry]
        reading = momentum.return_12_1(close, index, formation=formation, skip=21)
        if reading is None:
            return None
        return bool(reading > 0)
    if entry == "trend":
        if index < 199:
            return None
        window = close[:index + 1]
        sma50 = float(window[-50:].mean())
        sma200 = float(window[-200:].mean())
        return bool(sma50 > sma200 and close[index] > sma50)
    if entry == "breakout":
        if index < 21:
            return None
        prior_high = float(high[index - 20:index].max())
        avg_volume = float(volume[index - 20:index].mean())
        traded = float(volume[index])
        return bool(close[index] >= prior_high
                    and (avg_volume <= 0 or traded > 1.1 * avg_volume))
    if entry == "dip":
        if index < 50:
            return None
        rsi = _rsi(close, index)
        if rsi is None:
            return None
        sma50 = float(close[index - 49:index + 1].mean())
        return bool(rsi < 35 and close[index] > sma50)
    raise ValueError(f"unknown entry {entry!r}")


def _signal_arrays(high, low, close, volume, entry: str) -> Tuple[np.ndarray, np.ndarray]:
    n = len(close)
    signal = np.zeros(n, dtype=bool)
    known = np.zeros(n, dtype=bool)
    for index in range(n):
        verdict = signal_at(high, low, close, volume, index, entry)
        if verdict is None:
            continue
        known[index] = True
        signal[index] = bool(verdict)
    return signal, known


def _atr_array(high, low, close) -> np.ndarray:
    out = np.zeros(len(close))
    for index in range(len(close)):
        value = momentum.atr_at(high, low, close, index)
        if value is not None:
            out[index] = value
    return out


def _r(entry: float, exit_price: float, risk: float, cost: float) -> float:
    paid = entry * (1.0 + cost)
    received = exit_price * (1.0 - cost)
    return (received - paid) / risk


def simulate(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
             close: np.ndarray, atr: np.ndarray, signal: np.ndarray,
             known: np.ndarray, dates: Sequence[str], rule: Rule,
             cost: float = COST) -> List[Trade]:
    """Trade one name. A signal at the close is filled at the next open.

    When a bar touches the stop and the target, the stop is the fill. A gap
    through either level is filled at the open. An open trade is marked at
    the last close so a rule that never exits cannot hide its result.
    """
    trades: List[Trade] = []
    state = rule.entry in STATE_ENTRIES
    in_pos = False
    cooled = False
    pending_enter = False
    pending_reason: Optional[str] = None
    signal_atr = 0.0
    entry_i = 0
    entry_px = stop = risk = peak = 0.0
    target: Optional[float] = None
    n = len(close)

    def finish(index: int, price: float, reason: str) -> None:
        nonlocal in_pos, cooled, pending_reason, target
        trades.append(Trade(
            entry_index=entry_i, exit_index=index,
            entry_day=str(dates[entry_i])[:10],
            exit_day=str(dates[index])[:10],
            r_multiple=_r(entry_px, price, risk, cost),
            reason=reason,
        ))
        in_pos = False
        pending_reason = None
        target = None
        cooled = reason == "stop" and state

    for index in range(n):
        if pending_enter and not in_pos:
            px = float(open_[index])
            distance = rule.atr_multiple * signal_atr
            level = px - distance
            if px > 0 and distance > 0 and level > 0:
                in_pos = True
                entry_i = index
                entry_px = px
                risk = distance
                stop = level
                peak = px
                target = (px + rule.target_r * distance
                          if rule.target_r is not None else None)
            pending_enter = False

        if in_pos:
            bar_open = float(open_[index])
            bar_high = float(high[index])
            bar_low = float(low[index])
            # The open of the entry bar is the fill, so only the rest of that
            # session can hit the stop or the target. Later sessions can gap.
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
            if rule.target_r is None and atr[index] > 0:
                trailed = peak - rule.atr_multiple * float(atr[index])
                if trailed > stop:
                    stop = trailed
            if state and known[index] and not signal[index]:
                pending_reason = "signal"
            elif rule.max_hold and (index - entry_i) >= rule.max_hold:
                pending_reason = "time"
        else:
            if cooled and state and known[index] and not signal[index]:
                cooled = False
            can_buy = bool(known[index] and signal[index] and not cooled
                           and atr[index] > 0 and index + 1 < n)
            pending_enter = can_buy
            if can_buy:
                signal_atr = float(atr[index])

    if in_pos:
        finish(n - 1, float(close[-1]), "mark")
    return trades


def _drawdown(values: Sequence[float]) -> float:
    equity = peak = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _window_score(trades: Sequence[Trade], cutoff: str) -> Tuple[float, int, float, float, int, float]:
    selected = [trade for trade in trades if trade.entry_day < cutoff]
    confirmed = [trade for trade in trades if trade.entry_day >= cutoff]
    return (
        float(sum(trade.r_multiple for trade in selected)),
        len(selected),
        _drawdown([trade.r_multiple for trade in selected]),
        float(sum(trade.r_multiple for trade in confirmed)),
        len(confirmed),
        _drawdown([trade.r_multiple for trade in confirmed]),
    )


def choose(scores: Sequence[Score], incumbent: Rule = INCUMBENT) -> Tuple[Rule, bool, str]:
    """Pick on the selection window. Promote only if the later window agrees.

    The incumbent stays when the favorite lost money out of sample, or made
    less there than the incumbent did.
    """
    eligible = [row for row in scores if row.selection_trades >= MIN_TRADES]
    if not eligible:
        return incumbent, False, "Not enough trades in the selection window yet."
    favorite = max(eligible, key=lambda row: (row.selection_r, -row.selection_dd))
    current = next((row for row in scores if row.rule == incumbent), None)
    current_r = current.confirmation_r if current is not None else 0.0
    if favorite.rule == incumbent:
        return incumbent, False, "The selection window kept the rule already running."
    if favorite.confirmation_r > 0 and favorite.confirmation_r > current_r:
        return (favorite.rule, True,
                "A challenger made more money on the later window, so it is now the live rule.")
    return (incumbent, False,
            "The selection favorite did not beat the live rule on the later window, so the live rule stays.")


def _cutoff(dates: Sequence[str]) -> str:
    usable = [str(day)[:10] for day in dates]
    start = min(config.MOMENTUM_FORMATION, len(usable) - 2)
    span = max(1, len(usable) - start)
    index = min(len(usable) - 1, start + int(SELECTION_SHARE * span))
    return usable[index]


def _prepare(high, low, close, volume) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    return {entry: _signal_arrays(high, low, close, volume, entry) for entry in ENTRIES}


def score_books(books: Dict[str, dict], rules: Optional[Sequence[Rule]] = None,
                ) -> Tuple[str, List[Score]]:
    """Score every rule on the supplied books. ``books`` values hold OHLCV."""
    prepared = {}
    calendar: List[str] = []
    for symbol, book in books.items():
        high, low, close = book["high"], book["low"], book["close"]
        volume = book["volume"]
        prepared[symbol] = {
            "open": book["open"], "high": high, "low": low, "close": close,
            "atr": _atr_array(high, low, close),
            "dates": book["dates"],
            "signals": _prepare(high, low, close, volume),
        }
        if len(book["dates"]) > len(calendar):
            calendar = list(book["dates"])
    if not calendar:
        return "", []
    cutoff = _cutoff(calendar)
    scores: List[Score] = []
    for rule in rules or grid():
        trades: List[Trade] = []
        for book in prepared.values():
            signal, known = book["signals"][rule.entry]
            trades.extend(simulate(
                book["open"], book["high"], book["low"], book["close"],
                book["atr"], signal, known, book["dates"], rule,
            ))
        (sel_r, sel_n, sel_dd, con_r, con_n, con_dd) = _window_score(trades, cutoff)
        scores.append(Score(
            rule=rule, selection_r=sel_r, selection_trades=sel_n,
            selection_dd=sel_dd, confirmation_r=con_r,
            confirmation_trades=con_n, confirmation_dd=con_dd,
        ))
    return cutoff, scores


# ---------------------------------------------------------------------------
# Storage and the report the cockpit shows
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_table(store) -> None:
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS rule_contest (
               id            INTEGER PRIMARY KEY CHECK (id = 1),
               fingerprint   TEXT NOT NULL,
               updated_at    TEXT NOT NULL,
               ready         INTEGER NOT NULL,
               promoted      INTEGER NOT NULL,
               holder_json   TEXT NOT NULL,
               report_json   TEXT NOT NULL
           )"""
    )
    store.conn.commit()


def fingerprint(store, symbols: Sequence[str]) -> str:
    """Changes when the stored daily bars change, so the contest is not rerun."""
    row = store.conn.execute(
        """SELECT COUNT(*), COALESCE(MAX(timestamp), '')
           FROM price_bars
           WHERE bar_size = ? AND symbol IN (%s)""" % ",".join("?" * len(symbols)),
        (config.HISTORY_BAR_SIZE, *symbols),
    ).fetchone()
    if row is None:
        return "0:"
    return f"{row[0]}:{row[1]}"


def load_book(store, symbol: str) -> Optional[dict]:
    """Daily bars, oldest first, including the open and the volume."""
    rows = store.conn.execute(
        """SELECT timestamp, open, high, low, close, volume
           FROM price_bars
           WHERE symbol = ? AND bar_size = ?
           ORDER BY timestamp DESC LIMIT ?""",
        (symbol, config.HISTORY_BAR_SIZE, _HISTORY_LIMIT),
    ).fetchall()
    if len(rows) < 2:
        return None
    rows.reverse()
    return {
        "open": np.array([float(row[1]) for row in rows]),
        "high": np.array([float(row[2]) for row in rows]),
        "low": np.array([float(row[3]) for row in rows]),
        "close": np.array([float(row[4]) for row in rows]),
        "volume": np.array([float(row[5] or 0) for row in rows]),
        "dates": [str(row[0])[:10] for row in rows],
    }


def _blank(reason: str, fingerprint_: str) -> Report:
    return Report(
        ready=False, promoted=False, holder=INCUMBENT, reason=reason,
        fingerprint=fingerprint_, selection_end="", confirmation_start="",
        rows=tuple(), updated_at=_utcnow(),
    )


def run(store, symbols: Sequence[str]) -> Report:
    """Score the grid and decide whether the live rule changes."""
    mark = fingerprint(store, symbols) if symbols else "0:"
    books = {}
    for symbol in symbols:
        book = load_book(store, symbol)
        if book is not None and len(book["close"]) >= MIN_BARS:
            books[symbol] = book
    if not books:
        return _blank(
            "The contest needs about two years of daily bars on at least one name. "
            "Sync from IBKR and it will run on the next pass.",
            mark,
        )
    cutoff, scores = score_books(books)
    holder, promoted, reason = choose(scores)
    ranked = tuple(sorted(scores, key=lambda row: row.selection_r, reverse=True)[:8])
    return Report(
        ready=True, promoted=promoted, holder=holder, reason=reason,
        fingerprint=mark, selection_end=cutoff, confirmation_start=cutoff,
        rows=ranked, updated_at=_utcnow(),
    )


def save(store, report: Report) -> None:
    _ensure_table(store)
    payload = {
        "reason": report.reason,
        "selection_end": report.selection_end,
        "confirmation_start": report.confirmation_start,
        "rows": [row.as_row() for row in report.rows],
    }
    store.conn.execute(
        """INSERT INTO rule_contest
              (id, fingerprint, updated_at, ready, promoted, holder_json, report_json)
           VALUES (1, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
              fingerprint = excluded.fingerprint,
              updated_at = excluded.updated_at,
              ready = excluded.ready,
              promoted = excluded.promoted,
              holder_json = excluded.holder_json,
              report_json = excluded.report_json""",
        (report.fingerprint, report.updated_at, int(report.ready),
         int(report.promoted), json.dumps(rule_to_dict(report.holder)),
         json.dumps(payload)),
    )
    store.conn.commit()


def load(store) -> Optional[Report]:
    _ensure_table(store)
    row = store.conn.execute(
        """SELECT fingerprint, updated_at, ready, promoted, holder_json, report_json
           FROM rule_contest WHERE id = 1"""
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[5])
    scores = []
    for item in payload.get("rows", []):
        scores.append(Score(
            rule=rule_from_dict(item["rule"]),
            selection_r=float(item["selection_r"]),
            selection_trades=int(item["selection_trades"]),
            selection_dd=float(item["selection_dd"]),
            confirmation_r=float(item["confirmation_r"]),
            confirmation_trades=int(item["confirmation_trades"]),
            confirmation_dd=float(item["confirmation_dd"]),
        ))
    return Report(
        ready=bool(row[2]), promoted=bool(row[3]),
        holder=rule_from_dict(json.loads(row[4])),
        reason=str(payload.get("reason") or ""),
        fingerprint=str(row[0]), updated_at=str(row[1]),
        selection_end=str(payload.get("selection_end") or ""),
        confirmation_start=str(payload.get("confirmation_start") or ""),
        rows=tuple(scores),
    )


def ensure(store, symbols: Sequence[str], force: bool = False) -> Report:
    """Return the saved contest, recomputing when the bars have changed."""
    current = fingerprint(store, symbols) if symbols else "0:"
    saved = load(store)
    if saved is not None and not force and saved.fingerprint == current:
        return saved
    report = run(store, symbols)
    save(store, report)
    logger.info("Contest %s — %s", "promoted" if report.promoted else "kept",
                describe(report.holder))
    return report


def live_rule(store) -> Rule:
    """The rule the runner sends. The incumbent until a contest promotes one."""
    try:
        saved = load(store)
    except Exception:  # noqa: BLE001 - a missing table must not stop trading
        return INCUMBENT
    if saved is None:
        return INCUMBENT
    return saved.holder


# ---------------------------------------------------------------------------
# What the runner asks, once per session, for the rule that won
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveRead:
    symbol: str
    buy: bool
    price: float
    stop: float
    target: Optional[float]
    day: date
    index: int
    rationale: str


def _book_arrays(book: dict):
    return (book["high"], book["low"], book["close"], book["volume"])


def rule_from_spec(spec) -> Rule:
    """Build a contest ``Rule`` from a file-driven ``StockSpec``."""
    return Rule(
        entry=str(spec.entry),
        atr_multiple=float(spec.atr_multiple),
        target_r=None if spec.target_r is None else float(spec.target_r),
        max_hold=None if spec.max_hold is None else int(spec.max_hold),
    )


def read_symbol(
    book: dict,
    rule: Rule,
    session: date,
    *,
    fixed_stop_pct: Optional[float] = None,
    fixed_take_profit_pct: Optional[float] = None,
) -> Optional[LiveRead]:
    """The last completed session, read with ``rule``.

    Optional fixed percentage exits (from a stock file) override the ATR stop
    and/or R-multiple target when set.
    """
    close = book["close"]
    index = momentum.completed_index(book["dates"], session)
    if index < 0:
        return None
    verdict = signal_at(*_book_arrays(book), index, rule.entry)
    atr_value = momentum.atr_at(book["high"], book["low"], close, index)
    if verdict is None or atr_value is None or atr_value <= 0:
        return None
    price = float(close[index])
    distance = rule.atr_multiple * atr_value
    if fixed_stop_pct is not None and fixed_stop_pct > 0:
        stop = round(price * (1.0 - fixed_stop_pct), 2)
    else:
        stop = round(price - distance, 2)
    if stop <= 0 or stop >= price:
        return None
    if fixed_take_profit_pct is not None and fixed_take_profit_pct > 0:
        target = round(price * (1.0 + fixed_take_profit_pct), 2)
    elif rule.target_r is not None:
        risk = price - stop
        target = round(price + rule.target_r * risk, 2) if risk > 0 else None
    else:
        target = None
    day = momentum.as_date(book["dates"][index])
    if day is None:
        return None
    return LiveRead(
        symbol="", buy=bool(verdict), price=price, stop=stop, target=target,
        day=day, index=index,
        rationale=f"{describe(rule)} · close {price:.2f} on {day.isoformat()}",
    )


def allow_entry(book: dict, rule: Rule, now_index: int,
                last_outcome: Optional[str], exit_index: Optional[int],
                *, cold_start: bool = False) -> bool:
    """Whether a live BUY may fire on this completed bar.

    Sticky state entries (``m12`` / ``m6`` / ``m3`` / ``trend``) stay true for
    many sessions. Without a rising-edge check the live runner would re-count
    a BUY every day while flat. Require yesterday off (or unknown) and today
    on. After a stop-loss on those entries, also wait until the condition has
    turned off at least once since the exit. Breakout and dip stay
    level-triggered — they are short-lived events.

    Cold start: when the live book has never successfully sent an entry for
    this symbol, allow a level-true state signal even if the regime was already
    on yesterday. Otherwise a name that was already in an ``m12`` uptrend when
    paper trading began would wait months for an off→on flip and never trade.
    """
    verdict = signal_at(*_book_arrays(book), now_index, rule.entry)
    if not verdict:
        return False
    if rule.entry in STATE_ENTRIES and now_index > 0:
        prior = signal_at(*_book_arrays(book), now_index - 1, rule.entry)
        if prior is True and not cold_start:
            return False
    if last_outcome != "stop_loss" or rule.entry not in STATE_ENTRIES:
        return True
    if exit_index is None:
        return False
    for index in range(exit_index, now_index + 1):
        later = signal_at(*_book_arrays(book), index, rule.entry)
        if later is False:
            return True
    return False


def manage(
    book: dict,
    rule: Rule,
    entry_day: date,
    previous_stop: float,
    session: date,
    *,
    fixed_stop_pct: Optional[float] = None,
    fixed_take_profit_pct: Optional[float] = None,
) -> momentum.HoldAction:
    """Trail, or leave, using the live rule and the last completed session."""
    floor = float(previous_stop) if previous_stop and previous_stop > 0 else 0.0
    parked = momentum.HoldAction(kind="hold", reason="", stop=round(floor, 2))
    close = book["close"]
    index = momentum.completed_index(book["dates"], session)
    if index < 0:
        return parked
    start = None
    for cursor, raw in enumerate(book["dates"]):
        day = momentum.as_date(raw)
        if day is not None and day >= entry_day:
            start = cursor
            break
    if start is None or start > index:
        return parked
    verdict = signal_at(*_book_arrays(book), index, rule.entry)
    if rule.entry in STATE_ENTRIES and verdict is False:
        return momentum.HoldAction(kind="exit", reason="signal", stop=round(floor, 2))
    if rule.max_hold and (index - start) >= rule.max_hold:
        return momentum.HoldAction(kind="exit", reason="time", stop=round(floor, 2))
    atr_value = momentum.atr_at(book["high"], book["low"], close, index)
    if atr_value is None or atr_value <= 0:
        return parked
    price = float(close[index])
    # Fixed take-profit rules (and R-targets) keep the stop put; only trail when
    # neither a fixed TP pct nor a target_r is set.
    trail = rule.target_r is None and not (
        fixed_take_profit_pct is not None and fixed_take_profit_pct > 0
    )
    if trail:
        peak = float(np.max(close[start:index + 1]))
        if fixed_stop_pct is not None and fixed_stop_pct > 0:
            trailed = round(peak * (1.0 - fixed_stop_pct), 2)
        else:
            trailed = round(peak - rule.atr_multiple * atr_value, 2)
        new_stop = round(max(floor, trailed), 2) if trailed > 0 else round(floor, 2)
        if price <= new_stop:
            return momentum.HoldAction(kind="exit", reason="stop", stop=new_stop)
        if new_stop > floor + 0.009 and new_stop < price:
            return momentum.HoldAction(kind="raise", reason="trail", stop=new_stop)
        return momentum.HoldAction(kind="hold", reason="", stop=new_stop if new_stop else floor)
    if floor > 0 and price <= floor:
        return momentum.HoldAction(kind="exit", reason="stop", stop=round(floor, 2))
    return parked
