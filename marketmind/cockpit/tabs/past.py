"""Past — how the capital evolved, and every bracket the lab sent."""

from __future__ import annotations

from collections import defaultdict

import pandas as pd
import streamlit as st

from marketmind import config
from marketmind.cockpit import theme
from marketmind.cockpit.state import get_journal
from marketmind.cockpit.tabs.journal import TABLES, _daily_frame
from marketmind.cockpit.tabs.overview import _change_chart, _explanation, _value_chart
from marketmind.cockpit.tabs.strategies import render_replay


def _outcome(status: str) -> str:
    text = (status or "").strip().lower()
    if text == "blocked":
        return "Blocked"
    if text in {"error", "failed", "rejected", "cancelled", "canceled"}:
        return "Failed"
    return "Sent"


def render(store=None) -> None:
    theme.heading(
        "Past",
        "How the paper capital evolved, and what was sent on each stock.",
    )

    journal = get_journal()
    baseline = journal.baseline()
    pnl = journal.pnl_breakdown(None)
    currency = pnl.currency if pnl else (baseline.currency if baseline else "EUR")
    orders = journal.orders(limit=500)
    history = journal.daily_history()

    if not baseline or pnl is None:
        theme.empty(
            "No past yet",
            "The record starts the first time the account is read. Nothing "
            "here is typed in.",
        )
        return

    theme.card_row([
        theme.card("Original", theme.money(pnl.original, currency),
                   baseline.recorded_at_utc[:10]),
        theme.card("Now", theme.money(pnl.current, currency),
                   f"{theme.pct(pnl.change_pct)} from the start",
                   tone=theme.tone_of(pnl.total_change)),
        theme.card("From trading", theme.money(pnl.trading_pnl, currency, signed=True),
                   f"Realized {theme.money(pnl.realized_pnl, currency)} · "
                   f"unrealized {theme.money(pnl.unrealized_pnl, currency)}",
                   tone=theme.tone_of(pnl.trading_pnl)),
        theme.card("From interest",
                   theme.money(pnl.interest_and_other, currency, signed=True),
                   "Idle cash, fees and FX",
                   tone=theme.tone_of(pnl.interest_and_other)),
    ])
    _explanation(pnl, currency)

    theme.section("How the capital moved", "One row per UTC day")
    if not history:
        theme.empty("No daily rows yet", "They are written on every account read and every cycle.")
    else:
        left, right = st.columns(2)
        with left:
            st.plotly_chart(_value_chart(history, pnl.original, currency),
                            width="stretch", config={"displayModeBar": False})
        with right:
            st.plotly_chart(_change_chart(history, currency),
                            width="stretch", config={"displayModeBar": False})

    theme.section("Orders by stock", "Sent, blocked, or failed")
    theme.callout(
        "A <b>sent</b> row left the lab as a bracket. <b>Blocked</b> means the "
        "impact screen refused it. <b>Failed</b> means IBKR rejected it. The "
        "journal does not yet record whether the take-profit or the stop was "
        "the exit, so this page does not invent a profit per stock.",
    )
    if not orders:
        theme.empty("No orders yet", "The table stays empty until a bracket is really sent.")
    else:
        grouped: dict[str, dict[str, int]] = defaultdict(
            lambda: {"Sent": 0, "Blocked": 0, "Failed": 0}
        )
        for order in orders:
            grouped[order["symbol"]][_outcome(order["status"])] += 1
        summary = pd.DataFrame([
            {"Symbol": symbol, **counts, "Total": sum(counts.values())}
            for symbol, counts in sorted(grouped.items())
        ])
        st.dataframe(summary, width="stretch", hide_index=True)

        st.dataframe(pd.DataFrame([{
            "When (UTC)": order["timestamp_utc"][:19].replace("T", " "),
            "Symbol": order["symbol"],
            "Result": _outcome(order["status"]),
            "Reference": theme.money_or_dash(order["reference_price"], currency),
            "Take profit": theme.money_or_dash(order["take_profit_price"], currency),
            "Stop loss": theme.money_or_dash(order["stop_loss_price"], currency),
            "Note": order["note"] or "",
        } for order in orders]), width="stretch", hide_index=True)

    theme.section("Replay a rule", "What a strategy would have said on past bars")
    render_replay(store)

    theme.section("Day by day", "Trading is kept apart from interest")
    if history:
        st.dataframe(_daily_frame(history, currency), width="stretch", hide_index=True)
        st.download_button(
            "Download daily journal",
            data=pd.DataFrame(history).to_csv(index=False).encode("utf-8"),
            file_name="paper_daily.csv",
            mime="text/csv",
        )
    else:
        theme.empty("No daily rows yet", "Read the account on Present, or start the algo.")

    with st.expander("Raw tables"):
        st.caption(f"Read-only. Stored in `{config.DB_PATH}`.")
        table = st.selectbox("Table", TABLES, key="journal_browser_table")
        try:
            cursor = store.conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (50,)
            )
            rows = cursor.fetchall()
            columns = [d[0] for d in cursor.description]
            if rows:
                st.dataframe(pd.DataFrame(rows, columns=columns),
                             width="stretch", hide_index=True)
            else:
                theme.empty(f"{table} is empty", "Nothing stored yet.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read {table}: {exc}")
