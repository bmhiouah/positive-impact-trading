"""Cockpit shell — one fixed IBKR paper session, five focused pages.

Run with:  python scripts/run_cockpit.py

    Overview         where the paper money is, and why its value moved
    Data & Universe  what the IBKR API gave us, and how to extend the universe
    Strategies       which long-only strategies the data can actually support
    Trading          Start / Stop Algo and the live bracket workflow
    Journal          every daily observation and every order sent
"""

from __future__ import annotations

# Ensure an event loop exists before ib_insync is imported (Python 3.14).
from marketmind import ib_compat  # noqa: F401

import streamlit as st

from marketmind import config
from marketmind.cockpit import theme
from marketmind.cockpit.state import get_market, get_state, get_store, sync_status
from marketmind.cockpit.tabs import (
    data_lab, journal, overview, strategies, trading,
)

st.set_page_config(
    page_title="Positive Impact Trading — Paper Lab",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.apply()

PAGES = {
    "Overview": overview.render,
    "Data & Universe": data_lab.render,
    "Strategies": strategies.render,
    "Trading": trading.render,
    "Journal": journal.render,
}

# --- Sidebar ----------------------------------------------------------------

with st.sidebar:
    theme.brand()

    state = sync_status(get_state())
    running = bool(state.algo_running)
    connected = bool(state.ibkr_connected)
    st.markdown(
        "<div class='pit-card pit-status'>"
        "<div class='pit-status-row'><span>Algo</span>"
        f"{theme.pill('Running' if running else 'Stopped', 'ok' if running else 'mute')}</div>"
        "<div class='pit-status-row'><span>IB Gateway</span>"
        f"{theme.pill('Connected' if connected else 'Idle', 'ok' if connected else 'mute')}</div>"
        + (f"<div class='sub'>Paper account {theme.esc(state.ibkr_account)}</div>"
           if state.ibkr_account else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    if state.ibkr_last_error or state.algo_last_error:
        st.caption(state.ibkr_last_error or state.algo_last_error)

    if theme.NAV_KEY not in st.session_state:
        st.session_state[theme.NAV_KEY] = "Overview"
    st.markdown("<div class='pit-nav-label'>Pages</div>", unsafe_allow_html=True)
    for name in PAGES:
        selected = st.session_state[theme.NAV_KEY] == name
        if st.button(
            name,
            key=f"nav-{name}",
            type="primary" if selected else "secondary",
            width="stretch",
        ):
            theme.go(name)

    try:
        summary = get_market().summary()
        st.markdown(
            "<div class='pit-side-stats'>"
            f"<div><b>{summary['instruments']}</b><span>instruments</span></div>"
            f"<div><b>{summary['ready']}</b><span>data-ready</span></div>"
            f"<div><b>{summary['bars']:,}</b><span>daily bars</span></div>"
            f"<div><b>{summary['with_quote']}</b><span>quotes</span></div>"
            "</div>",
            unsafe_allow_html=True,
        )
    except Exception:  # noqa: BLE001 - the sidebar must never break the app
        st.caption("Data layer initializing…")

    theme.footer(
        f"Paper session {config.IBKR_HOST}:{config.IBKR_PORT} · client {config.IBKR_CLIENT_ID}",
        "Long only · paper · TP/SL",
    )

# --- Page -------------------------------------------------------------------

try:
    PAGES[st.session_state[theme.NAV_KEY]](get_store())
except Exception as exc:  # noqa: BLE001 - show a usable error instead of a blank page
    st.error(f"Page error: {exc}")
    st.exception(exc)
