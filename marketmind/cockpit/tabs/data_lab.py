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
            "Bid / Ask": (
                f"{quote.get('bid') or 0:,.2f} / {quote.get('ask') or 0:,.2f}"
                if quote else "—"
            ),
            "Debt data": "Yes" if row.get("fundamentals") else "Missing",
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
        if st.button("⬇  Sync from IBKR", disabled=state.algo_running,
                     type="primary", use_container_width=True):
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


def _render_compliance(market, store, coverage) -> None:
    theme.callout(
        "IBKR's paper account refuses <code>reqFundamentalData</code> "
        "(error 10358), so debt-to-market-cap and excluded sectors cannot be "
        "read from the API. Enter them here: the impact screen reads this same "
        "table and <b>blocks any equity without a debt ratio</b>. Whitelisted "
        "impact ETFs (SPUS, HLAL…) are exempt.",
        tone="warn", caption="The one dataset IBKR will not give us",
    )
    symbols = [row["symbol"] for row in coverage] or market.universe.symbols()
    if not symbols:
        return
    selected = st.selectbox("Instrument", symbols, key="fundamental_symbol")
    existing = store.get_fundamental_data(selected)
    current_pct = 100.0 * float(
        existing.debt_to_market_cap if existing and
        existing.debt_to_market_cap is not None else 0.0
    )

    with st.form("fundamental_form"):
        c1, c2 = st.columns(2)
        sector = c1.text_input("Sector", value=(existing.sector if existing else "") or "",
                               placeholder="e.g. Technology")
        industry = c2.text_input("Industry",
                                 value=(existing.industry if existing else "") or "")
        c3, c4, c5 = st.columns(3)
        ratio_pct = c3.number_input("Debt / market cap (%)", min_value=0.0,
                                    max_value=100.0, value=current_pct,
                                    step=0.5, format="%.2f")
        market_cap = c4.number_input("Market cap (optional)", min_value=0.0,
                                     value=float(existing.market_cap or 0)
                                     if existing else 0.0, format="%.0f")
        total_debt = c5.number_input("Total debt (optional)", min_value=0.0,
                                     value=float(existing.total_debt or 0)
                                     if existing else 0.0, format="%.0f")
        if st.form_submit_button("Save compliance data", type="primary"):
            try:
                ratio = (
                    total_debt / market_cap
                    if ratio_pct == 0 and market_cap > 0 and total_debt > 0
                    else ratio_pct / 100.0
                )
                market.set_fundamental(
                    selected,
                    sector=sector.strip() or None,
                    industry=industry.strip() or None,
                    debt_to_market_cap=ratio,
                    market_cap=market_cap or None,
                    total_debt=total_debt or None,
                )
                st.success(f"{selected}: saved. The screen now sees "
                           f"{ratio_pct:.2f}% debt / market cap.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    limit = 0.33
    status = ("compliant" if ratio_pct / 100.0 < limit else "over the 33% limit")
    st.caption(f"Current screen: {ratio_pct:.2f}% — {status}. "
               "Values are stored in `fundamental_data`, the same source the "
               "compliance gate uses.")

    all_rows = store.conn.execute(
        """SELECT symbol, sector, industry, debt_to_market_cap, currency, source
           FROM fundamental_data ORDER BY symbol"""
    ).fetchall()
    if all_rows:
        st.dataframe(
            pd.DataFrame(all_rows, columns=["Symbol", "Sector", "Industry",
                                            "Debt / MCap", "Currency", "Source"]),
            width="stretch", hide_index=True,
        )


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
        f"{config.MARKET_DATA_TYPE}) · rising candles red / falling green."
    )


def render(store=None) -> None:
    theme.page_header(
        "02 / Data",
        "Data & Universe",
        "Gather what the IB Gateway API can actually provide, keep it tidy, and "
        "extend the universe without touching code. Strategies only run on data "
        "that is really here.",
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
        theme.card("Debt ratios", f"{summary['with_fundamentals']}",
                   "Entered manually — API fundamentals unavailable"),
    ])

    theme.section("IBKR sync", "One connection at a time")
    _render_sync(state)

    tabs = st.tabs([
        "📊  Coverage", "🌍  Universe", "🛡  Compliance data",
        "📈  Price explorer", "🧾  Sync log",
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
            theme.empty("The universe is empty", "Add a ticker in the Universe tab.")
    with tabs[1]:
        _render_universe(market)
    with tabs[2]:
        _render_compliance(market, store, coverage)
    with tabs[3]:
        _render_price_explorer(market, coverage)
    with tabs[4]:
        logs = market.sync_log(100)
        if logs:
            st.dataframe(pd.DataFrame(logs).rename(columns={
                "symbol": "Symbol", "update_type": "Type", "records": "Bars",
                "started_at": "Started (UTC)", "completed_at": "Completed (UTC)",
                "status": "Status", "error": "Error",
            }), width="stretch", hide_index=True)
        else:
            theme.empty("No sync yet", "Each symbol's ingestion result is audited here.")
