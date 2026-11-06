"""Trading tab — start / stop the long-only paper-trading algo.

No settings here on purpose: one fixed IB Gateway paper session, capital read
from the account via the API, and systematic TP/SL brackets on every entry.
"""

from __future__ import annotations

import streamlit as st

from marketmind import config
from marketmind.cockpit.components import status_badge
from marketmind.cockpit.state import (
    get_state,
    start_algo,
    stop_algo,
    sync_status,
)


def render(store=None) -> None:
    st.subheader("Algo Control — Long-Only Paper Trading")

    state = sync_status(get_state())

    col_actions, col_status = st.columns([1, 2])

    with col_actions:
        st.markdown("#### Actions")
        if st.button(
            "▶️ Start Algo",
            disabled=state.algo_running,
            use_container_width=True,
            type="primary",
        ):
            with st.spinner("Connecting to IBKR Gateway…"):
                start_algo()
            st.rerun()

        if st.button(
            "⏹️ Stop Algo",
            disabled=not state.algo_running,
            use_container_width=True,
        ):
            stop_algo()
            st.rerun()

    with col_status:
        st.markdown("#### Status")
        st.markdown(
            f"**Algo:** {status_badge(state.algo_running)} "
            f"{'Running' if state.algo_running else 'Stopped'}"
        )
        st.markdown(
            f"**IBKR:** {status_badge(state.ibkr_connected)} "
            f"{'Connected' if state.ibkr_connected else 'Not Connected'}"
        )
        if state.ibkr_account:
            st.caption(f"Paper account {state.ibkr_account}")
        if state.ibkr_last_error:
            st.error(state.ibkr_last_error)
        if state.algo_last_error:
            st.warning(f"Last cycle error: {state.algo_last_error}")
        if state.algo_last_result:
            r = state.algo_last_result
            st.caption(
                f"Last cycle: {r.get('signals', 0)} signals, "
                f"{r.get('entries', 0)} entries, "
                f"{r.get('approved', 0)} approved, "
                f"{r.get('submitted', 0)} brackets submitted, "
                f"{r.get('skipped', 0)} skipped"
            )
        st.caption(f"Completed cycles: {state.cycle_count}")
        if state.last_cycle_at:
            st.caption(f"Last cycle at: {state.last_cycle_at[:19]} UTC")

    st.markdown("---")
    st.markdown(
        f"**Every entry is a bracket:** market/limit BUY + take-profit "
        f"**+{config.TAKE_PROFIT_PCT:.0%}** and stop-loss "
        f"**-{config.STOP_LOSS_PCT:.0%}**. No naked order is ever sent. "
        f"Exits are handled by those two legs, so stopping the algo leaves "
        f"open positions protected."
    )
    st.info(
        "**How Start Algo works:**\n"
        "1. Any previous session is fully stopped and disconnected.\n"
        "2. A fresh API session is opened on the dedicated event-loop thread.\n"
        "3. The account snapshot is read — that is where the capital comes from.\n"
        "4. The original paper money is recorded once in the journal.\n"
        "5. Only then does the trading thread start (signals → compliance → brackets).\n\n"
        f"Fixed session: `{config.IBKR_HOST}:{config.IBKR_PORT}`, client id "
        f"`{config.IBKR_CLIENT_ID}`. If any step fails the algo stays stopped "
        "and the real IBKR error is shown above."
    )
