"""Light cockpit colours: cool gray page, white cards, one teal accent.

The page stays pale so it can be looked at for a while. Teal is reserved for
the selected page and the main button, so the screen is not gray all over.
Gains are green and losses are red, separate from that teal.
"""

from __future__ import annotations

import html
from typing import Iterable, Optional, Sequence

import plotly.graph_objects as go
import streamlit as st

INK = "#1E293B"
MUTED = "#526071"
FAINT = "#8B9AAB"
BORDER = "#D5E0EA"
SURFACE = "#FFFFFF"
PAGE = "#E7EEF5"
BRAND = "#0E7490"
OK = "#15803D"
CASH = "#7BA3C9"
EQUITY = "#0E7490"
UP = "#15803D"
DOWN = "#C2413A"
AMBER = "#C2710C"
GRID = "#E2EAF2"

NAV_KEY = "mode"

_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF ", "JPY": "¥"}

_CSS = f"""
<style>
  .stApp {{ background: {PAGE}; }}
  .block-container {{
      padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1120px;
  }}
  header[data-testid="stHeader"] {{ background: transparent; }}
  div[data-testid="stDecoration"],
  div[data-testid="stToolbar"],
  footer, #MainMenu {{ display: none; }}
  section[data-testid="stSidebar"],
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="collapsedControl"] {{ display: none; }}

  h1, h2, h3, p, span, div, label, button {{
      font-family: Inter, "Segoe UI", Helvetica, Arial, sans-serif;
  }}

  .pit-topline {{
      display: flex; align-items: baseline; gap: 1rem;
      margin: 0 0 1rem 0; color: {MUTED}; font-size: 13px;
  }}
  .pit-topline b {{
      color: {INK}; font-size: 15px; font-weight: 700; letter-spacing: -0.01em;
  }}
  .pit-topline .grow {{ margin-left: auto; }}

  .pit-hero {{ margin: 1.1rem 0 1.1rem 0; }}
  .pit-title {{
      font-size: 42px; line-height: 1; font-weight: 650;
      letter-spacing: -0.03em; color: {INK}; margin: 0 0 .45rem 0;
  }}
  .pit-sub {{
      font-size: 17px; color: {MUTED}; margin: 0; max-width: 640px;
      line-height: 1.45;
  }}

  .pit-card {{
      background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 16px;
      padding: 16px 18px 14px; min-height: 96px; margin-bottom: 12px;
  }}
  .pit-card .label {{
      font-family: Inter, "Segoe UI", sans-serif;
      font-size: 12px; font-weight: 650; letter-spacing: .04em;
      text-transform: uppercase; color: {FAINT}; margin-bottom: 8px;
  }}
  .pit-card .value {{
      font-size: 28px; font-weight: 650; letter-spacing: -0.03em;
      color: {INK}; line-height: 1.05;
  }}
  .pit-card .sub {{
      font-family: Inter, "Segoe UI", sans-serif;
      font-size: 13px; color: {MUTED}; margin-top: 8px; line-height: 1.4;
  }}
  .pit-card .sub.up {{ color: {UP}; }}
  .pit-card .sub.down {{ color: {DOWN}; }}
  .pit-card.accent-up .value {{ color: {UP}; }}
  .pit-card.accent-down .value {{ color: {DOWN}; }}
  .pit-card.accent-blue .value,
  .pit-card.accent-brand .value {{ color: {INK}; }}

  .pit-section {{
      display: flex; align-items: baseline; gap: .75rem;
      margin: 1.6rem 0 .75rem 0;
  }}
  .pit-section h3 {{
      font-size: 22px; font-weight: 650; color: {INK}; margin: 0;
      letter-spacing: -0.02em;
  }}
  .pit-section .hint {{
      font-family: Inter, "Segoe UI", sans-serif;
      font-size: 13px; color: {FAINT}; margin-left: auto;
  }}

  .pit-pill {{
      font-family: Inter, "Segoe UI", sans-serif;
      display: inline-block; padding: 2px 8px; border-radius: 999px;
      font-size: 12px; font-weight: 650;
  }}
  .pit-pill.ok {{ background: #E5F4EC; color: {OK}; }}
  .pit-pill.warn {{ background: #FBF1E4; color: {AMBER}; }}
  .pit-pill.bad {{ background: #F8E8E6; color: {DOWN}; }}
  .pit-pill.info {{ background: #F5F5F4; color: {INK}; }}
  .pit-pill.mute {{ background: #F5F5F4; color: {MUTED}; }}

  .pit-callout {{
      background: {SURFACE}; border: 1px solid {BORDER};
      border-radius: 14px; padding: 14px 16px; margin: 0 0 12px 0;
      font-size: 15px; color: {INK}; line-height: 1.5;
  }}
  .pit-callout.warn {{ background: #FBF7F1; }}
  .pit-callout .cap {{
      font-family: Inter, "Segoe UI", sans-serif;
      font-size: 12px; font-weight: 650; letter-spacing: .04em;
      text-transform: uppercase; color: {FAINT}; margin-bottom: 4px;
  }}
  .pit-callout code {{
      font-family: "DejaVu Sans Mono", ui-monospace, monospace;
      font-size: 13px; background: #F5F5F4; padding: 1px 5px; border-radius: 5px;
  }}

  .pit-empty {{
      background: {SURFACE}; border: 1px dashed {BORDER};
      border-radius: 16px; padding: 28px 22px; text-align: left;
  }}
  .pit-empty .t {{ font-size: 18px; font-weight: 650; color: {INK}; }}
  .pit-empty .d {{
      font-family: Inter, "Segoe UI", sans-serif;
      font-size: 14px; color: {MUTED}; margin-top: 6px; line-height: 1.5;
  }}

  div[data-testid="stDataFrame"] {{
      border: 1px solid {BORDER}; border-radius: 14px; overflow: hidden;
      background: {SURFACE};
  }}
  div[data-testid="stPlotlyChart"] {{
      background: {SURFACE}; border: 1px solid {BORDER};
      border-radius: 16px; padding: 8px 8px 0;
  }}
  div[data-testid="stForm"] {{
      border: 1px solid {BORDER}; border-radius: 16px; background: {SURFACE};
      padding: 8px 14px 12px;
  }}
  div[data-testid="stExpander"] {{
      border: 1px solid {BORDER}; border-radius: 14px; background: {SURFACE};
  }}
  .stButton > button {{
      border-radius: 999px; font-weight: 650; min-height: 42px;
      font-family: Inter, "Segoe UI", sans-serif;
  }}
  .stButton > button[kind="primary"],
  .stButton > button[data-testid="stBaseButton-primary"] {{
      background: {BRAND}; color: #FFFFFF; border: 1px solid {BRAND};
  }}
  .stButton > button[kind="secondary"],
  .stButton > button[data-testid="stBaseButton-secondary"] {{
      background: {SURFACE}; color: {INK}; border: 1px solid {BORDER};
  }}
  button[data-variant="segmented_control"] {{
      min-height: 46px; border-radius: 12px !important;
  }}
  button[data-variant="segmented_control"] p {{
      font-family: Inter, "Segoe UI", sans-serif;
      font-size: 15px; font-weight: 650; margin: 0;
      white-space: nowrap;
  }}
  button[data-variant="segmented_control"][aria-checked="true"] {{
      background: {BRAND} !important; color: #FFFFFF !important;
  }}
  button[data-variant="segmented_control"][aria-checked="true"] p {{
      color: #FFFFFF !important;
  }}
</style>
"""


def apply() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def go(page: str) -> None:
    st.session_state[NAV_KEY] = page
    st.rerun()


def symbol(currency: str = "EUR") -> str:
    return _SYMBOLS.get((currency or "EUR").upper(), f"{currency} ")


def money(value: Optional[float], currency: str = "EUR",
          signed: bool = False) -> str:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        number = 0.0
    body = f"{symbol(currency)}{abs(number):,.2f}"
    if signed:
        return f"{'+' if number >= 0 else '-'}{body}"
    return f"-{body}" if number < 0 else body


def money_or_dash(value: Optional[float], currency: str = "EUR") -> str:
    if value is None or value == "":
        return "—"
    return money(value, currency)


def pct(value: Optional[float], signed: bool = True) -> str:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:+.2f}%" if signed else f"{number:.2f}%"


def tone_of(value: Optional[float]) -> str:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        number = 0.0
    if number > 0:
        return "up"
    if number < 0:
        return "down"
    return "mute"


def heading(title: str, subtitle: str) -> None:
    st.markdown(
        "<div class='pit-hero'>"
        f"<div class='pit-title'>{esc(title)}</div>"
        f"<p class='pit-sub'>{esc(subtitle)}</p>"
        "</div>",
        unsafe_allow_html=True,
    )


def page_header(eyebrow: str, title: str, subtitle: str) -> None:
    """Kept so older page modules still import. New pages use ``heading``."""
    heading(title, subtitle)


def card(label: str, value: str, sub: Optional[str] = None,
         tone: str = "default") -> str:
    accent = "" if tone in ("default", "mute") else f" accent-{esc(tone)}"
    sub_class = tone if tone in ("up", "down") else ""
    sub_html = f"<div class='sub {sub_class}'>{esc(sub)}</div>" if sub else ""
    return (
        f"<div class='pit-card{accent}'>"
        f"<div class='label'>{esc(label)}</div>"
        f"<div class='value'>{esc(value)}</div>"
        f"{sub_html}</div>"
    )


def card_row(cards: Sequence[str]) -> None:
    if not cards:
        return
    columns = st.columns(len(cards))
    for column, markup in zip(columns, cards):
        with column:
            st.markdown(markup, unsafe_allow_html=True)


def section(title: str, hint: Optional[str] = None) -> None:
    hint_html = f"<span class='hint'>{esc(hint)}</span>" if hint else ""
    st.markdown(
        "<div class='pit-section'>"
        f"<h3>{esc(title)}</h3>{hint_html}</div>",
        unsafe_allow_html=True,
    )


def pill(text: str, tone: str = "mute") -> str:
    return f"<span class='pit-pill {esc(tone)}'>{esc(text)}</span>"


def pills(items: Iterable[str]) -> None:
    st.markdown(" ".join(items), unsafe_allow_html=True)


def callout(body: str, tone: str = "info",
            caption: Optional[str] = None) -> None:
    cap = f"<div class='cap'>{esc(caption)}</div>" if caption else ""
    st.markdown(
        f"<div class='pit-callout {esc(tone)}'>{cap}{body}</div>",
        unsafe_allow_html=True,
    )


def empty(title: str, detail: str) -> None:
    st.markdown(
        f"<div class='pit-empty'><div class='t'>{esc(title)}</div>"
        f"<div class='d'>{esc(detail)}</div></div>",
        unsafe_allow_html=True,
    )


def divider() -> None:
    st.markdown("<hr class='pit'/>", unsafe_allow_html=True)


def footer(*lines: str) -> None:
    st.markdown(
        "<div class='pit-foot'>" + "<br/>".join(esc(x) for x in lines) + "</div>",
        unsafe_allow_html=True,
    )


def brand() -> None:
    st.markdown("<b>Positive Impact</b>", unsafe_allow_html=True)


def style_figure(fig: go.Figure, height: int = 330,
                 y_title: Optional[str] = None,
                 legend: bool = False) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=12, t=28, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Segoe UI, sans-serif", size=12, color=MUTED),
        hoverlabel=dict(bgcolor=SURFACE, font_size=12, font_color=INK,
                        bordercolor=BORDER),
        showlegend=legend,
        legend=dict(orientation="h", y=1.12, x=0,
                    font=dict(color=MUTED)) if legend else None,
        xaxis=dict(showgrid=False, linecolor=BORDER,
                   tickfont=dict(color=FAINT), title=None),
        yaxis=dict(showgrid=True, gridcolor=GRID, zeroline=False,
                   linecolor=BORDER, tickfont=dict(color=FAINT),
                   title=dict(text=y_title, font=dict(color=FAINT))
                   if y_title else None),
    )
    return fig
