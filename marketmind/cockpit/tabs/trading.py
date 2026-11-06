"""Trading — Start / Stop Algo, plus a real explanation of what each cycle did.

No connection settings on purpose: one fixed IB Gateway paper session, capital
read from the account via the API, systematic TP/SL brackets on every entry.
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
                     "fresh daily bars — sync IBKR history in Data & Universe")
    if result.get("strategies_ready", 0) == 0:
        parts.append("<b>no strategy is data-ready</b>, so no signal can be "
                     "generated")
    elif result.get("signals", 0) == 0:
        parts.append("all ready strategies evaluated but <b>no BUY rule "
                     "triggered</b> on the current bars")
    if missing_debt and result.get("signals", 0) > 0:
        parts.append(f"<b>{missing_debt}</b> instruments have no debt ratio — "
                     "the impact screen blocks those equities. Enter the ratio "
                     "in Data & Universe → Compliance data")
    blocked = [o for o in journal.orders(limit=50) if o["status"] == "blocked"]
    if blocked:
        parts.append(f"<b>{len(blocked)}</b> recent attempts were blocked by "
                     "the compliance gate (see Journal)")
    if parts:
        theme.callout(
            "Nothing was sent this cycle because " + "; ".join(parts) + ".",
            tone="warn", caption="Why there is no trade yet",
        )


def render(store=None) -> None:
    theme.page_header(
        "04 / Execution",
        "Trading",
        "One button opens the IB Gateway session, reads the capital from the "
        "API, records the journal and only then starts the long-only loop. "
        "Every entry leaves the account protected by a TP/SL bracket.",
    )

    state = sync_status(get_state())
    journal = get_journal()
    coverage = get_market().coverage()
    result = state.algo_last_result or {}

    # --- control panel -------------------------------------------------------
    control, status = st.columns([2, 3])
    with control:
        b1, b2 = st.columns(2)
        if b1.button("▶  Start Algo", disabled=state.algo_running,
                     type="primary", use_container_width=True):
            with st.spinner("Connect → read capital → start trading…"):
                start_algo()
            st.rerun()
        if b2.button("⏹  Stop Algo", disabled=not state.algo_running,
                     use_container_width=True):
            stop_algo()
            st.rerun()
        st.caption(
            "Stopping the algo leaves resting brackets in place at IBKR, so "
            "open positions stay protected."
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
            f"Each {config.POLL_INTERVAL:.0f}s: read account → write journal → "
            "refresh stale data over this same connection → evaluate every "
            "ready strategy → impact screen → send a BUY with take profit "
            f"<b>+{config.TAKE_PROFIT_PCT:.0%}</b> and stop loss "
            f"<b>−{config.STOP_LOSS_PCT:.0%}</b>.",
            tone="info", caption="Current cycle",
        )
    _diagnosis(state, journal, coverage)

    # --- live session detail -------------------------------------------------
    theme.section("Session detail", "Fixed by design — no settings")
    st.markdown(
        "<div class='pit-card' style='min-height:0'>"
        "<div class='label'>Connection & lifecycle</div>"
        "<div class='pit-kv'>"
        f"<b>Gateway</b> {config.IBKR_HOST}:{config.IBKR_PORT} "
        f"(paper) · client id <b>{config.IBKR_CLIENT_ID}</b><br/>"
        f"<b>Market data</b> delayed (type {config.MARKET_DATA_TYPE}) · "
        f"<b>Capital</b> read from NetLiquidation, never a parameter<br/>"
        "<b>Start</b> stop previous run → connect → account snapshot → "
        "journal baseline → start trading thread<br/>"
        "<b>Order</b> parent BUY (transmit=False) + SELL LIMIT take profit + "
        "SELL STOP stop loss (transmit=True)"
        "</div></div>",
        unsafe_allow_html=True,
    )

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
        if orders:
            st.dataframe(pd.DataFrame([{
                "When (UTC)": o["timestamp_utc"][:19].replace("T", " "),
                "Symbol": o["symbol"], "Status": o["status"],
                "Reference": o["reference_price"],
                "Take profit": o["take_profit_price"],
                "Stop loss": o["stop_loss_price"],
            } for o in orders]), width="stretch", hide_index=True)
        else:
            theme.empty("No orders sent yet",
                        "The journal records submissions, compliance blocks and errors.")
