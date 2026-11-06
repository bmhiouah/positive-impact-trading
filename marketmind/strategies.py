"""Impact-positive trading strategy framework.

Provides tools for backtesting, signal generation, and strategy evaluation
using the marketmind data layer. Supports incremental strategy development
with compliance-aware filtering.

Design principles:
  * Data-driven — uses stored DuckDB data for reproducibility
  * Compliance-aware — automatically filters by impact rules
  * Incremental — only process new data
  * Multi-strategy — easy to add/remove strategy components
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol

import numpy as np
import pandas as pd

from marketmind.data.models import PriceBar, FundamentalData, ScreenResult
from marketmind.data.storage import DataStore
from marketmind.data.screener import DataIntegratedScreener
from marketmind.compliance.rules import AssetClass, MAX_DEBT_TO_MARKET_CAP

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal type
# ---------------------------------------------------------------------------

class StrategySignal:
    """Dictionary-based signal type produced by strategies."""
    def __init__(self, symbol: str, action: str, strength: float, rationale: str, timestamp: datetime):
        self.symbol = symbol
        self.action = action
        self.strength = strength
        self.rationale = rationale
        self.timestamp = timestamp


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class BaseStrategy:
    """Abstract base class for impact-positive trading strategies."""

    def __init__(self, name: str, data_store: DataStore, screener: DataIntegratedScreener) -> None:
        self.name = name
        self._store = data_store
        self._screener = screener
        self.signals: List[StrategySignal] = []

    def _check_compliance(self, symbol: str, asset_class: AssetClass) -> bool:
        try:
            result = self._screener.screen_symbol(symbol, asset_class)
            return result.passed
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Compliance check failed for %s: %s", symbol, e)
            return False


# ---------------------------------------------------------------------------
# DebtRatioStrategy
# ---------------------------------------------------------------------------

class DebtRatioStrategy(BaseStrategy):
    """Strategy screens based on debt-to-market-cap ratio.

    Only considers symbols with debt/MCap < threshold as candidates.
    Ranks by lowest debt ratio (most financially healthy).
    """

    def __init__(
        self,
        name: str,
        data_store: DataStore,
        screener: DataIntegratedScreener,
        max_debt_ratio: float = MAX_DEBT_TO_MARKET_CAP,
    ) -> None:
        super().__init__(name, data_store, screener)
        self.max_debt_ratio = max_debt_ratio

    def generate_signals(self, symbols: List[str]) -> List[StrategySignal]:
        signals: List[StrategySignal] = []
        for symbol in symbols:
            fundamentals = self._store.get_fundamental_data(symbol)
            if fundamentals is None:
                continue
            asset_class = AssetClass.EQUITY
            if not self._check_compliance(symbol, asset_class):
                continue
            debt_ratio = fundamentals.debt_to_market_cap
            if debt_ratio is None or debt_ratio >= self.max_debt_ratio:
                continue
            strength = 1.0 - (debt_ratio / self.max_debt_ratio)
            signal = StrategySignal(
                symbol=symbol, action="BUY", strength=round(strength, 3),
                rationale=f"Debt/MCap={debt_ratio:.1%} under threshold", timestamp=datetime.utcnow()
            )
            signals.append(signal)
        signals.sort(key=lambda s: s.strength, reverse=True)
        self.signals = signals
        return signals


# ---------------------------------------------------------------------------
# SectorExclusionStrategy
# ---------------------------------------------------------------------------

class SectorExclusionStrategy(BaseStrategy):
    """Strategy avoids excluded sectors per impact principles."""

    EXCLUDED_SECTORS = {
        "financial_services",
        "alcohol",
        "gambling",
        "adult_entertainment",
        "tobacco",
        "weapons",
    }

    def generate_signals(self, symbols: List[str]) -> List[StrategySignal]:
        signals: List[StrategySignal] = []
        for symbol in symbols:
            fundamentals = self._store.get_fundamental_data(symbol)
            if fundamentals is None:
                continue
            sector = fundamentals.sector or ""
            if sector.lower().strip() in self.EXCLUDED_SECTORS:
                continue
            signal = StrategySignal(
                symbol=symbol, action="HOLD", strength=0.5,
                rationale=f"Sector '{sector}' is impact-compliant", timestamp=datetime.utcnow()
            )
            signals.append(signal)
        self.signals = signals
        return signals


# ---------------------------------------------------------------------------
# CompositeStrategy
# ---------------------------------------------------------------------------

class CompositeStrategy(BaseStrategy):
    """Combines multiple strategies with weighting.

    Each constituent strategy generates signals independently.
    Signal strengths are weighted by the strategy's assigned weight
    (weights must sum to 1.0 across all constituent strategies).
    Duplicate symbols are resolved by keeping the highest strength.
    """

    def __init__(
        self,
        name: str,
        data_store: DataStore,
        screener: DataIntegratedScreener,
        strategies: List[BaseStrategy],
        strategy_weights: Optional[List[float]] = None,
    ) -> None:
        super().__init__(name, data_store, screener)
        self.strategies = strategies
        if strategy_weights is None:
            n = len(strategies)
            self.strategy_weights = [1.0 / n] * n if n > 0 else []
        else:
            if len(strategy_weights) != len(strategies):
                raise ValueError("strategy_weights must match strategies length")
            total_weight = sum(strategy_weights)
            if abs(total_weight - 1.0) > 0.001:
                raise ValueError("strategy_weights must sum to 1.0")
            self.strategy_weights = strategy_weights

    def generate_signals(self, symbols: List[str]) -> List[StrategySignal]:
        all_signals: List[StrategySignal] = []

        for strategy, weight in zip(self.strategies, self.strategy_weights):
            strategy_signals = strategy.generate_signals(symbols)
            for s in strategy_signals:
                new_strength = round(s.strength * weight, 3)
                rationale = f"[{strategy.name}] {s.rationale}"
                weighted_signal = StrategySignal(
                    symbol=s.symbol,
                    action=s.action,
                    strength=new_strength,
                    rationale=rationale,
                    timestamp=s.timestamp,
                )
                all_signals.append(weighted_signal)

        # Deduplicate by symbol, keep highest strength
        seen: Dict[str, StrategySignal] = {}
        for signal in all_signals:
            sym = signal.symbol
            if sym not in seen or signal.strength > seen[sym].strength:
                seen[sym] = signal

        # Sort by strength (strongest first)
        final_signals = sorted(seen.values(), key=lambda s: s.strength, reverse=True)
        self.signals = final_signals
        return final_signals


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

async def run_strategy(strategy: BaseStrategy, symbols: List[str]) -> Dict[str, Any]:
    """Run a strategy and return results dict.

    Args:
        strategy: Strategy instance to run.
        symbols: List of symbols to generate signals for.

    Returns:
        Dictionary with signal counts, strength statistics, and individual signals.
    """
    signals = strategy.generate_signals(symbols)

    buy_signals = [s for s in signals if s.action == "BUY"]
    hold_signals = [s for s in signals if s.action == "HOLD"]
    sell_signals = [s for s in signals if s.action == "SELL"]

    buy_strengths = [s.strength for s in buy_signals]

    return {
        "strategy_name": strategy.name,
        "total_signals": len(signals),
        "buy_count": len(buy_signals),
        "hold_count": len(hold_signals),
        "sell_count": len(sell_signals),
        "buy_avg_strength": round(sum(buy_strengths) / len(buy_strengths), 3) if buy_strengths else 0,
        "signals": [
            {
                "symbol": s.symbol,
                "action": s.action,
                "strength": s.strength,
                "rationale": s.rationale,
                "timestamp": s.timestamp.isoformat(),
            }
            for s in signals
        ],
        "generated_at": datetime.utcnow().isoformat(),
    }
