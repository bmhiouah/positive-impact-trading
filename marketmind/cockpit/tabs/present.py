"""Trading — run the algo and watch positions and orders."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from marketmind.cockpit import theme
from marketmind.cockpit.state import (
    get_journal, get_market, get_state, refresh_snapshot, start_algo, stop_algo,
    sync_status,
)
from marketmind.cockpit.tabs.overview import _orders_table
from marketmind.cockpit.tabs.trading import _diagnosis
from marketmind.journal import allocation_of


def render(store=None) -> None:
    theme.heading(
        "📈 Trading",
        "Start or stop the algo, and watch the positions and the orders.",
    )

    state = sync_status(get_state())
    journal = get_journal()
    baseline = journal.baseline()
    pnl = journal.pnl_breakdown(
        state.live_snapshot if state.live_snapshot is not None else None
    )
    currency = pnl.currency if pnl else (baseline.currency if baseline else "EUR")
    split = (allocation_of(state.live_snapshot)
             if state.live_snapshot is not None else journal.split())

    running = state.algo_running
    theme.pills([
        theme.pill("Algo Running" if running else "Algo Stopped",
                   "ok" if running else "mute"),
    ])

    # Clickable action = primary; inactive Start/Stop = greyed disabled.
    start_type = "secondary" if running else "primary"
    stop_type = "primary" if running else "secondary"

    start, stop, read = st.columns(3)
    with start:
        if st.button("▶  Start algo", type=start_type, disabled=running,
                     width="stretch", key="start_algo"):
            with st.spinner("Connect → read capital → start trading…"):
                start_algo()
            st.rerun()
    with stop:
        if st.button("⏹  Stop algo", type=stop_type, disabled=not running,
                     width="stretch", key="stop_algo"):
            stop_algo()
            st.rerun()
    with read:
        if st.button("↻  Read account", disabled=running,
                     width="stretch", key="read_account"):
            with st.spinner("Connecting → reading account → disconnecting…"):
                refresh_snapshot()
            st.rerun()

    if state.ibkr_last_error or state.algo_last_error:
        st.error(state.ibkr_last_error or state.algo_last_error)
    elif state.refresh_error:
        st.error(state.refresh_error)
    elif state.algo_running:
        st.caption("Stopping leaves the stop resting at IBKR.")
    elif state.snapshot_at:
        st.caption(f"Last account read {state.snapshot_at[:19].replace('T', ' ')} UTC.")
    else:
        st.caption("Start runs the loop. Read account only takes a snapshot.")

    if not baseline or pnl is None:
        theme.empty(
            "Nothing to monitor yet",
            "Read the account, or start the algo. The paper money comes from "
            "IBKR, and the original figure is kept.",
        )
        return

    positions = []
    resting = []
    if state.algo_running and state.algo_runner is not None:
        try:
            positions = state.algo_runner.adapter.get_positions() or []
            resting = state.algo_runner.adapter.get_open_orders() or []
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Could not read the live session: {exc}")

    cash = theme.money(split.cash, currency) if split else "—"
    equity = theme.money(split.equity, currency) if split else "—"
    theme.card_row([
        theme.card("Net liquidation", theme.money(pnl.current, currency),
                   f"Original {theme.money(pnl.original, currency)}"),
        theme.card("Cash", cash, "Idle, including accrued interest"),
        theme.card("Long equity", equity, "Open long positions"),
        theme.card("Open positions", f"{len(positions)}" if state.algo_running else "—",
                   "Live while the algo is running"),
    ])

    try:
        summary = get_market().summary()
    except Exception:  # noqa: BLE001
        summary = {"instruments": 0, "ready": 0}
    if not state.algo_running and not summary.get("ready"):
        note, jump = st.columns([4, 1], vertical_alignment="center")
        with note:
            theme.callout(
                "No instrument has fresh history yet, so the algo has nothing "
                "it can honestly trade. Prepare the data on Strategies.",
                tone="warn", caption="Before you start",
            )
        with jump:
            if st.button("🧪  Strategies", width="stretch", key="present_to_future"):
                theme.go("Strategies")

    theme.section("📂  Open positions", "What is held right now")
    if not state.algo_running:
        theme.empty(
            "The algo is stopped",
            "Positions show up here while it is running. Brackets already "
            "sent stay at IBKR after you stop.",
        )
    elif positions:
        st.dataframe(pd.DataFrame(positions), width="stretch", hide_index=True)
    else:
        theme.empty("Flat", "No open long position.")

    theme.section("🧾  Orders", "Sent on this session, newest first")
    coverage = get_market().coverage()
    _diagnosis(state, journal, coverage)
    if state.algo_running:
        if resting:
            st.dataframe(pd.DataFrame(resting), width="stretch", hide_index=True)
        else:
            theme.empty("No resting order", "A stop shows up here once a buy is sent.")
    else:
        recent = journal.trades(limit=12)
        _orders_table(recent, currency)
        if recent:
            st.caption("Latest orders on record. The full list is in the Journal.")
