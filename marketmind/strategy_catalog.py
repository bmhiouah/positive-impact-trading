"""Data-driven catalogue of LONG-ONLY strategies.

Each strategy declares the data it needs. The cockpit shows which ones are
available *given the data actually present*, and the runner only executes the
ready ones. Adding more symbols to the universe or more history automatically
unlocks more strategies — no wiring change required.

No strategy ever emits a SELL. Exits are handled by the TP/SL brackets that
accompany every entry, and shorting is not part of this lab.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from marketmind import config
from marketmind.compliance.rules import is_debt_ratio_compliant, is_excluded_sector
from marketmind.data.storage import DataStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategySpec:
    key: str
    name: str
    description: str
    min_bars: int = 0
    needs_fundamentals: bool = False
    family: str = "Price"


SPECS: tuple[StrategySpec, ...] = (
    StrategySpec(
        key="trend", name="Golden cross (50/200)",
        description="Buy an established uptrend: SMA50 above SMA200 and price above SMA50.",
        min_bars=200, family="Trend",
    ),
    StrategySpec(
        key="breakout", name="20-day breakout",
        description="Buy a close through the prior 20-day high on above-average volume.",
        min_bars=21, family="Momentum",
    ),
    StrategySpec(
        key="dip", name="Dip buy in uptrend",
        description="Buy a short-term RSI washout while price stays above its 50-day average.",
        min_bars=50, family="Mean reversion",
    ),
    StrategySpec(
        key="momentum", name="12-week momentum",
        description="Buy sustained 63-session strength that is still rising over the last week.",
        min_bars=64, family="Momentum",
    ),
    StrategySpec(
        key="impact_debt", name="Impact quality screen",
        description="Buy only companies with a compliant sector and debt/market-cap below 33%.",
        min_bars=0, needs_fundamentals=True, family="Fundamentals",
    ),
)

_BY_KEY = {spec.key: spec for spec in SPECS}


def _closes(store: DataStore, symbol: str, limit: int = 500) -> Optional[Dict[str, np.ndarray]]:
    rows = store.conn.execute(
        """SELECT timestamp, open, high, low, close, volume
           FROM price_bars
           WHERE symbol = ? AND bar_size = ?
           ORDER BY timestamp DESC LIMIT ?""",
        (symbol, config.HISTORY_BAR_SIZE, limit),
    ).fetchall()
    if len(rows) < 2:
        return None
    rows.reverse()
    return {
        "high": np.array([float(r[2]) for r in rows]),
        "low": np.array([float(r[3]) for r in rows]),
        "close": np.array([float(r[4]) for r in rows]),
        "volume": np.array([float(r[5] or 0) for r in rows]),
        "dates": [str(r[0])[:10] for r in rows],
    }


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    """Simple RSI over the last ``period`` deltas (0-100)."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)[-period:]
    gains = np.clip(deltas, 0, None).mean()
    losses = np.clip(-deltas, 0, None).mean()
    if losses == 0:
        return 100.0
    return float(100.0 - 100.0 / (1.0 + gains / losses))


def _signal(symbol: str, spec: StrategySpec, action: str,
            strength: float, rationale: str, price: float,
            **indicators: Any) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "strategy": spec.key,
        "strategy_name": spec.name,
        "action": action,
        "strength": round(max(0.0, min(1.0, float(strength))), 3),
        "rationale": rationale,
        "price": price,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **indicators,
    }


# ---------------------------------------------------------------------------
# Individual strategies
# ---------------------------------------------------------------------------

def _trend(store: DataStore, symbol: str, spec: StrategySpec
           ) -> Optional[Dict[str, Any]]:
    data = _closes(store, symbol)
    if data is None or len(data["close"]) < spec.min_bars:
        return None
    close = data["close"]
    sma50 = float(close[-50:].mean())
    sma200 = float(close[-200:].mean())
    price = float(close[-1])
    buy = sma50 > sma200 and price > sma50
    strength = min(1.0, 0.5 * (sma50 / sma200 - 1) / 0.10
                   + 0.5 * (price / sma50 - 1) / 0.05)
    return _signal(
        symbol, spec, "BUY" if buy else "HOLD", strength if buy else 0.0,
        f"SMA50 {sma50:.2f} vs SMA200 {sma200:.2f}, price {price:.2f}",
        price, sma50=round(sma50, 2), sma200=round(sma200, 2),
    )


def _breakout(store: DataStore, symbol: str, spec: StrategySpec
              ) -> Optional[Dict[str, Any]]:
    data = _closes(store, symbol)
    if data is None or len(data["close"]) < spec.min_bars:
        return None
    prior_high = float(data["high"][-21:-1].max())
    avg_volume = float(data["volume"][-21:-1].mean())
    price = float(data["close"][-1])
    volume = float(data["volume"][-1])
    buy = price >= prior_high and (avg_volume <= 0 or volume > 1.1 * avg_volume)
    strength = min(1.0, (price / prior_high - 1) / 0.03 + 0.3) if buy else 0.0
    return _signal(
        symbol, spec, "BUY" if buy else "HOLD", strength,
        f"Close {price:.2f} vs 20d high {prior_high:.2f}, volume ratio "
        f"{volume / avg_volume if avg_volume else 0:.2f}x",
        price, prior_high=round(prior_high, 2),
    )


def _dip(store: DataStore, symbol: str, spec: StrategySpec
         ) -> Optional[Dict[str, Any]]:
    data = _closes(store, symbol)
    if data is None or len(data["close"]) < spec.min_bars:
        return None
    close = data["close"]
    rsi = _rsi(close)
    sma50 = float(close[-50:].mean())
    price = float(close[-1])
    buy = rsi < 35 and price > sma50
    strength = min(1.0, (35 - rsi) / 35 + 0.2) if buy else 0.0
    return _signal(
        symbol, spec, "BUY" if buy else "HOLD", strength,
        f"RSI14 {rsi:.1f}, price {price:.2f} vs SMA50 {sma50:.2f}",
        price, rsi=round(rsi, 1), sma50=round(sma50, 2),
    )


def _momentum(store: DataStore, symbol: str, spec: StrategySpec
              ) -> Optional[Dict[str, Any]]:
    data = _closes(store, symbol)
    if data is None or len(data["close"]) < spec.min_bars:
        return None
    close = data["close"]
    price = float(close[-1])
    r63 = price / float(close[-64]) - 1
    r5 = price / float(close[-6]) - 1
    buy = r63 > 0.05 and r5 > 0
    strength = min(1.0, r63 / 0.25) if buy else 0.0
    return _signal(
        symbol, spec, "BUY" if buy else "HOLD", strength,
        f"63-session return {r63:+.1%}, last 5 sessions {r5:+.1%}",
        price, return_63=round(r63, 4), return_5=round(r5, 4),
    )


def _impact_debt(store: DataStore, symbol: str, spec: StrategySpec
                 ) -> Optional[Dict[str, Any]]:
    fund = store.get_fundamental_data(symbol)
    if fund is None or fund.debt_to_market_cap is None:
        return None
    ratio = float(fund.debt_to_market_cap)
    compliant = is_debt_ratio_compliant(ratio) and not is_excluded_sector(fund.sector)
    price_row = store.conn.execute(
        "SELECT close FROM price_bars WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    price = float(price_row[0]) if price_row else 0.0
    return _signal(
        symbol, spec, "BUY" if compliant else "HOLD",
        1.0 - ratio / 0.33 if compliant else 0.0,
        f"Sector {fund.sector or 'unknown'}, debt/MCap {ratio:.1%}",
        price, debt_ratio=round(ratio, 4),
    )


_RUNNERS = {
    "trend": _trend, "breakout": _breakout, "dip": _dip,
    "momentum": _momentum, "impact_debt": _impact_debt,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def availability(coverage: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Which strategies the CURRENT data can support, and why not the rest."""
    result = []
    for spec in SPECS:
        if spec.needs_fundamentals:
            eligible = sum(1 for row in coverage if row.get("fundamentals"))
            total = len(coverage)
            reason = (f"{eligible}/{total} symbols have debt-ratio data"
                      if eligible else
                      "Needs debt/market-cap data — IBKR fundamentals are not "
                      "available on this paper account (error 10358)")
        else:
            eligible = sum(
                1 for row in coverage
                if int(row.get("bars", 0)) >= spec.min_bars
            )
            reason = (f"{eligible} symbols have ≥ {spec.min_bars} daily bars"
                      if eligible else
                      f"Needs ≥ {spec.min_bars} daily bars per symbol — "
                      "run a data sync first")
        result.append({
            "key": spec.key, "name": spec.name,
            "description": spec.description, "family": spec.family,
            "min_bars": spec.min_bars,
            "needs_fundamentals": spec.needs_fundamentals,
            "eligible": eligible, "ready": eligible > 0, "reason": reason,
        })
    return result


def run(key: str, symbols: List[str], store: DataStore) -> List[Dict[str, Any]]:
    """Run one strategy across the universe. Never raises on a single symbol."""
    spec = _BY_KEY.get(key)
    runner = _RUNNERS.get(key)
    if spec is None or runner is None:
        raise ValueError(f"Unknown strategy {key!r}")
    signals: List[Dict[str, Any]] = []
    for symbol in symbols:
        try:
            result = runner(store, symbol, spec)
            if result is not None:
                signals.append(result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Strategy %s failed for %s: %s", key, symbol, exc)
    return signals


def run_all_ready(symbols: List[str], store: DataStore,
                  coverage: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every ready strategy, deduplicated to the strongest BUY per symbol."""
    strongest: Dict[str, Dict[str, Any]] = {}
    for item in availability(coverage):
        if not item["ready"]:
            continue
        for signal in run(item["key"], symbols, store):
            if signal["action"] != "BUY":
                continue
            current = strongest.get(signal["symbol"])
            if current is None or signal["strength"] > current["strength"]:
                strongest[signal["symbol"]] = signal
    return sorted(strongest.values(), key=lambda s: s["strength"], reverse=True)


# ---------------------------------------------------------------------------
# Historical replay — "what would this rule have said?"
# ---------------------------------------------------------------------------

def _trend_at(data: Dict[str, np.ndarray], i: int) -> Optional[bool]:
    if i < 199:
        return None
    close = data["close"]
    sma50 = close[i - 49:i + 1].mean()
    sma200 = close[i - 199:i + 1].mean()
    return bool(sma50 > sma200 and close[i] > sma50)


def _breakout_at(data: Dict[str, np.ndarray], i: int) -> Optional[bool]:
    if i < 21:
        return None
    prior_high = data["high"][i - 20:i].max()
    avg_volume = data["volume"][i - 20:i].mean()
    return bool(data["close"][i] >= prior_high
                and (avg_volume <= 0 or data["volume"][i] > 1.1 * avg_volume))


def _dip_at(data: Dict[str, np.ndarray], i: int) -> Optional[bool]:
    if i < 50:
        return None
    close = data["close"]
    rsi = _rsi(close[:i + 1])
    sma50 = close[i - 49:i + 1].mean()
    return bool(rsi < 35 and close[i] > sma50)


def _momentum_at(data: Dict[str, np.ndarray], i: int) -> Optional[bool]:
    if i < 64:
        return None
    close = data["close"]
    return bool(close[i] / close[i - 63] - 1 > 0.05
                and close[i] / close[i - 5] - 1 > 0)


_RULES_AT = {
    "trend": _trend_at, "breakout": _breakout_at,
    "dip": _dip_at, "momentum": _momentum_at,
}


def replayable_keys() -> List[str]:
    """Strategies whose rules can be replayed on price history."""
    return [spec.key for spec in SPECS if spec.key in _RULES_AT]


def spec_name(key: str) -> str:
    spec = _BY_KEY.get(key)
    return spec.name if spec else key


def replay(key: str, symbol: str, store: DataStore,
           lookback: int = 120, horizon: int = 5) -> Dict[str, Any]:
    """Walk a rule over past bars using ONLY data available on each date.

    Returns the BUY dates and the forward ``horizon``-session returns, which
    is enough to compare long-only ideas without pretending to be a full
    execution backtest (no commissions, slippage, position sizing or TP/SL).
    """
    rule = _RULES_AT.get(key)
    if rule is None:
        return {"supported": False, "reason":
                "Fundamental screens are point-in-time snapshots and cannot "
                "be replayed on price history."}
    data = _closes(store, symbol, limit=600)
    if data is None:
        return {"supported": False, "reason": "No price history for this symbol."}
    close = data["close"]
    n = len(close)
    start = max(0, n - min(int(lookback), n))
    signals: List[Dict[str, Any]] = []
    for i in range(start, n):
        verdict = rule(data, i)
        if verdict is None or not verdict:
            continue
        forward = (float(close[i + horizon] / close[i] - 1)
                   if i + horizon < n else None)
        signals.append({
            "date": data["dates"][i],
            "price": float(close[i]),
            f"return_{horizon}": forward,
        })
    measured = [row[f"return_{horizon}"] for row in signals
                if row[f"return_{horizon}"] is not None]
    return {
        "supported": True,
        "strategy": key,
        "name": _BY_KEY[key].name,
        "symbol": symbol,
        "horizon": horizon,
        "evaluated": n - start,
        "signals": signals,
        "count": len(signals),
        "measured": len(measured),
        "avg_return": float(np.mean(measured)) if measured else None,
        "win_rate": (sum(1 for r in measured if r > 0) / len(measured)
                     if measured else None),
        "best": float(np.max(measured)) if measured else None,
        "worst": float(np.min(measured)) if measured else None,
        "prices": [{"date": data["dates"][i], "close": float(close[i])}
                   for i in range(start, n)],
    }
