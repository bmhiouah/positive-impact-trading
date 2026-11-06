"""Overview — the first page: where the paper money is, and how it evolved.

A camembert shows the split between idle cash and money invested in long
equity, plus the original paper money (recorded once from the IBKR API),
the daily evolution and every TP/SL bracket that was actually sent.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketmind import config
from marketmind.cockpit.components import fmt_currency, fmt_pct
from marketmind.cockpit.state import (
    get_journal,
    get_state,
    refresh_snapshot,
    sync_status,
)
from marketmind.journal import allocation_of

# Light-theme palette (explicit, so the chart stays readable in any IDE theme).
CASH_COLOR = "#7FB2E5"
EQUITY_COLOR = "#2E8B7A"
TEXT_COLOR = "#1F2933"
GRID_COLOR = "#E5E9F0"


def _pie(cash: float, equity: float):
    """The camembert: cash vs long equity (zero slices are left out)."""
    labels, values, colors = [], [], []
    for label, value, color in (
        ("Cash", cash, CASH_COLOR),
        ("Long equity", equity, EQUITY_COLOR),
    ):
        if value > 0:
            labels.append(label)
            values.append(value)
            colors.append(color)
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                marker=dict(colors=colors,
                            line=dict(color="#FFFFFF", width=2)),
                textinfo="label+percent",
                textfont=dict(size=14, color="#FFFFFF"),
                hovertemplate="%{label}: $%{value:,.2f} (%{percent})<extra></extra>",
                sort=False,
            )
        ]
    )
    fig.update_layout(
        title=dict(text="Paper money — cash vs long equity",
                   font=dict(size=16, color=TEXT_COLOR), x=0.5),
        height=380,
        margin=dict(l=20, r=20, t=60, b=20),
        paper_bgcolor="rgba(255,255,255,0)",
        font=dict(color=TEXT_COLOR),
        legend=dict(orientation="h", y=-0.05, x=0.5, xanchor="center"),
        showlegend=True,
    )
    return fig


def _equity_chart(history, original: float):
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[row["date_utc"] for row in history],
            y=[row["net_liquidation"] for row in history],
            mode="lines+markers",
            name="Net liquidation",
            line=dict(color=EQUITY_COLOR, width=2),
            marker=dict(size=7),
        )
    )
    fig.add_hline(
        y=original, line_dash="dash", line_color="#9AA5B1",
        annotation_text="Original paper money",
        annotation_font_color=TEXT_COLOR,
    )
    fig.update_layout(
        title=dict(text="How the paper money evolves (daily)",
                   font=dict(size=16, color=TEXT_COLOR), x=0.5),
        height=340,
        margin=dict(l=20, r=20, t=60, b=20),
        paper_bgcolor="rgba(255,255,255,0)",
        plot_bgcolor="rgba(255,255,255,0)",
        font=dict(color=TEXT_COLOR),
        xaxis=dict(showgrid=False, title="Day (UTC)"),
        yaxis=dict(showgrid=True, gridcolor=GRID_COLOR, title="USD"),
    )
    return fig


def render(store=None) -> None:
    st.subheader("Paper Trading Overview")
    state = sync_status(get_state())
    journal = get_journal()
    baseline = journal.baseline()

    # --- header row: refresh + baseline ---------------------------------
    col_refresh, col_info = st.columns([1, 3])
    with col_refresh:
        if st.button("🔄 Read from IBKR", disabled=state.algo_running,
                     use_container_width=True):
            with st.spinner("Connecting → reading account → disconnecting…"):
                refresh_snapshot()
            st.rerun()
        if state.refresh_error:
            st.error(state.refresh_error)
        elif state.snapshot_at:
            st.caption(f"Last read: {state.snapshot_at[:19]} UTC")
        elif state.algo_running:
            st.caption("The algo is refreshing the journal each cycle.")

    with col_info:
        if baseline:
            st.caption(
                f"Original paper money recorded on "
                f"{baseline.recorded_at_utc[:10]} — from IBKR account "
                f"{baseline.account_id or '—'}"
            )
            st.markdown(f"### {fmt_currency(baseline.original_capital)}")
        else:
            st.info(
                "No baseline yet. Press **🔄 Read from IBKR** (or Start Algo) "
                "to record the original paper money from the API."
            )

    st.markdown("---")

    # --- the camembert ---------------------------------------------------
    if state.live_snapshot is not None:
        split = allocation_of(state.live_snapshot)
        current = float(state.live_snapshot.net_liquidation)
        source = "live read"
    else:
        split = journal.split()
        latest = journal.latest()
        current = float(latest["net_liquidation"]) if latest else 0.0
        source = "journal"

    left, right = st.columns([3, 2])
    with left:
        if split is None or split.total <= 0:
            st.info("No account data yet — connect to IBKR to see the split.")
        else:
            st.plotly_chart(_pie(split.cash, split.equity), width="stretch")
            st.caption(f"Source: {source} · long-only, no leverage, no shorts.")
    with right:
        st.markdown("#### Where the money is")
        if split is not None:
            st.metric("Cash", fmt_currency(split.cash), f"{split.cash_pct:.1f}%")
            st.metric("Long equity", fmt_currency(split.equity),
                      f"{split.equity_pct:.1f}%")
            st.metric("Total (Net Liq)", fmt_currency(split.total))
        if baseline and current:
            pnl = current - baseline.original_capital
            pct = 100.0 * pnl / baseline.original_capital
            st.markdown("#### Since the beginning")
            st.metric("P&L vs original", fmt_currency(pnl), fmt_pct(pct))

    st.markdown("---")

    # --- daily evolution -------------------------------------------------
    history = journal.daily_history()
    if history:
        original = baseline.original_capital if baseline else history[0]["net_liquidation"]
        st.plotly_chart(_equity_chart(history, original), width="stretch")
        if len(history) == 1:
            st.caption("One day recorded so far — come back tomorrow to see the curve grow.")
    else:
        st.info("No daily observations yet.")

    # --- what was traded --------------------------------------------------
    st.markdown("#### Trades done (TP/SL brackets)")
    st.caption(
        f"Systematic take-profit +{config.TAKE_PROFIT_PCT:.0%} / "
        f"stop-loss -{config.STOP_LOSS_PCT:.0%} on every entry."
    )
    orders = journal.orders(limit=100)
    if orders:
        df = pd.DataFrame(orders)
        df = df.rename(columns={
            "timestamp_utc": "When (UTC)", "symbol": "Symbol", "side": "Side",
            "quantity": "Qty", "entry_order_id": "Entry id",
            "entry_type": "Entry", "entry_price": "Entry price",
            "reference_price": "Reference", "take_profit_price": "Take profit",
            "stop_loss_price": "Stop loss", "status": "Status", "note": "Note",
        })
        columns = ["When (UTC)", "Symbol", "Side", "Qty", "Entry", "Reference",
                   "Take profit", "Stop loss", "Status", "Note", "Entry id"]
        df = df[[c for c in columns if c in df.columns]]
        df["When (UTC)"] = df["When (UTC)"].str[:19]
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info("No trades sent yet. Brackets will appear here as soon as a "
                "strategy produces a long entry.")

    # --- live positions ---------------------------------------------------
    if state.algo_running and state.algo_runner is not None:
        try:
            positions = state.algo_runner.adapter.get_positions()
            if positions:
                st.markdown("#### Open long positions (IBKR)")
                st.dataframe(pd.DataFrame(positions), width="stretch",
                             hide_index=True)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Could not read positions: {exc}")
