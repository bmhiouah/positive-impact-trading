"""Strategies — the long-only catalogue, driven by the data we actually have.

A strategy declares its data requirements. The live rule is 12-month
momentum. The others stay visible so they can be studied. The algo does not
send them. No SELL signals are produced: the exit is the trailing stop.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketmind import config, contest, strategy_catalog
from marketmind.momentum import session_day
from marketmind.cockpit import theme
from marketmind.cockpit.state import get_market


def _spec_card(item: Dict[str, Any]) -> str:
    if item.get("live"):
        state = (theme.pill("Live", "ok") if item["ready"]
                 else theme.pill("Live · needs a year of bars", "warn"))
    else:
        state = (theme.pill("Study", "info") if item["ready"]
                 else theme.pill("Waiting for data", "warn"))
    return (
        "<div class='pit-card' style='min-height:0;margin-bottom:12px'>"
        f"<div style='display:flex;justify-content:space-between;gap:8px;"
        f"align-items:center;margin-bottom:8px'>"
        f"<span style='font-size:14px;font-weight:700;color:{theme.INK}'>"
        f"{theme.esc(item['name'])}</span>{state}</div>"
        f"<div style='font-size:11px;color:{theme.FAINT};letter-spacing:.08em;"
        f"text-transform:uppercase;margin-bottom:7px'>"
        f"{theme.esc(item['family'])}</div>"
        f"<div style='font-size:12px;color:{theme.MUTED};line-height:1.55;"
        f"min-height:36px'>{theme.esc(item['description'])}</div>"
        f"<div class='sub'>{theme.esc(item['reason'])}</div>"
        "</div>"
    )


def _signal_rows(signals: List[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Symbol": s["symbol"],
        "Strategy": s["strategy_name"],
        "Signal": s["action"],
        "Strength": f"{s['strength'] * 100:.0f}%",
        "Price": f"{s['price']:,.2f}",
        "Why": s["rationale"],
    } for s in signals])


def _replay_chart(result: Dict[str, Any]) -> go.Figure:
    prices = result["prices"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[p["date"] for p in prices], y=[p["close"] for p in prices],
        mode="lines", name=result["symbol"],
        line=dict(color=theme.BRAND, width=2),
    ))
    hits = [s for s in result["signals"] if s["price"]]
    fig.add_trace(go.Scatter(
        x=[s["date"] for s in hits], y=[s["price"] for s in hits],
        mode="markers", name="BUY signal",
        marker=dict(symbol="triangle-up", size=11, color=theme.UP,
                    line=dict(color="#FFFFFF", width=1)),
    ))
    theme.style_figure(fig, height=340, legend=True)
    return fig


def _context(store):
    market = get_market()
    coverage = market.coverage()
    symbols = market.universe.symbols()
    availability = strategy_catalog.availability(coverage)
    ready = [item for item in availability if item["ready"]]
    return market, coverage, symbols, availability, ready


def _render_contest(store, symbols) -> contest.Report:
    """Score the grid when the bars have changed, and show the holder."""
    mark = contest.fingerprint(store, symbols) if symbols else "0:"
    saved = contest.load(store)
    if saved is None or saved.fingerprint != mark:
        with st.spinner("Scoring the rules on the stored daily bars…"):
            report = contest.ensure(store, symbols)
    else:
        report = saved
    if st.button("Score the rules again", key="rerun_contest"):
        with st.spinner("Scoring the rules on the stored daily bars…"):
            report = contest.ensure(store, symbols, force=True)
    holder = contest.describe(report.holder)
    theme.section("🏁  The contest", "The live rule is whoever made more money on a later window")
    theme.card_row([
        theme.card("Live rule", "Promoted" if report.promoted else "Incumbent",
                   holder),
        theme.card("Selection ends", report.selection_end or "—",
                   "The favorite is chosen on the days before this"),
        theme.card("Later window", report.confirmation_start or "—",
                   "A favorite is kept only if this window still pays"),
        theme.card("Challengers shown", f"{len(report.rows)}",
                   "Top of the grid, by money in the selection window"),
    ])
    theme.callout(report.reason or "The contest has not run yet.",
                  tone="ok" if report.promoted else "info")
    if report.rows:
        st.dataframe(pd.DataFrame([{
            "Rule": contest.describe(row.rule),
            "Selection R": f"{row.selection_r:.2f}",
            "Selection trades": row.selection_trades,
            "Selection drawdown": f"{row.selection_dd:.2f}",
            "Later R": f"{row.confirmation_r:.2f}",
            "Later trades": row.confirmation_trades,
            "Later drawdown": f"{row.confirmation_dd:.2f}",
            "Sending": "Yes" if row.rule == report.holder else "",
        } for row in report.rows]), width="stretch", hide_index=True)
        st.caption(
            "R is the result divided by the initial stop distance, after "
            "five basis points of cost each way. A bar that touches the stop "
            "and the target counts as the stop."
        )
    return report


def render_ideas(store=None) -> None:
    """Catalogue and what the rules would buy on the data we have now."""
    market, coverage, symbols, availability, ready = _context(store)
    report = _render_contest(store, symbols)
    buys = []
    for symbol in symbols:
        book = contest.load_book(store, symbol)
        if book is None:
            continue
        reading = contest.read_symbol(book, report.holder, session_day())
        if reading is not None and reading.buy:
            buys.append(reading)

    theme.card_row([
        theme.card("Ready to study", f"{len(ready)} / {len(availability)}",
                   "The live rule is chosen by the contest above"),
        theme.card("Instruments ready",
                   f"{sum(1 for r in coverage if r['status'] == 'ready')}",
                   f"of {len(symbols)} active tickers"),
        theme.card("Live buys", f"{len(buys)}",
                   "Last completed day, for the rule being sent"),
        theme.card("Risk per trade", f"{config.RISK_FRACTION:.1%}",
                   "Of the account, measured to the stop"),
    ])

    theme.section("📚  Catalogue", "Each card says whether the stored data can support it")
    cols = st.columns(2)
    for index, item in enumerate(availability):
        with cols[index % 2]:
            st.markdown(_spec_card(item), unsafe_allow_html=True)

    theme.section("🎯  What they would buy now")
    choices = ["All ready strategies"] + [i["name"] for i in availability]
    selected = st.selectbox("Strategy", choices, key="signal_strategy")
    if selected == "All ready strategies":
        keys = [i["key"] for i in ready]
    else:
        keys = [next(i["key"] for i in availability if i["name"] == selected)]
    signals: List[Dict[str, Any]] = []
    for key in keys:
        signals.extend(strategy_catalog.run(key, symbols, store))
    if not signals:
        theme.empty(
            "No evaluations yet",
            "The catalogue needs daily bars before it can produce any "
            "signal. Sync the data in the section below.",
        )
    else:
        buys = [s for s in signals if s["action"] == "BUY"]
        st.caption(
            f"{len(signals)} evaluations · {len(buys)} BUY · "
            f"{len(signals) - len(buys)} HOLD. HOLD is not sent."
        )
        st.dataframe(_signal_rows(signals), width="stretch", hide_index=True)

    _render_compare(store, symbols, availability)


def render_replay(store=None) -> None:
    """Walk one rule over past bars. This is study, not a forecast."""
    _market, coverage, _symbols, availability, _ready = _context(store)
    replay_keys = strategy_catalog.replayable_keys()
    replayable = [i for i in availability if i["key"] in replay_keys]
    with_bars = [r["symbol"] for r in coverage if r["bars"] > 0]
    if not with_bars or not replayable:
        theme.empty(
            "Replay needs price history",
            "Sync at least one instrument on Strategies first.",
        )
        return
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    key = c1.selectbox(
        "Rule", [i["key"] for i in replayable],
        format_func=strategy_catalog.spec_name,
        key="replay_key",
    )
    symbol = c2.selectbox("Symbol", with_bars, key="replay_symbol")
    lookback = c3.slider("Days back", 60, 250, 120, 10, key="replay_lookback")
    horizon = c4.slider("Forward days", 1, 20, 5, key="replay_horizon")
    result = strategy_catalog.replay(key, symbol, store, lookback, horizon)
    if not result.get("supported"):
        st.info(result.get("reason"))
        return
    avg = result["avg_return"]
    win = result["win_rate"]
    theme.card_row([
        theme.card("BUY signals", f"{result['count']}",
                   f"over {result['evaluated']} evaluated days"),
        theme.card(f"Avg {horizon}d return",
                   theme.pct(avg * 100) if avg is not None else "—",
                   "after each BUY signal",
                   tone=theme.tone_of(avg)),
        theme.card("Positive forward",
                   theme.pct(win * 100, signed=False) if win is not None else "—",
                   f"{result['measured']} with a full forward window"),
        theme.card("Best / worst",
                   (f"{theme.pct(result['best'] * 100)} / "
                    f"{theme.pct(result['worst'] * 100)}")
                   if result["best"] is not None else "—",
                   "per signal, before costs"),
    ])
    st.plotly_chart(_replay_chart(result), width="stretch",
                    config={"displayModeBar": False})
    if result["signals"]:
        st.dataframe(
            pd.DataFrame(result["signals"]).rename(columns={
                "date": "Date", "price": "Close",
                f"return_{horizon}": f"{horizon}d forward return",
            }), width="stretch", hide_index=True,
        )
    st.caption(
        "A rule replay, not a full backtest. No commissions, size, "
        "take-profit, stop or compliance block."
    )


def _render_compare(store, symbols, availability) -> None:
    theme.section("⚖️  Side by side")
    counts = []
    for item in availability:
        signals_for = strategy_catalog.run(item["key"], symbols, store)
        counts.append({
            "Strategy": item["name"],
            "BUY": sum(1 for s in signals_for if s["action"] == "BUY"),
            "HOLD": sum(1 for s in signals_for if s["action"] == "HOLD"),
            "Ready": "Yes" if item["ready"] else "No",
        })
    df = pd.DataFrame(counts)
    st.dataframe(df, width="stretch", hide_index=True)
    if df["BUY"].sum() or df["HOLD"].sum():
        fig = go.Figure()
        fig.add_trace(go.Bar(name="BUY", x=df["Strategy"], y=df["BUY"],
                             marker_color=theme.UP))
        fig.add_trace(go.Bar(name="HOLD", x=df["Strategy"], y=df["HOLD"],
                             marker_color=theme.CASH))
        fig.update_layout(barmode="stack")
        theme.style_figure(fig, height=320, legend=True)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    theme.callout(
        "When the algo runs it sends the <b>live rule from the contest</b>. "
        "Until a challenger wins the later window, that rule is 12-month "
        "momentum with a trailing stop. The impact screen still has to pass. "
        "The other cards are here to study.",
    )
