"""Cockpit shell — three pages, one job each.

    Trading      run the algo and watch positions and orders
    Strategies   rules that might be used, and the data they need
    Journal      how the capital moved, and what was sent
"""

from __future__ import annotations

# Ensure an event loop exists before ib_insync is imported (Python 3.14).
from marketmind import ib_compat  # noqa: F401

import streamlit as st

from marketmind.cockpit import theme
from marketmind.cockpit.state import get_state, get_store, sync_status
from marketmind.cockpit.tabs import future, past, present

st.set_page_config(
    page_title="Positive Impact Trading — Paper Lab",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)
theme.apply()

MODES = {
    "Trading": present.render,
    "Strategies": future.render,
    "Journal": past.render,
}
LABELS = {
    "Trading": "📈  Trading",
    "Strategies": "🧪  Strategies",
    "Journal": "📒  Journal",
}

state = sync_status(get_state())
algo = "🟢 Algo running" if state.algo_running else "⚪ Algo stopped"
gateway = "🔗 Gateway on" if state.ibkr_connected else "🔌 Gateway idle"
account = f" · {theme.esc(state.ibkr_account)}" if state.ibkr_account else ""
st.markdown(
    "<div class='pit-topline'><b>Positive Impact</b>"
    "<span>Paper lab</span>"
    f"<span class='grow'>{theme.esc(algo)} · {theme.esc(gateway)}{account}</span>"
    "</div>",
    unsafe_allow_html=True,
)

mode = st.segmented_control(
    "Section",
    list(MODES),
    default="Trading",
    selection_mode="single",
    required=True,
    key=theme.NAV_KEY,
    label_visibility="collapsed",
    width="stretch",
    format_func=lambda name: LABELS[name],
)

try:
    MODES[mode](get_store())
except Exception as exc:  # noqa: BLE001 - show a usable error instead of a blank page
    st.error(f"Page error: {exc}")
    st.exception(exc)
