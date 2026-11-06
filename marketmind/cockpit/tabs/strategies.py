"""Strategies & Opportunities tab — explore, backtest, and compare strategies."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import pandas as pd
import streamlit as st

from marketmind.backtester import BacktestEngine
from marketmind.cockpit.components import dataframe, line_chart
from marketmind.cockpit.state import get_journal
from marketmind.data import DataStore, DataIntegratedScreener
from marketmind.strategies import (
    BaseStrategy,
    CompositeStrategy,
    DebtRatioStrategy,
    SectorExclusionStrategy,
    run_strategy,
)


def _available_strategies(store: DataStore) -> List[BaseStrategy]:
    screener = DataIntegratedScreener(store)
    return [
        DebtRatioStrategy(name="debt_ratio_0.33", data_store=store, screener=screener, max_debt_ratio=0.33),
        DebtRatioStrategy(name="debt_ratio_0.20", data_store=store, screener=screener, max_debt_ratio=0.20),
        SectorExclusionStrategy(name="sector_exclusion", data_store=store, screener=screener),
        CompositeStrategy(
            name="composite_50_50",
            data_store=store,
            screener=screener,
            strategies=[
                DebtRatioStrategy(name="d", data_store=store, screener=screener, max_debt_ratio=0.33),
                SectorExclusionStrategy(name="s", data_store=store, screener=screener),
            ],
            strategy_weights=[0.5, 0.5],
        ),
    ]


def render(store: DataStore) -> None:
    st.subheader("Strategies & Opportunities")

    tab_signals, tab_backtest, tab_compare = st.tabs(
        ["📊 Signal Explorer", "🔬 Backtest", "⚖️ Compare Strategies"]
    )

    default_symbols = ["AAPL", "MSFT", "TSLA", "JPM", "SPUS", "HLAL"]

    with tab_signals:
        _render_signal_explorer(store, default_symbols)

    with tab_backtest:
        _render_backtest(store, default_symbols)

    with tab_compare:
        _render_compare(store, default_symbols)


# ---------------------------------------------------------------------------
# Signal explorer
# ---------------------------------------------------------------------------

def _render_signal_explorer(store: DataStore, symbols: List[str]) -> None:
    st.markdown("### Generate Strategy Signals")
    st.caption("Run a strategy against the symbol universe to see current BUY / HOLD / SELL signals.")

    col1, col2 = st.columns(2)
    with col1:
        strategy_names = [s.name for s in _available_strategies(store)]
        selected = st.selectbox("Strategy", strategy_names)
        symbol_input = st.text_input("Symbols (comma-separated)", value=", ".join(symbols))
    with col2:
        run_btn = st.button("Run Strategy", use_container_width=True, type="primary")

    syms = [s.strip().upper() for s in symbol_input.split(",") if s.strip()]

    if run_btn:
        strategies = _available_strategies(store)
        strat = next((s for s in strategies if s.name == selected), None)
        if strat is None:
            st.error("Strategy not found.")
            return
        try:
            result = run_strategy(strat, syms)
            st.session_state["cockpit_last_signal_result"] = result
        except Exception as exc:  # pylint: disable=broad-except
            st.error(f"Strategy run failed: {exc}")

    result = st.session_state.get("cockpit_last_signal_result")
    if result:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Signals", result["total_signals"])
        col2.metric("BUY", result["buy_count"])
        col3.metric("HOLD", result["hold_count"])
        col4.metric("SELL", result["sell_count"])
        st.caption(f"Generated at: {result.get('generated_at', '-')}")

        sig_df = pd.DataFrame(result["signals"])
        if not sig_df.empty:
            sig_df["strength"] = sig_df["strength"].map(lambda v: f"{v:.3f}")
            dataframe(sig_df, key="signals")


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def _render_backtest(store: DataStore, symbols: List[str]) -> None:
    st.markdown("### Backtest a Strategy")
    st.caption("Run a historical backtest and review performance metrics + equity curve.")

    # Capital is never typed in: backtests start from the original paper money
    # recorded from the IBKR account (or the latest observed net liquidation).
    journal = get_journal()
    baseline = journal.baseline()
    latest = journal.latest()
    initial_capital = (
        baseline.original_capital if baseline
        else (latest["net_liquidation"] if latest else 0.0)
    )

    col1, col2 = st.columns(2)
    with col1:
        strategy_names = [s.name for s in _available_strategies(store)]
        selected = st.selectbox("Strategy", strategy_names, key="bt_strategy")
        symbol_input = st.text_input("Symbols (comma-separated)", value=", ".join(symbols), key="bt_symbols")
        lookback = st.number_input("Lookback days", value=90, min_value=7, max_value=730, step=7)
    with col2:
        st.metric("Starting capital (from IBKR)", f"${initial_capital:,.2f}")
        commission = st.number_input("Commission", value=0.001, step=0.0001, format="%.4f", key="bt_comm")
        slippage = st.number_input("Slippage", value=0.0005, step=0.0001, format="%.4f", key="bt_slip")
        run_btn = st.button("Run Backtest", use_container_width=True, type="primary", key="bt_run")

    syms = [s.strip().upper() for s in symbol_input.split(",") if s.strip()]

    if run_btn:
        strategies = _available_strategies(store)
        strat = next((s for s in strategies if s.name == selected), None)
        if strat is None:
            st.error("Strategy not found.")
            return

        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=lookback)

        try:
            engine = BacktestEngine(
                data_store=store,
                initial_capital=initial_capital,
                commission=commission,
                slippage=slippage,
            )
            results = engine.run(
                strat,
                syms,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            metrics = engine.metrics()

            st.session_state["cockpit_last_backtest"] = {
                "metrics": metrics,
                "results": results,
                "trade_log": engine._trade_log,
                "strategy": selected,
            }
        except Exception as exc:  # pylint: disable=broad-except
            st.error(f"Backtest failed: {exc}")

    bt = st.session_state.get("cockpit_last_backtest")
    if bt:
        metrics = bt["metrics"]
        results = bt["results"]

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Final Equity", f"${metrics.get('final_equity', 0):,.2f}")
        col2.metric("Total Return", f"{metrics.get('total_return', 0):+.2%}")
        col3.metric("Sharpe Ratio", f"{metrics.get('sharpe_ratio', 0):.2f}")
        col4.metric("Max Drawdown", f"{metrics.get('max_drawdown', 0):+.2%}")

        col5, col6 = st.columns(2)
        col5.metric("CAGR", f"{metrics.get('cagr', 0):+.2%}")
        col6.metric("Win Rate", f"{metrics.get('win_rate', 0):+.2%}")

        if not results.empty:
            plot_df = results.reset_index()
            plot_df.columns = [c if c != "index" else "date" for c in plot_df.columns]
            line_chart(plot_df, "date", "total_equity", "Equity Curve")

        if bt["trade_log"]:
            st.markdown("### Trade Log")
            tlog_df = pd.DataFrame(bt["trade_log"])
            dataframe(tlog_df, key="backtest_trades")


# ---------------------------------------------------------------------------
# Compare strategies
# ---------------------------------------------------------------------------

def _render_compare(store: DataStore, symbols: List[str]) -> None:
    st.markdown("### Compare Strategies")
    st.caption("Run multiple strategies over the same period and rank by final equity.")

    symbol_input = st.text_input("Symbols (comma-separated)", value=", ".join(symbols), key="cmp_symbols")
    lookback = st.number_input("Lookback days", value=90, min_value=7, max_value=730, step=7, key="cmp_lookback")
    run_btn = st.button("Compare All Strategies", use_container_width=True, type="primary", key="cmp_run")

    if run_btn:
        syms = [s.strip().upper() for s in symbol_input.split(",") if s.strip()]
        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=lookback)

        strategies = _available_strategies(store)
        comparison = []

        for strat in strategies:
            try:
                engine = BacktestEngine(data_store=store)
                engine.run(
                    strat, syms,
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                )
                metrics = engine.metrics()
                comparison.append(
                    {
                        "strategy": strat.name,
                        "final_equity": metrics.get("final_equity", 0),
                        "total_return": metrics.get("total_return", 0),
                        "sharpe": metrics.get("sharpe_ratio", 0),
                        "max_drawdown": metrics.get("max_drawdown", 0),
                        "win_rate": metrics.get("win_rate", 0),
                        "total_trades": metrics.get("total_trades", 0),
                    }
                )
            except Exception as exc:  # pylint: disable=broad-except
                comparison.append(
                    {
                        "strategy": strat.name,
                        "final_equity": 0,
                        "total_return": 0,
                        "sharpe": 0,
                        "max_drawdown": 0,
                        "win_rate": 0,
                        "total_trades": 0,
                        "error": str(exc),
                    }
                )

        comparison.sort(key=lambda r: r["final_equity"], reverse=True)
        st.session_state["cockpit_strategy_comparison"] = comparison

    comp = st.session_state.get("cockpit_strategy_comparison")
    if comp:
        df = pd.DataFrame(comp)
        for c in ["final_equity", "total_return", "sharpe", "max_drawdown", "win_rate"]:
            if c in df.columns:
                df[c] = df[c].map(lambda v: f"{v:,.2f}" if c == "final_equity" else f"{v:+.2%}" if c in ("total_return", "max_drawdown", "win_rate") else f"{v:.2f}")
        dataframe(df, key="strategy_compare")
