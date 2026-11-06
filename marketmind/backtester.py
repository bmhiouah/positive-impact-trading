"""Impact-positive trading backtesting platform.

Provides backtesting engine, performance metrics, and visualization
for strategies built on the marketmind data layer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from marketmind.data.models import PriceBar, FundamentalData, ScreenResult
from marketmind.data.storage import DataStore
from marketmind.data.screener import DataIntegratedScreener
from marketmind.strategies import BaseStrategy
from marketmind.compliance.rules import AssetClass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backtest Engine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """Engine for backtesting trading strategies on historical data.

    Supports incremental signal generation and realistic execution
    with commission, slippage, and position tracking.
    """

    def __init__(
        self,
        data_store: DataStore,
        initial_capital: float = 100_000.0,
        commission: float = 0.001,
        slippage: float = 0.0005,
    ) -> None:
        self._store = data_store
        self._initial_capital = initial_capital
        self._commission = commission
        self._slippage = slippage
        self._results: Optional[pd.DataFrame] = None
        self._trade_log: List[Dict[str, object]] = []

    # ------------------------------------------------------------------
    # Core backtest loop
    # ------------------------------------------------------------------

    def run(
        self,
        strategy: BaseStrategy,
        symbols: List[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        bar_size: str = "1 day",
        limit: int = 504,
    ) -> pd.DataFrame:
        """Run a strategy backtest over the given symbol universe.

        The model:
          1. Fetch historical price bars for all symbols
          2. Generate signals once at the start (or rebalance daily)
          3. Hold positions until signals reverse
          4. Track equity curve with commission/slippage

        Args:
            strategy: Strategy instance to test.
            symbols: List of symbols to test on.
            start_date: ISO date string inclusive start.
            end_date: ISO date string inclusive end.
            bar_size: Bar granularity (e.g. "1 day").
            limit: Max bars to fetch per symbol.

        Returns:
            DataFrame with daily equity curve.
        """
        # ------------------------------------------------------------------
        # 1. Fetch historical price data
        # ------------------------------------------------------------------
        price_data: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            bars = self._store.get_price_history(
                sym, bar_size=bar_size, limit=limit
            )
            # Filter by date range if provided
            filtered = []
            for bar in bars:
                bar_str = str(bar.timestamp)[:10] if bar.timestamp else ""
                sd = start_date if start_date else "1900-01-01"
                ed = end_date if end_date else "2100-12-31"
                if sd <= bar_str <= ed:
                    filtered.append(bar)
            if not filtered:
                continue
            filtered.sort(key=lambda b: b.timestamp)
            df = pd.DataFrame(
                {
                    "timestamp": [b.timestamp for b in filtered],
                    "open": [b.open for b in filtered],
                    "high": [b.high for b in filtered],
                    "low": [b.low for b in filtered],
                    "close": [b.close for b in filtered],
                    "volume": [b.volume for b in filtered],
                }
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values("timestamp").reset_index(drop=True)
            price_data[sym] = df

        if not price_data:
            logger.warning("No price data found for %s", symbols)
            return pd.DataFrame()

        # ------------------------------------------------------------------
        # 2. Generate initial signals
        # ------------------------------------------------------------------
        # Strategy.generate_signals returns List[StrategySignal] for the
        # given symbol list. We'll hold these signals for the full period
        # (or until rebalance logic kicks in).
        init_signals = strategy.generate_signals(symbols)

        # Build signal maps: symbol -> {action, strength}
        buy_signals: set = set()
        sell_signals: set = set()
        signal_strengths: Dict[str, float] = {}

        for s in init_signals:
            sym = s.symbol
            action = s.action
            strength = s.strength
            if action == "BUY" and sym in symbols:
                buy_signals.add(sym)
                signal_strengths[sym] = strength
            elif action == "SELL" and sym in symbols:
                sell_signals.add(sym)
                signal_strengths[sym] = strength

        logger.info(
            "Initial signals: BUY=%s SELL=%s",
            buy_signals,
            sell_signals if sell_signals else "none",
        )

        # ------------------------------------------------------------------
        # 3. Determine the full date range
        # ------------------------------------------------------------------
        all_dates: List[pd.Timestamp] = []
        for df in price_data.values():
            if not df.empty:
                all_dates.extend(df["timestamp"].tolist())
        all_dates = sorted(set(all_dates))

        if not all_dates:
            logger.warning("No dates found in price data")
            return pd.DataFrame()

        # ------------------------------------------------------------------
        # 4. Simulate day-by-day
        # ------------------------------------------------------------------
        cash = self._initial_capital
        holdings: Dict[str, int] = {}  # symbol -> shares held
        entry_costs: Dict[str, float] = {}  # symbol -> adjusted entry price
        equity_curve: List[Dict[str, object]] = []

        for date in all_dates:
            # Get closing prices for all symbols as of this date
            day_prices: Dict[str, float] = {}
            for sym in symbols:
                df_sym = price_data.get(sym, pd.DataFrame())
                if not df_sym.empty:
                    hist = df_sym[df_sym["timestamp"] <= date]
                    if not hist.empty:
                        day_prices[sym] = float(hist.iloc[-1]["close"])

            # --- Liquidate positions not getting BUY signals today ---
            for sym in list(holdings.keys()):
                if sym not in buy_signals:
                    price = day_prices.get(sym, 0)
                    if price > 0 and sym in holdings:
                        shares = holdings[sym]
                        proceeds = price * shares * (1 - self._commission)
                        pnl = (price - entry_costs.get(sym, price)) * shares * (1 - self._commission)
                        cash += proceeds
                        self._trade_log.append(
                            {
                                "date": str(date.date()),
                                "symbol": sym,
                                "action": "SELL",
                                "shares": shares,
                                "price": price,
                                "pnl": pnl,
                                "reason": "signal_reversed",
                            }
                        )
                        del holdings[sym]
                        del entry_costs[sym]

            # --- Execute BUY signals for symbols not already held ---
            for sym in buy_signals:
                if sym in holdings:
                    continue  # already hold position
                price = day_prices.get(sym)
                if price is None or price <= 0:
                    continue

                # Apply slippage
                adjusted_price = price * (1 + self._slippage)

                shares = 1  # 1-share incremental tracking
                cost = adjusted_price * shares * (1 + self._commission)

                if cash >= cost:
                    holdings[sym] = shares
                    entry_costs[sym] = adjusted_price
                    cash -= cost
                    self._trade_log.append(
                        {
                            "date": str(date.date()),
                            "symbol": sym,
                            "action": "BUY",
                            "shares": shares,
                            "price": adjusted_price,
                            "pnl": 0,
                            "reason": f"initial_signal_strength={signal_strengths.get(sym, 0):.2f}",
                        }
                    )

            # --- Record equity ---
            total_market_value = sum(
                holdings.get(sym, 0) * day_prices.get(sym, 0) for sym in symbols
            )
            total_equity = cash + total_market_value

            equity_curve.append(
                {
                    "date": str(date.date()),
                    "cash": cash,
                    "market_value": total_market_value,
                    "total_equity": total_equity,
                    "positions": dict(holdings),
                }
            )

        # ------------------------------------------------------------------
        # 5. Construct results DataFrame
        # ------------------------------------------------------------------
        results_df = pd.DataFrame(equity_curve)
        if not results_df.empty:
            results_df["date"] = pd.to_datetime(results_df["date"])
            results_df.set_index("date", inplace=True)

        self._results = results_df
        return results_df

    # --------------------------------------------------------------------------
    # Performance metrics
    # --------------------------------------------------------------------------

    def metrics(self) -> Dict[str, float]:
        """Calculate performance metrics from the last backtest run."""
        if self._results is None or self._results.empty:
            return {}

        eq = self._results["total_equity"]
        initial = self._initial_capital

        if eq.empty or initial <= 0:
            return {}

        # CAGR
        n_days = max(1, (eq.index[-1] - eq.index[0]).days)
        total_return = eq.iloc[-1] / initial - 1.0
        cagr = (1.0 + total_return) ** (365.0 / n_days) - 1.0

        # Daily returns
        daily_returns = eq.pct_change().dropna()

        # Volatility (annualized)
        annual_vol = daily_returns.std() * np.sqrt(365) if len(daily_returns) > 1 else 0.0

        # Sharpe ratio (risk-free = 0 for simplicity)
        sharpe = (daily_returns.mean() * 365) / annual_vol if annual_vol != 0 else 0.0

        # Max drawdown
        peak = eq.cummax()
        drawdown = (eq - peak) / peak
        max_dd = drawdown.min()

        # Win rate from trade log
        win_rate = 0.0
        if self._trade_log:
            winning = [t for t in self._trade_log if t.get("pnl", 0) > 0]
            losing = [t for t in self._trade_log if t.get("pnl", 0) < 0]
            total = len(self._trade_log)
            win_rate = len(winning) / total if total > 0 else 0.0

        return {
            "initial_capital": initial,
            "final_equity": float(eq.iloc[-1]),
            "total_return": float(total_return),
            "cagr": cagr,
            "annual_volatility": annual_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": float(max_dd),
            "win_rate": win_rate,
            "total_trades": len(self._trade_log),
        }

    # --------------------------------------------------------------------------
    # Visualization
    # --------------------------------------------------------------------------

    def plot_equity_curve(self, figsize: Tuple[int, int] = (10, 6)) -> None:
        """Plot the equity curve from the last backtest run."""
        if self._results is None or self._results.empty:
            logger.error("No backtest results to plot. Run a backtest first.")
            return

        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=figsize)
        ax.plot(self._results.index, self._results["total_equity"], label="Equity", color="#2c7bb6")
        ax.axhline(self._initial_capital, color="gray", linestyle="--", label="Initial Capital")
        ax.set_title("Strategy Equity Curve")
        ax.set_ylabel("Portfolio Value ($)")
        ax.set_xlabel("Date")
        ax.legend()
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.show()

    def plot_drawdown(self, figsize: Tuple[int, int] = (10, 4)) -> None:
        """Plot drawdown curve."""
        if self._results is None or self._results.empty:
            logger.error("No backtest results to plot.")
            return

        import matplotlib.pyplot as plt

        eq = self._results["total_equity"]
        peak = eq.cummax()
        drawdown = (eq - peak) / peak

        fig, ax = plt.subplots(figsize=figsize)
        ax.fill_between(drawdown.index, drawdown, 0, color="red", alpha=0.3, label="Drawdown")
        ax.plot(drawdown.index, drawdown, color="red", linewidth=1)
        ax.axhline(0, color="gray", linestyle="--")
        ax.set_title("Drawdown Curve")
        ax.set_ylabel("Drawdown (%)")
        ax.set_xlabel("Date")
        ax.legend()
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.show()

    def plot_trade_distribution(self, figsize: Tuple[int, int] = (8, 5)) -> None:
        """Plot histogram of trade P&Ls."""
        if not self._trade_log:
            logger.error("No trade log available.")
            return

        import matplotlib.pyplot as plt

        pnls = [t["pnl"] for t in self._trade_log if "pnl" in t]

        fig, ax = plt.subplots(figsize=figsize)
        ax.hist(pnls, bins=20, color="teal", edgecolor="black", alpha=0.7)
        ax.axvline(0, color="black", linestyle="--", linewidth=1)
        ax.set_title("Trade P&L Distribution")
        ax.set_xlabel("P&L ($)")
        ax.set_ylabel("Frequency")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.show()
