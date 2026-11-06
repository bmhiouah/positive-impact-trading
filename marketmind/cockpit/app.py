"""Main cockpit app — paper-trading lab dashboard.

Run with:  streamlit run marketmind/cockpit/app.py

Pages:
  1. 🏠 Overview    — cash vs equity camembert, original paper money, daily P&L, trades
  2. 🤖 Trading     — Start / Stop Algo
  3. 📊 Strategies  — Signal explorer, backtest, comparison
  4. 📈 Analytics   — Market data explorer, fundamentals, data browser
"""

from __future__ import annotations

# Ensure an event loop exists before ib_insync is imported (Python 3.14).
from marketmind import ib_compat  # noqa: F401

import streamlit as st

from marketmind import config
from marketmind.cockpit.state import get_state, get_store, sync_status
from marketmind.cockpit.tabs import trading, strategies, analytics, overview

st.set_page_config(
    page_title="Positive Impact Trading — Paper Lab",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGES = {
    "🏠 Overview": overview.render,
    "🤖 Trading": trading.render,
    "📊 Strategies": strategies.render,
    "📈 Analytics": analytics.render,
}

# --- Sidebar ----------------------------------------------------------------

st.sidebar.title("📈 Paper Trading Lab")
st.sidebar.markdown("---")
st.sidebar.markdown(f"**Journal:** `{config.DB_PATH}`")
st.sidebar.markdown("**Mode:** Paper · long only")
st.sidebar.markdown("---")

store = get_store()
state = sync_status(get_state())

st.sidebar.markdown(
    f"**Algo:** {'🟢 Running' if state.algo_running else '🔴 Stopped'}\n\n"
    f"**IBKR:** {'🟢 Connected' if state.ibkr_connected else '🔴 Not Connected'}"
)
if state.ibkr_connected and state.ibkr_account:
    st.sidebar.caption(f"Paper account {state.ibkr_account}")
if state.ibkr_last_error:
    st.sidebar.caption(state.ibkr_last_error)
st.sidebar.markdown("---")

selection = st.sidebar.radio("Navigate", list(PAGES.keys()), index=0)

# --- Header -----------------------------------------------------------------

st.title("📈 Positive Impact Trading — Paper Lab")
st.markdown(
    "Experiment with long-only algorithmic paper trading. Capital comes from "
    "the IBKR account, and every entry carries a take-profit and a stop-loss."
)

# --- Render selected page ---------------------------------------------------

try:
    PAGES[selection](store)
except Exception as exc:  # pylint: disable=broad-except
    st.error(f"Page error: {exc}")

st.sidebar.markdown("---")
st.sidebar.caption("One Gateway session · one paper account · one journal")
