"""Trading — Start / Stop Algo, plus a real explanation of what each cycle did.

No connection settings on purpose: one fixed IB Gateway paper session, capital
read from the account via the API, one momentum rule, a trailing stop.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from marketmind import config
from marketmind.cockpit import theme
from marketmind.cockpit.state import (
    get_journal, get_market, get_state, start_algo, stop_algo, sync_status,
)


def _diagnosis(state, journal, coverage) -> None:
    """Explain zero trades instead of silently showing an empty table."""
    result = state.algo_last_result or {}
    if not state.algo_running or result.get("submitted", 0) > 0:
        return
    missing_debt = sum(1 for row in coverage if not row.get("fundamentals"))
    stale = sum(1 for row in coverage if row["status"] != "ready")
    parts = []
    if not coverage:
        parts.append("the universe has no instruments")
    if stale:
        parts.append(f"<b>{stale}/{len(coverage)}</b> instruments lack enough "
                     "fresh daily bars — sync them on Strategies")
    if result.get("strategies_ready", 0) == 0 or result.get("waiting") == "history":
        parts.append("the live rule needs a year of daily bars before it can "
                     "say buy — sync on Strategies so the history can lengthen")
    elif result.get("decided") is False:
        return
    elif result.get("signals", 0) == 0:
        parts.append("the live rule did not say buy on the last completed session")
    if missing_debt and result.get("signals", 0) > 0:
        parts.append(f"<b>{missing_debt}</b> instruments have no debt ratio — "
                     "the impact screen blocks those equities. Enter the ratio "
                     "on Strategies")
    blocked = int(result.get("blocked") or 0)
    if blocked:
        parts.append(f"<b>{blocked}</b> ideas were refused by the impact screen "
                     "this cycle and were not sent")
    if parts:
        theme.callout(
            "Nothing was sent this cycle because " + "; ".join(parts) + ".",
            tone="warn", caption="Why there is no trade yet",
        )


def render(store=None) -> None:
    theme.page_header(
        "Execution",
        "Trading",
        "Start opens the paper session, reads capital from the account, and "
        "only then runs the loop. Each entry is a buy with a stop that "
        "trails the stock's own daily range. There is no fixed take-profit.",
    )

    state = sync_status(get_state())
    journal = get_journal()
    coverage = get_market().coverage()
    result = state.algo_last_result or {}

    # --- control panel -------------------------------------------------------
    control, status = st.columns([2, 3])
    with control:
        b1, b2 = st.columns(2)
        if b1.button("Start Algo", disabled=state.algo_running,
                     type="primary", width="stretch", key="start_algo"):
            with st.spinner("Connect → read capital → start trading…"):
                start_algo()
            st.rerun()
        if b2.button("Stop Algo", disabled=not state.algo_running,
                     width="stretch", key="stop_algo"):
            stop_algo()
            st.rerun()
        st.caption(
            "Stopping the algo leaves the stop resting at IBKR, so an open "
            "position stays protected."
        )
    with status:
        pills = [
            theme.pill("Algo running" if state.algo_running else "Algo stopped",
                       "ok" if state.algo_running else "mute"),
            theme.pill("Gateway connected" if state.ibkr_connected
                       else "Gateway idle",
                       "ok" if state.ibkr_connected else "mute"),
        ]
        if state.ibkr_account:
            pills.append(theme.pill(f"Account {state.ibkr_account}", "info"))
        theme.pills(pills)
        if state.ibkr_last_error:
            st.error(state.ibkr_last_error)
        if state.algo_last_error:
            st.warning(f"Last cycle error: {state.algo_last_error}")

    theme.card_row([
        theme.card("Cycles completed", f"{state.cycle_count}",
                   (state.last_cycle_at or "—")[:19].replace("T", " ")),
        theme.card("Universe", f"{result.get('universe', len(coverage))}",
                   "active tickers evaluated"),
        theme.card("Ready strategies", f"{result.get('strategies_ready', '—')}",
                   "given the stored data"),
        theme.card("Brackets submitted", f"{result.get('submitted', 0)}",
                   "last cycle", tone="blue" if result.get("submitted") else "default"),
    ])

    if state.algo_running:
        theme.callout(
            f"Every {config.POLL_INTERVAL:.0f}s the account is read and fills "
            "are checked. Once per session, on the last completed day: "
            "12-month momentum, then the impact screen, then a buy sized so "
            f"the stop risks <b>{config.RISK_FRACTION:.1%}</b> of the account. "
            f"The stop is <b>{config.ATR_MULTIPLE:.0f}×</b> the 20-day range "
            "and only moves up.",
            tone="info", caption="Current cycle",
        )
    _diagnosis(state, journal, coverage)

    # --- open orders / positions --------------------------------------------
    if state.algo_running and state.algo_runner is not None:
        theme.section("Live at IBKR", "Read through the open session")
        try:
            orders = state.algo_runner.adapter.get_open_orders()
            positions = state.algo_runner.adapter.get_positions()
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Could not read live orders: {exc}")
        else:
            left, right = st.columns(2)
            with left:
                st.markdown("**Resting orders**")
                if orders:
                    st.dataframe(pd.DataFrame(orders), width="stretch",
                                 hide_index=True)
                else:
                    theme.empty("No resting orders", "Brackets appear here once sent.")
            with right:
                st.markdown("**Open long positions**")
                if positions:
                    st.dataframe(pd.DataFrame(positions), width="stretch",
                                 hide_index=True)
                else:
                    theme.empty("Flat", "No open long positions.")
    else:
        theme.section("Last bracket orders", "From the journal")
        orders = journal.orders(limit=10)
        baseline = journal.baseline()
        currency = baseline.currency if baseline else "EUR"
        if orders:
            st.dataframe(pd.DataFrame([{
                "When (UTC)": o["timestamp_utc"][:19].replace("T", " "),
                "Symbol": o["symbol"], "Status": str(o["status"] or "").title(),
                "Reference": theme.money_or_dash(o["reference_price"], currency),
                "Stop": theme.money_or_dash(o["stop_loss_price"], currency),
            } for o in orders]), width="stretch", hide_index=True)
        else:
            theme.empty("No orders sent yet",
                        "The journal records submissions, compliance blocks and errors.")

    with st.expander("How this session works"):
        st.markdown(
            f"Gateway **{config.IBKR_HOST}:{config.IBKR_PORT}** (paper), "
            f"client id **{config.IBKR_CLIENT_ID}**. Market data is delayed "
            f"(type {config.MARKET_DATA_TYPE}). Capital is read from "
            "NetLiquidation, never typed in. Start tears down any previous "
            "run, connects, writes the journal, then starts the trading "
            "thread. Each entry is a parent BUY plus a GTC stop. The stop "
            "trails up. Stopping the algo leaves that stop resting at IBKR."
        )
