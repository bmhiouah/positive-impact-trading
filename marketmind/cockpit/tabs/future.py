"""Future — strategies that might be used, and the data they still need."""

from __future__ import annotations

import streamlit as st

from marketmind.cockpit import theme
from marketmind.cockpit.state import get_market, get_state, sync_status
from marketmind.cockpit.tabs import data_lab, strategies


def render(store=None) -> None:
    theme.heading(
        "Future",
        "Strategies you might run later, and the data each one still needs.",
    )
    strategies.render_ideas(store)

    theme.section("Data they need", "One sync, then the tickers and the debt ratios")
    state = sync_status(get_state())
    data_lab._render_sync(state)

    market = get_market()
    coverage = market.coverage()
    if coverage:
        st.dataframe(data_lab._coverage_table(coverage), width="stretch",
                     hide_index=True)
        st.caption("Ready means enough fresh daily bars for a signal.")
    else:
        theme.empty("No tickers yet", "Add one below, then sync it from IBKR.")

    data_lab._render_universe(market)

    with st.expander("Debt ratios and sectors"):
        data_lab._render_compliance(market, store, coverage)
    with st.expander("Price history"):
        data_lab._render_price_explorer(market, coverage)
