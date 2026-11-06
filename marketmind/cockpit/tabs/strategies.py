"""Strategies — the long-only catalogue, driven by the data we actually have.

A strategy declares its data requirements. The UI shows which ones are
available, the signal explorer shows what they say right now, and the replay
shows what a rule would have said on past bars. No SELL signals are produced:
exits belong to the TP/SL brackets.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketmind import config, strategy_catalog
from marketmind.cockpit import theme
from marketmind.cockpit.state import get_market


def _spec_card(item: Dict[str, Any]) -> str:
    state = (theme.pill("Ready", "ok") if item["ready"]
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


def render(store=None) -> None:
    theme.page_header(
        "03 / Research",
        "Strategies",
        "A catalogue of long-only ideas, each declaring the data it needs. "
        "Only the strategies your stored IBKR history can support are offered "
        "to the runner — nothing is guessed and nothing is shorted.",
    )

    market = get_market()
    coverage = market.coverage()
    symbols = market.universe.symbols()
    availability = strategy_catalog.availability(coverage)
    ready = [item for item in availability if item["ready"]]
    current = strategy_catalog.run_all_ready(symbols, store, coverage)

    theme.card_row([
        theme.card("Ready strategies", f"{len(ready)} / {len(availability)}",
                   "Others show exactly what data is missing"),
        theme.card("Data-ready instruments",
                   f"{sum(1 for r in coverage if r['status'] == 'ready')}",
                   f"of {len(symbols)} active tickers"),
        theme.card("BUY signals now", f"{len(current)}",
                   "Strongest signal kept per symbol"),
        theme.card("Entry discipline", "3 legs",
                   f"BUY + TP +{config.TAKE_PROFIT_PCT:.0%} / "
                   f"SL −{config.STOP_LOSS_PCT:.0%}"),
    ])

    theme.section("Catalogue", "Availability is computed from live coverage")
    cols = st.columns(2)
    for index, item in enumerate(availability):
        with cols[index % 2]:
            st.markdown(_spec_card(item), unsafe_allow_html=True)

    tabs = st.tabs([
        "🎯  Signal explorer", "⏪  Historical replay", "⚖️  Compare",
    ])

    with tabs[0]:
        choices = ["All ready strategies"] + [i["name"] for i in availability]
        selected = st.selectbox("Strategy", choices, key="signal_strategy")
        if selected == "All ready strategies":
            keys = [i["key"] for i in ready]
        else:
            keys = [next(i["key"] for i in availability
                         if i["name"] == selected)]
        signals: List[Dict[str, Any]] = []
        for key in keys:
            signals.extend(strategy_catalog.run(key, symbols, store))
        if not signals:
            theme.empty(
                "No evaluations yet",
                "The catalogue needs daily bars before it can produce any "
                "signal. Sync IBKR data in Data & Universe first.",
            )
        else:
            buys = [s for s in signals if s["action"] == "BUY"]
            st.caption(
                f"{len(signals)} evaluations · {len(buys)} BUY · "
                f"{len(signals) - len(buys)} HOLD · "
                "HOLD is not sent to the broker."
            )
            st.dataframe(_signal_rows(signals), width="stretch", hide_index=True)
            if not buys:
                st.caption(
                    "No BUY right now. Rules are deliberately strict; the "
                    "runner will re-evaluate every cycle and never force a trade."
                )

    with tabs[1]:
        replay_keys = strategy_catalog.replayable_keys()
        replayable = [i for i in availability if i["key"] in replay_keys]
        with_bars = [r["symbol"] for r in coverage if r["bars"] > 0]
        if not with_bars or not replayable:
            theme.empty("Replay needs price history",
                        "Sync at least one instrument from IBKR first.")
        else:
            c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
            key = c1.selectbox(
                "Rule", [i["key"] for i in replayable],
                format_func=strategy_catalog.spec_name,
                key="replay_key",
            )
            symbol = c2.selectbox("Symbol", with_bars, key="replay_symbol")
            lookback = c3.slider("Days back", 60, 250, 120, 10,
                                 key="replay_lookback")
            horizon = c4.slider("Forward days", 1, 20, 5, key="replay_horizon")
            result = strategy_catalog.replay(key, symbol, store,
                                             lookback, horizon)
            if not result.get("supported"):
                st.info(result.get("reason"))
            else:
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
                               theme.pct(win * 100, signed=False)
                               if win is not None else "—",
                               f"{result['measured']} with full forward window"),
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
                    "Rule replay, not a full backtest: no commissions, "
                    "slippage, position sizing, TP/SL exits or compliance "
                    "blocks. Use it to compare ideas, not to forecast P&L."
                )

    with tabs[2]:
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
            st.plotly_chart(fig, width="stretch",
                            config={"displayModeBar": False})
        theme.callout(
            "On each cycle the runner evaluates every ready strategy, keeps "
            "the <b>strongest BUY per symbol</b>, applies the impact screen, "
            "then sends a three-leg bracket. Blocked attempts are written to "
            "the journal so a missing debt ratio is visible rather than silent.",
            tone="info", caption="From signal to order",
        )
