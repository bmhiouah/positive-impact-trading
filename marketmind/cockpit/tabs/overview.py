"""Overview — the account explained, not just displayed.

The headline number is split into trading P&L and interest/other, because a
flat paper account earns interest on idle cash. Showing only
``NetLiquidation - original`` as "P&L" makes an empty trades table look broken.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketmind import config
from marketmind.cockpit import theme
from marketmind.cockpit.state import (
    get_journal, get_market, get_state, refresh_snapshot, sync_status,
)
from marketmind.journal import PnlBreakdown, allocation_of


def _donut(cash: float, equity: float, currency: str) -> go.Figure:
    labels, values, colors = [], [], []
    for label, value, color in (
        ("Idle cash", cash, theme.CASH),
        ("Long equity", equity, theme.EQUITY),
    ):
        if value > 0:
            labels.append(label)
            values.append(value)
            colors.append(color)
    fig = go.Figure(
        data=[go.Pie(
            labels=labels, values=values, hole=0.62, sort=False,
            marker=dict(colors=colors, line=dict(color=theme.SURFACE, width=3)),
            textinfo="label+percent",
            textfont=dict(size=12, color="#FFFFFF"),
            hovertemplate=(
                "%{label}: " + theme.symbol(currency)
                + "%{value:,.2f} (%{percent})<extra></extra>"
            ),
        )]
    )
    theme.style_figure(fig, height=310)
    fig.update_layout(
        showlegend=False,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        annotations=[dict(
            text=f"<b>{theme.money(cash + equity, currency)}</b>"
                 f"<br><span style='font-size:11px'>Net liquidation</span>",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=17, color=theme.INK),
        )],
    )
    return fig


def _value_chart(history, original: float, currency: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[r["date_utc"] for r in history],
        y=[r["net_liquidation"] for r in history],
        mode="lines+markers", name="Net liquidation",
        line=dict(color=theme.UP, width=2.5),
        marker=dict(size=8, color=theme.UP,
                    line=dict(color=theme.PAGE, width=2)),
        fill="tozeroy", fillcolor="rgba(52,211,153,.10)",
        hovertemplate="%{x}<br>" + theme.symbol(currency)
                      + "%{y:,.2f}<extra></extra>",
    ))
    fig.add_hline(
        y=original, line_dash="dot", line_color=theme.FAINT,
        annotation_text="Original paper money",
        annotation_font=dict(size=11, color=theme.MUTED),
    )
    theme.style_figure(fig, height=290, y_title=currency)
    return fig


def _change_chart(history, currency: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Trading P&L",
        x=[r["date_utc"] for r in history],
        y=[r["trading_pnl"] for r in history],
        marker_color=theme.UP,
        hovertemplate="%{x}<br>Trading: " + theme.symbol(currency)
                      + "%{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Interest & other",
        x=[r["date_utc"] for r in history],
        y=[r["interest_pnl"] for r in history],
        marker_color=theme.CASH,
        hovertemplate="%{x}<br>Interest: " + theme.symbol(currency)
                      + "%{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(barmode="stack")
    theme.style_figure(fig, height=290, y_title=currency, legend=True)
    return fig


def _explanation(pnl: PnlBreakdown, currency: str) -> None:
    """The sentence that resolves the 'P&L with no trades' confusion."""
    if pnl.has_trading_activity:
        theme.callout(
            f"Of the <b>{theme.money(pnl.total_change, currency, signed=True)}</b> "
            f"total change, <b>{theme.money(pnl.trading_pnl, currency, signed=True)}</b> "
            "comes from trading (realized + unrealized P&L) and "
            f"<b>{theme.money(pnl.interest_and_other, currency, signed=True)}</b> "
            "from interest, fees and FX on idle cash.",
            tone="info", caption="Why the account moved",
        )
        return
    detail = (
        f"No bracket order has been sent yet (<b>{pnl.trades_count} trades</b> in "
        "the journal), and IBKR reports realized P&L "
        f"<b>{theme.money(pnl.realized_pnl, currency)}</b> / unrealized P&L "
        f"<b>{theme.money(pnl.unrealized_pnl, currency)}</b>. "
        f"The <b>{theme.money(pnl.total_change, currency, signed=True)}</b> is "
        "<b>interest accruing on idle paper cash</b>, not a trading gain. "
        f"Accrued interest currently stands at "
        f"{theme.money(pnl.accrued_cash, currency)}."
    )
    theme.callout(detail, tone="warn" if pnl.total_change else "info",
                  caption="Why there is a change with no trades")


def _orders_table(orders, currency: str) -> None:
    if not orders:
        theme.empty(
            "No bracket orders yet",
            "Entries appear here once a strategy has price data, passes the "
            "impact screen and produces a long BUY. Check Data & Universe to "
            "see what is still missing.",
        )
        return
    df = pd.DataFrame(orders)
    table = pd.DataFrame({
        "When (UTC)": df["timestamp_utc"].str[:19].str.replace("T", " ", regex=False),
        "Symbol": df["symbol"],
        "Side": df["side"],
        "Qty": df["quantity"],
        "Entry id": df["entry_order_id"],
        "Reference": df["reference_price"].map(
            lambda v: theme.money(v, currency)),
        "Take profit": df["take_profit_price"].map(
            lambda v: theme.money(v, currency)),
        "Stop loss": df["stop_loss_price"].map(
            lambda v: theme.money(v, currency)),
        "Status": df["status"].str.title(),
        "Strategy / note": df["note"],
    })
    st.dataframe(table, width="stretch", hide_index=True)


def _nudge(state, summary) -> None:
    """One next step, only when the lab cannot trade yet."""
    if state.algo_running:
        return
    instruments = int(summary.get("instruments") or 0)
    ready = int(summary.get("ready") or 0)
    if instruments and ready:
        return
    if instruments == 0:
        text = (
            "The universe is empty, so there is nothing to price or trade. "
            "Add a ticker, then sync it from IBKR."
        )
    else:
        text = (
            f"<b>{ready} of {instruments}</b> instruments have enough fresh "
            "daily bars. Sync from IBKR before a strategy can run."
        )
    copy, action = st.columns([4.4, 1.2], vertical_alignment="center")
    with copy:
        theme.callout(text, tone="warn", caption="Next")
    with action:
        if st.button("Open Data", width="stretch", key="overview_open_data"):
            theme.go("Data & Universe")


def render(store=None) -> None:
    theme.page_header(
        "Portfolio",
        "Overview",
        "Original paper money, where it sits, and whether the change came "
        "from trading or from interest on idle cash.",
    )

    state = sync_status(get_state())
    journal = get_journal()
    baseline = journal.baseline()
    pnl: Optional[PnlBreakdown] = journal.pnl_breakdown(
        state.live_snapshot if state.live_snapshot is not None else None
    )
    # A live snapshot may carry a corrected base currency before the legacy
    # baseline row has been migrated, so the breakdown owns the display unit.
    currency = pnl.currency if pnl else (baseline.currency if baseline else "EUR")

    # --- daily check-in ------------------------------------------------------
    action, note = st.columns([1.15, 3.6], vertical_alignment="center")
    with action:
        if st.button("Read account", disabled=state.algo_running,
                     type="primary", width="stretch", key="read_account"):
            with st.spinner("Connecting → reading account → disconnecting…"):
                refresh_snapshot()
            st.rerun()
    with note:
        if state.refresh_error:
            st.error(state.refresh_error)
        elif state.algo_running:
            st.caption("The running algo refreshes the journal every cycle.")
        elif state.snapshot_at:
            st.caption(f"Last API read: {state.snapshot_at[:19].replace('T', ' ')} UTC")
        elif baseline:
            st.caption(
                f"Original paper money recorded {baseline.recorded_at_utc[:10]} "
                f"from account {baseline.account_id or '—'}. "
                "Read account pulls a fresh snapshot."
            )

    if not baseline or pnl is None:
        theme.empty(
            "No paper money recorded yet",
            "Read the account, or start the algo. Both take the figure from "
            "IBKR and keep it.",
        )
        return

    try:
        _nudge(state, get_market().summary())
    except Exception:  # noqa: BLE001 - a hint must not blank the portfolio
        pass

    # --- headline numbers ----------------------------------------------------
    theme.card_row([
        theme.card("Net liquidation", theme.money(pnl.current, currency),
                   f"Original {theme.money(pnl.original, currency)}"),
        theme.card("Total change", theme.money(pnl.total_change, currency, signed=True),
                   f"{theme.pct(pnl.change_pct)} since the beginning",
                   tone=theme.tone_of(pnl.total_change)),
        theme.card("Trading P&L", theme.money(pnl.trading_pnl, currency, signed=True),
                   f"Realized {theme.money(pnl.realized_pnl, currency)} · "
                   f"Unrealized {theme.money(pnl.unrealized_pnl, currency)}",
                   tone=theme.tone_of(pnl.trading_pnl)),
        theme.card("Interest & other",
                   theme.money(pnl.interest_and_other, currency, signed=True),
                   f"Accrued cash {theme.money(pnl.accrued_cash, currency)}",
                   tone=theme.tone_of(pnl.interest_and_other)),
    ])

    _explanation(pnl, currency)

    # --- allocation ----------------------------------------------------------
    theme.section("Where the paper money is", "Source: IBKR account values")
    split = (allocation_of(state.live_snapshot)
             if state.live_snapshot is not None else journal.split())
    left, right = st.columns([3, 2])
    with left:
        if split is None or split.total <= 0:
            theme.empty("No account values", "Read the account from IBKR to see the split.")
        else:
            st.plotly_chart(_donut(split.cash, split.equity, currency),
                            width="stretch", config={"displayModeBar": False})
    with right:
        if split:
            rows = [
                ("Idle cash", theme.money(split.cash, currency),
                 theme.pct(split.cash_pct, signed=False), theme.CASH),
                ("Long equity", theme.money(split.equity, currency),
                 theme.pct(split.equity_pct, signed=False), theme.EQUITY),
            ]
            st.markdown(
                "<div class='pit-card' style='min-height:0'>"
                "<div class='label'>Allocation</div>"
                + "".join(
                    f"<div style='display:flex;align-items:center;gap:10px;"
                    f"padding:9px 0;border-bottom:1px solid {theme.BORDER}'>"
                    f"<span style='width:9px;height:9px;border-radius:3px;"
                    f"background:{color}'></span>"
                    f"<span style='font-size:13px;color:{theme.INK};flex:1'>{name}</span>"
                    f"<span style='font-size:13px;font-weight:650;color:{theme.INK}'>{value}</span>"
                    f"<span style='font-size:12px;color:{theme.FAINT};width:52px;"
                    f"text-align:right'>{share}</span></div>"
                    for name, value, share, color in rows
                )
                + f"<div style='display:flex;justify-content:space-between;padding-top:12px'>"
                  f"<span style='font-size:12px;color:{theme.MUTED}'>Total (Net Liq)</span>"
                  f"<b style='font-size:14px;color:{theme.INK}'>"
                  f"{theme.money(split.total, currency)}</b></div></div>",
                unsafe_allow_html=True,
            )
            st.caption(
                "Equity = GrossPositionValue. Cash = NetLiquidation − equity, "
                "which includes accrued interest. Long only, no leverage."
            )

    # --- evolution -----------------------------------------------------------
    theme.section("Daily evolution", "One upserted journal row per UTC day")
    history = journal.daily_history()
    if not history:
        theme.empty("No daily rows yet", "They are written on every read and every trading cycle.")
    else:
        tab_value, tab_change = st.tabs(["Account value", "What moved it"])
        with tab_value:
            st.plotly_chart(_value_chart(history, pnl.original, currency),
                            width="stretch", config={"displayModeBar": False})
        with tab_change:
            st.plotly_chart(_change_chart(history, currency),
                            width="stretch", config={"displayModeBar": False})
        if len(history) == 1:
            st.caption("One day on record — the curve grows each day you use the lab.")

    # --- activity ------------------------------------------------------------
    theme.section(
        "Latest brackets",
        f"BUY + take profit +{config.TAKE_PROFIT_PCT:.0%} / "
        f"stop loss −{config.STOP_LOSS_PCT:.0%}",
    )
    recent = journal.orders(limit=8)
    _orders_table(recent, currency)
    if recent:
        st.caption("Latest 8. The Journal page keeps the full list.")

    if state.algo_running and state.algo_runner is not None:
        try:
            positions = state.algo_runner.adapter.get_positions()
            if positions:
                theme.section("Open long positions", "Live from IBKR")
                st.dataframe(pd.DataFrame(positions), width="stretch",
                             hide_index=True)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Could not read positions: {exc}")

