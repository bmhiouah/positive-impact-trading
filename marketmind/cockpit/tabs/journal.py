"""Journal — the audit trail for the paper lab.

One immutable baseline, one upserted row per UTC day, one row per bracket.
The raw browser is kept for inspection only; it never writes anything.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from marketmind import config
from marketmind.cockpit import theme
from marketmind.cockpit.state import get_journal


TABLES = (
    "paper_baseline", "paper_daily", "paper_orders", "instruments",
    "price_bars", "market_quotes", "contracts", "fundamental_data",
    "data_updates", "positions", "orders", "executions",
)


def _daily_frame(rows, currency: str) -> pd.DataFrame:
    records = []
    for row in rows:
        records.append({
            "Day (UTC)": row["date_utc"],
            "Net liquidation": theme.money(row["net_liquidation"], currency),
            "Cash": theme.money(row["cash"], currency),
            "Long equity": theme.money(row["equity"], currency),
            "Total change": theme.money(row["pnl_vs_original"], currency, signed=True),
            "Trading P&L": theme.money(row["trading_pnl"], currency, signed=True),
            "Interest & other": theme.money(row["interest_pnl"], currency, signed=True),
            "Realized": theme.money(row["realized_pnl"], currency),
            "Unrealized": theme.money(row["unrealized_pnl"], currency),
            "Accrued cash": theme.money(row["accrued_cash"], currency),
            "Orders": row["orders_count"],
            "Updated": row["updated_at_utc"][:19].replace("T", " "),
        })
    return pd.DataFrame(records)


def _price(value, currency: str) -> str:
    return theme.money_or_dash(value, currency)


def render(store=None) -> None:
    theme.page_header(
        "Records",
        "Journal",
        "Original paper money, one row per UTC day, and every bracket sent "
        "or blocked.",
    )

    journal = get_journal()
    baseline = journal.baseline()
    currency = baseline.currency if baseline else "EUR"
    daily = journal.daily_history()
    orders = journal.orders(limit=250)

    theme.card_row([
        theme.card("Original paper money",
                   theme.money(baseline.original_capital, currency)
                   if baseline else "—",
                   (baseline.recorded_at_utc[:10] + " · " +
                    (baseline.account_id or "")) if baseline else "Not recorded"),
        theme.card("Daily observations", f"{len(daily)}",
                   "one upserted row per UTC day"),
        theme.card("Bracket orders", f"{len(orders)}",
                   "submissions, blocks and errors"),
        theme.card("Last observation",
                   daily[-1]["date_utc"] if daily else "—",
                   "UTC day"),
    ])
    st.caption(f"Stored in `{config.DB_PATH}`.")

    tabs = st.tabs(["Daily journal", "Orders", "Raw data"])

    with tabs[0]:
        theme.section("Daily evolution", "Trading P&L is never mixed with interest")
        if daily:
            frame = _daily_frame(daily, currency)
            st.dataframe(frame, width="stretch", hide_index=True)
            st.download_button(
                "Download daily journal (CSV)",
                data=pd.DataFrame(daily).to_csv(index=False).encode("utf-8"),
                file_name="paper_daily.csv", mime="text/csv",
            )
        else:
            theme.empty("No daily rows yet",
                        "Read the account from IBKR or start the algo.")

    with tabs[1]:
        theme.section("Every order sent", "Three legs per entry, always")
        if orders:
            st.dataframe(pd.DataFrame([{
                "When (UTC)": o["timestamp_utc"][:19].replace("T", " "),
                "Symbol": o["symbol"],
                "Side": o["side"],
                "Qty": o["quantity"],
                "Entry id": o["entry_order_id"],
                "Entry": o["entry_type"],
                "Reference": _price(o["reference_price"], currency),
                "TP id": o["take_profit_order_id"],
                "Take profit": _price(o["take_profit_price"], currency),
                "SL id": o["stop_loss_order_id"],
                "Stop loss": _price(o["stop_loss_price"], currency),
                "Status": str(o["status"] or "").title(),
                "Note": o["note"],
            } for o in orders]), width="stretch", hide_index=True)
            st.caption(
                "A blocked row means the impact screen refused the entry "
                "(usually a missing debt ratio). An error row means IBKR "
                "rejected or could not price the bracket."
            )
        else:
            theme.empty("No orders yet",
                        "Nothing is invented: the table stays empty until a "
                        "bracket is really submitted.")

    with tabs[2]:
        theme.section("Raw data browser", "Read-only inspection of the SQLite store")
        c1, c2 = st.columns([3, 1])
        table = c1.selectbox("Table", TABLES, key="journal_browser_table")
        limit = c2.slider("Rows", 10, 500, 50, key="journal_browser_limit")
        try:
            cursor = store.conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
            columns = [d[0] for d in cursor.description]
            if rows:
                st.dataframe(pd.DataFrame(rows, columns=columns),
                             width="stretch", hide_index=True)
                st.caption(f"{len(rows)} rows from `{table}` (newest first)")
            else:
                theme.empty(f"`{table}` is empty", "Nothing stored for this table yet.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read `{table}`: {exc}")
