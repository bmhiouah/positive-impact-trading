"""Analytics tab — price bars, fundamentals, and raw data browser."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from marketmind.cockpit.components import candlestick_chart, dataframe


def render(store) -> None:
    st.subheader("Market Data Explorer")

    tab_prices, tab_fundamentals, tab_browser = st.tabs(
        ["📈 Price Bars", "💼 Fundamental Data", "🗄️ Data Browser"]
    )

    with tab_prices:
        _render_prices(store)
    with tab_fundamentals:
        _render_fundamentals(store)
    with tab_browser:
        _render_browser(store)


def _render_prices(store) -> None:
    st.markdown("### Price Bars")
    try:
        symbols = store.conn.execute("SELECT DISTINCT symbol FROM price_bars ORDER BY symbol").fetchall()
        symbol_list = [s[0] for s in symbols]
        if symbol_list:
            selected_symbol = st.selectbox("Select symbol", symbol_list, key="price_symbol")
            bar_rows = store.conn.execute(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM price_bars
                WHERE symbol = ?
                ORDER BY timestamp ASC
                """,
                [selected_symbol],
            ).fetchall()
            if bar_rows:
                bar_df = pd.DataFrame(
                    bar_rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
                )
                bar_df = bar_df.copy()
                bar_df.loc[:, "timestamp"] = (
                    pd.to_datetime(bar_df["timestamp"], utc=True)
                    .dt.tz_convert("UTC")
                    .dt.tz_localize(None)
                    .dt.strftime("%Y-%m-%d %H:%M:%S")
                )
                candlestick_chart(bar_df, selected_symbol)
            else:
                st.info("No price bars for this symbol.")
        else:
            st.info("No price data available.")
    except Exception as e:
        st.error(f"Could not load price bars: {e}")


def _render_fundamentals(store) -> None:
    st.markdown("### Fundamental Data")
    try:
        rows = store.conn.execute(
            """
            SELECT symbol, sector, industry, market_cap, total_debt, debt_to_market_cap,
                   pe_ratio, pb_ratio, dividend_yield, last_updated
            FROM fundamental_data
            ORDER BY symbol
            """
        ).fetchall()
        if rows:
            df = pd.DataFrame(
                rows,
                columns=[
                    "Symbol", "Sector", "Industry", "Market Cap", "Total Debt",
                    "Debt/Market Cap", "P/E Ratio", "P/B Ratio", "Dividend Yield", "Last Updated",
                ],
            )
            dataframe(df, key="analytics_fundamentals")
        else:
            st.info("No fundamental data yet.")
    except Exception as e:
        st.error(f"Could not load fundamental data: {e}")


def _render_browser(store) -> None:
    st.markdown("### Data Browser")
    table_names = [
        "paper_baseline", "paper_daily", "paper_orders",
        "price_bars", "fundamental_data", "screen_results",
        "data_updates", "trades", "portfolio_equity",
        "account_snapshots", "positions", "orders", "executions",
        "market_quotes", "contracts",
    ]
    selected_table = st.selectbox("Select table to view", table_names, key="browser_table")
    limit = st.slider("Rows to show", 10, 500, 50, key="browser_limit")

    if selected_table:
        try:
            rows = store.conn.execute(
                f"SELECT * FROM {selected_table} ORDER BY rowid DESC LIMIT {limit}"
            ).fetchall()
            columns = [d[0] for d in store.conn.execute(f"SELECT * FROM {selected_table} LIMIT 1").description] if rows else []
            if rows:
                df = pd.DataFrame(rows, columns=columns)
                dataframe(df, key="browser_df")
                st.caption(f"Showing {len(rows)} rows from `{selected_table}`")
            else:
                st.info(f"Table `{selected_table}` is empty.")
        except Exception as e:
            st.error(f"Could not load table `{selected_table}`: {e}")
