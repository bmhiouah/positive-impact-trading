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
    "🏠  Overview": overview.render,
    "🗂  Data & Universe": data_lab.render,
    "🧠  Strategies": strategies.render,
    "🤖  Trading": trading.render,
    "📒  Journal": journal.render,
}

# --- Sidebar ----------------------------------------------------------------

with st.sidebar:
    theme.brand()
    st.markdown("<hr class='pit'/>", unsafe_allow_html=True)

    state = sync_status(get_state())
    running = bool(state.algo_running)
    connected = bool(state.ibkr_connected)

    st.markdown(
        "<div class='pit-card' style='min-height:0;padding:13px 15px;margin-bottom:12px'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;"
        f"margin-bottom:9px'><span style='font-size:12px;color:{theme.MUTED}'>Algo</span>"
        f"{theme.pill('Running' if running else 'Stopped', 'ok' if running else 'mute')}</div>"
        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
        f"<span style='font-size:12px;color:{theme.MUTED}'>IB Gateway</span>"
        f"{theme.pill('Connected' if connected else 'Idle', 'ok' if connected else 'mute')}</div>"
        + (f"<div class='sub'>Paper account {theme.esc(state.ibkr_account)}</div>"
           if state.ibkr_account else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    if state.ibkr_last_error or state.algo_last_error:
        st.caption(state.ibkr_last_error or state.algo_last_error)

    selection = st.radio(
        "Navigate", list(PAGES.keys()), index=0,
        label_visibility="collapsed",
    )

    st.markdown("<hr class='pit'/>", unsafe_allow_html=True)
    try:
        summary = get_market().summary()
        st.markdown(
            "<div class='pit-kv'>"
            f"<b>{summary['instruments']}</b> instruments &nbsp;·&nbsp; "
            f"<b>{summary['ready']}</b> data-ready<br/>"
            f"<b>{summary['bars']:,}</b> daily bars &nbsp;·&nbsp; "
            f"<b>{summary['with_quote']}</b> quotes"
            "</div>",
            unsafe_allow_html=True,
        )
    except Exception:  # noqa: BLE001 - the sidebar must never break the app
        st.caption("Data layer initializing…")

    st.markdown("<hr class='pit'/>", unsafe_allow_html=True)
    theme.footer(
        f"Paper session {config.IBKR_HOST}:{config.IBKR_PORT} · client {config.IBKR_CLIENT_ID}",
        "Long only · no leverage · TP/SL on every entry",
        f"Journal {config.DB_PATH}",
    )

# --- Page -------------------------------------------------------------------

try:
    PAGES[selection](get_store())
except Exception as exc:  # noqa: BLE001 - show a usable error instead of a blank page
    st.error(f"Page error: {exc}")
    st.exception(exc)
