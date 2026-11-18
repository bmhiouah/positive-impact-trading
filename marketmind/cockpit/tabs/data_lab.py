"""Data & Universe — everything the IBKR API gave us, in one organised place.

This is the scalability surface: add a ticker here and it automatically joins
ingestion, coverage, strategies and the trading runner. No code change needed.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketmind import config
from marketmind.cockpit import theme
from marketmind.cockpit.state import get_market, get_state, sync_data, sync_status


def _quote_pair(quote: Dict[str, Any]) -> str:
    if not quote:
        return "—"

    def _px(value: Any) -> str:
        return f"{value:,.2f}" if isinstance(value, (int, float)) else "—"

    bid, ask = quote.get("bid"), quote.get("ask")
    if bid is None and ask is None:
        return "—"
    return f"{_px(bid)} / {_px(ask)}"


def _status_pill(status: str) -> str:
    return {"ready": "Ready", "partial": "Partial", "empty": "No data"}.get(
        status, status
    )


def _coverage_table(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    records = []
    for row in rows:
        quote = row.get("quote") or {}
        records.append({
            "Symbol": row["symbol"],
            "Name": (row.get("name") or "")[:34],
            "Sector": row.get("sector") or "—",
            "Status": _status_pill(row["status"]),
            "Daily bars": row["bars"],
            "Last close": (f"{row['last_close']:,.2f}"
                           if row.get("last_close") else "—"),
            "Bid / Ask": _quote_pair(quote),
            "Last sync": (str(row.get("last_sync") or "—")[:16]
                          .replace("T", " ")),
            "First / last bar": (
                f"{str(row.get('first_bar') or '—')[:10]} → "
                f"{str(row.get('last_bar') or '—')[:10]}"
            ),
        })
    return pd.DataFrame(records)


def _price_chart(series: List[Dict[str, Any]], symbol: str) -> go.Figure:
    df = pd.DataFrame(series)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["timestamp"].str[:10],
        open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        name=symbol,
        increasing=dict(line=dict(color=theme.UP),
                        fillcolor=theme.UP),
        decreasing=dict(line=dict(color=theme.DOWN),
                        fillcolor=theme.DOWN),
    ))
    if len(df) >= 50:
        fig.add_trace(go.Scatter(
            x=df["timestamp"].str[:10],
            y=df["close"].rolling(50).mean(),
            mode="lines", name="SMA 50",
            line=dict(color=theme.BRAND, width=1.8),
        ))
    fig.update_xaxes(rangeslider_visible=False)
    theme.style_figure(fig, height=400, legend=True)
    return fig


def _volume_chart(series: List[Dict[str, Any]]) -> go.Figure:
    df = pd.DataFrame(series)
    fig = go.Figure(go.Bar(
        x=df["timestamp"].str[:10], y=df["volume"],
        marker_color=theme.CASH, name="Volume",
    ))
    theme.style_figure(fig, height=170)
    return fig


def _render_sync(state) -> None:
    left, right = st.columns([1, 3])
    with left:
        if st.button("Sync from IBKR", disabled=state.algo_running,
                     type="primary", width="stretch", key="sync_ibkr"):
            with st.spinner("One connection: contracts → daily bars → quotes…"):
                sync_data()
            st.rerun()
    with right:
        if state.sync_error:
            st.error(state.sync_error)
        elif state.algo_running:
            st.caption(
                "The algo owns the only IBKR session right now. It refreshes "
                "the data itself on its first cycle. Stop it to sync manually."
            )
        elif state.last_sync:
            result = state.last_sync
            st.caption(
                f"Last sync: {result['ok']}/{result['symbols']} instruments OK · "
                f"{result['bars']:,} bars ingested · "
                f"{str(result['at'])[:19].replace('T', ' ')} UTC"
            )
        else:
            st.caption(
                f"Pulls {config.HISTORY_DURATION} of {config.HISTORY_BAR_SIZE} "
                "bars, contract metadata and delayed quotes for every active "
                "ticker, using the single client-id-1 session."
            )


def _render_universe(market) -> None:
    universe = market.universe
    rows = universe.all_rows()
    theme.section("Universe", f"{len(rows)} tickers · adding one needs no code change")
    left, right = st.columns([2, 3])

    with left:
        with st.form("add_instrument", clear_on_submit=True):
            new_symbol = st.text_input(
                "Add a ticker", placeholder="e.g. KO, ASML, V",
                help="US-listed stocks and ETFs resolvable by IBKR SMART routing.",
            )
            if st.form_submit_button("Add to universe", use_container_width=True):
                if not new_symbol.strip():
                    st.warning("Enter a ticker first.")
                else:
                    try:
                        added = universe.add(new_symbol)
                        st.success(f"{added} added — sync to fetch its history.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

        inactive = [r["symbol"] for r in rows if not r["active"]]
        if inactive:
            target = st.selectbox("Inactive tickers", inactive,
                                  key="reactivate_symbol")
            if st.button("Reactivate", use_container_width=True):
                universe.set_active(target, True)
                st.rerun()
        else:
            st.caption("Deactivated tickers keep their history but stop being "
                       "synced and traded.")

    with right:
        table = pd.DataFrame([{
            "Symbol": r["symbol"],
            "Name": (r["name"] or "—")[:36],
            "Exchange": r["exchange"] or "—",
            "Currency": r["currency"],
            "Sector": r["sector"] or "—",
            "Active": "Yes" if r["active"] else "Paused",
        } for r in rows])
        st.dataframe(table, width="stretch", hide_index=True, height=260)

    active = [r["symbol"] for r in rows if r["active"]]
    with st.expander("Pause a ticker", expanded=False):
        if active:
            cols = st.columns([3, 1])
            target = cols[0].selectbox("Ticker to pause", active,
                                       key="pause_symbol")
            if cols[1].button("Pause", use_container_width=True):
                universe.set_active(target, False)
                st.rerun()


def _render_price_explorer(market, coverage) -> None:
    with_data = [r["symbol"] for r in coverage if r["bars"] > 0]
    if not with_data:
        theme.empty("No price history yet",
                    "Run a sync from IBKR first — the chart is built from daily bars.")
        return
    selected = st.selectbox("Symbol", with_data, key="explorer_symbol")
    series = market.price_series(selected, limit=260)
    if not series:
        return
    st.plotly_chart(_price_chart(series, selected), width="stretch",
                    config={"displayModeBar": False})
    st.plotly_chart(_volume_chart(series), width="stretch",
                    config={"displayModeBar": False})
    st.caption(
        f"{len(series)} daily bars · delayed market data (type "
        f"{config.MARKET_DATA_TYPE}). Rising candles are green, falling candles are red."
    )


def render(store=None) -> None:
    theme.page_header(
        "Data",
        "Data & Universe",
        "What the Gateway has actually delivered, and the tickers this lab "
        "is allowed to trade.",
    )

    state = sync_status(get_state())
    market = get_market()
    coverage = market.coverage()
    summary = market.summary()

    theme.card_row([
        theme.card("Active instruments", f"{summary['instruments']}",
                   "One table drives ingestion and trading"),
        theme.card("Data-ready", f"{summary['ready']}",
                   f"≥ {config.MIN_BARS_FOR_SIGNALS} fresh daily bars",
                   tone="blue" if summary["ready"] else "default"),
        theme.card("Daily bars stored", f"{summary['bars']:,}",
                   f"Idempotent upserts · {config.HISTORY_DURATION} history"),
    ])

    theme.section("IBKR sync", "One connection at a time")
    _render_sync(state)

    tabs = st.tabs([
        "Coverage", "Universe", "Prices", "Sync log",
    ])
    with tabs[0]:
        if coverage:
            st.dataframe(_coverage_table(coverage), width="stretch",
                         hide_index=True)
            st.caption(
                "Ready = enough daily bars and a sync within "
                f"{config.DATA_STALE_HOURS}h. Partial = some history but not "
                "enough or too old. The runner refreshes stale data on its own "
                "connection when it starts."
            )
        else:
            theme.empty("The universe is empty", "Add a ticker below.")
    with tabs[1]:
        _render_universe(market)
    with tabs[2]:
        _render_price_explorer(market, coverage)
    with tabs[3]:
        logs = market.sync_log(100)
        if logs:
            st.dataframe(pd.DataFrame(logs).rename(columns={
                "symbol": "Symbol", "update_type": "Type", "records": "Bars",
                "started_at": "Started (UTC)", "completed_at": "Completed (UTC)",
                "status": "Status", "error": "Error",
            }), width="stretch", hide_index=True)
        else:
            theme.empty("No sync yet", "Each symbol's ingestion result is audited here.")
