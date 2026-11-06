"""Shared UI components for the cockpit."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


def fmt_currency(val) -> str:
    try:
        return f"${val:,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def fmt_pct(val) -> str:
    try:
        return f"{val:+.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def metric_card(label: str, value, delta: Optional[str] = None, delta_color: str = "normal") -> None:
    st.metric(label, value, delta=delta, delta_color=delta_color)


def dataframe(df: pd.DataFrame, key: Optional[str] = None, height: Optional[int] = None) -> None:
    if df is None or df.empty:
        st.info("No data available.")
        return
    st.dataframe(df, width="stretch", hide_index=True, height=height, key=key)


def line_chart(df: pd.DataFrame, x: str, y: str, title: str, height: int = 400) -> None:
    if df.empty:
        st.info(f"No data for {title}.")
        return
    fig = px.line(df, x=x, y=y, title=title)
    fig.update_layout(height=height, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig, width="stretch")


def candlestick_chart(df: pd.DataFrame, symbol: str, height: int = 500) -> None:
    if df.empty:
        st.info(f"No price data for {symbol}.")
        return
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=df["timestamp"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
            )
        ]
    )
    fig.update_layout(
        title=f"{symbol} Price Chart",
        height=height,
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis_rangeslider_visible=False,
    )
    st.plotly_chart(fig, width="stretch")


def status_badge(ok: bool) -> str:
    return "🟢" if ok else "🔴"
