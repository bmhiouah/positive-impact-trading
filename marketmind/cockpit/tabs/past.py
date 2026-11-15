"""Journal — how the capital moved, and every bracket the lab sent."""

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


_OUTCOME = {
    "open": "Open",
    "take_profit": "Take profit",
    "stop_loss": "Stop",
    "momentum": "Trend ended",
}


def render(store=None) -> None:
    theme.heading(
        "📒 Journal",
        "How the paper capital moved, and every order that was sent.",
    )

    journal = get_journal()
    baseline = journal.baseline()
    pnl = journal.pnl_breakdown(None)
    currency = pnl.currency if pnl else (baseline.currency if baseline else "EUR")
    orders = journal.trades(limit=500)
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

    theme.section("📈  How the capital moved", "One row per UTC day")
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

    theme.section("🧾  Orders by stock", "Open, stopped, or the trend ended")
    theme.callout(
        "<b>Open</b> means the position is still on, with its stop at IBKR. "
        "<b>Stop</b> means that stop filled. <b>Trend ended</b> means the "
        "12-month reading turned flat and the shares were sold. The result is "
        "the difference from the entry, before commission.",
    )
    if not orders:
        theme.empty(
            "No order sent yet",
            "The table stays empty until a bracket is really sent.",
        )
    else:
        grouped: dict[str, dict[str, float]] = defaultdict(
            lambda: {"Open": 0, "Take profit": 0, "Stop": 0, "Trend ended": 0,
                     "Result": 0.0, "Closed": 0}
        )
        for order in orders:
            bucket = grouped[order["symbol"]]
            label = _OUTCOME.get(order.get("outcome") or "open", "Open")
            bucket[label] += 1
            if order.get("result_pnl") is not None:
                bucket["Result"] += float(order["result_pnl"])
                bucket["Closed"] += 1
        st.dataframe(pd.DataFrame([
            {
                "Symbol": symbol,
                "Open": int(counts["Open"]),
                "Stop": int(counts["Stop"]),
                "Trend ended": int(counts["Trend ended"]),
                "Result": (theme.money(counts["Result"], currency, signed=True)
                           if counts["Closed"] else "—"),
            }
            for symbol, counts in sorted(grouped.items())
        ]), width="stretch", hide_index=True)

        st.dataframe(pd.DataFrame([{
            "When (UTC)": order["timestamp_utc"][:19].replace("T", " "),
            "Symbol": order["symbol"],
            "Where": _OUTCOME.get(order.get("outcome") or "open", "Open"),
            "Entry": theme.money(
                order["entry_price"] if order.get("entry_price")
                else order["reference_price"],
                currency,
            ),
            "Exit": theme.money_or_dash(order.get("exit_price"), currency),
            "Result": (theme.money(order["result_pnl"], currency, signed=True)
                       if order.get("result_pnl") is not None else "—"),
        } for order in orders]), width="stretch", hide_index=True)

    theme.section("⏪  Replay a rule", "What a strategy would have said on past bars")
    render_replay(store)

    theme.section("📅  Day by day", "Trading is kept apart from interest")
    if history:
        st.dataframe(_daily_frame(history, currency), width="stretch", hide_index=True)
        st.download_button(
            "Download daily journal",
            data=pd.DataFrame(history).to_csv(index=False).encode("utf-8"),
            file_name="paper_daily.csv",
            mime="text/csv",
        )
    else:
        theme.empty("No daily rows yet", "Read the account on Trading, or start the algo.")

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
