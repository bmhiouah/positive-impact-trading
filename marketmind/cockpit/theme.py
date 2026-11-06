"""One visual system for the whole cockpit.

Every page uses these helpers instead of ad-hoc Streamlit widgets, so colours,
typography, cards and charts stay consistent. The palette is light and calm;
price/P&L direction follows the Chinese market convention (up = red,
down = green). Cash/equity and data-series colours are deliberately neutral.
"""

from __future__ import annotations

import html
from typing import Iterable, Optional, Sequence

import plotly.graph_objects as go
import streamlit as st

# --- Palette -----------------------------------------------------------------
INK = "#0F1B2D"
MUTED = "#61728B"
FAINT = "#94A3B8"
BORDER = "#E2E9F3"
SURFACE = "#FFFFFF"
PAGE = "#F5F7FB"
BRAND = "#1D4ED8"
CASH = "#9DC1F0"
EQUITY = "#1D4ED8"
UP = "#D64545"       # gains / rising prices — red (Chinese convention)
DOWN = "#0E8A6E"     # losses / falling prices — green
AMBER = "#C77700"
GRID = "#EEF2F8"

_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF ", "JPY": "¥"}

_CSS = f"""
<style>
  .stApp {{ background: {PAGE}; }}
  .block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px; }}
  section[data-testid="stSidebar"] {{
      background: #FFFFFF; border-right: 1px solid {BORDER};
  }}
  section[data-testid="stSidebar"] .block-container {{ padding-top: 1.6rem; }}
  h1, h2, h3, h4, h5, p, span, div {{ font-family: 'Inter', -apple-system,
      BlinkMacSystemFont, 'Segoe UI', sans-serif; }}

  .pit-brand {{ display:flex; align-items:center; gap:.65rem; margin-bottom:.2rem; }}
  .pit-brand-mark {{
      width:34px; height:34px; border-radius:10px; flex:none;
      background:linear-gradient(135deg, {BRAND}, #4E86F7);
      color:#fff; font-weight:700; font-size:16px;
      display:flex; align-items:center; justify-content:center;
      box-shadow:0 4px 12px rgba(29,78,216,.24);
  }}
  .pit-brand-name {{ font-size:15px; font-weight:700; color:{INK}; line-height:1.15; }}
  .pit-brand-sub {{ font-size:11px; color:{FAINT}; letter-spacing:.06em;
      text-transform:uppercase; }}

  .pit-hero {{ margin: 0 0 1.35rem 0; }}
  .pit-eyebrow {{ font-size:11px; font-weight:700; letter-spacing:.14em;
      text-transform:uppercase; color:{BRAND}; margin-bottom:.35rem; }}
  .pit-title {{ font-size:30px; line-height:1.15; font-weight:750;
      letter-spacing:-.02em; color:{INK}; margin:0 0 .4rem 0; }}
  .pit-sub {{ font-size:14px; color:{MUTED}; margin:0; max-width:900px;
      line-height:1.55; }}

  .pit-card {{
      background:{SURFACE}; border:1px solid {BORDER}; border-radius:14px;
      padding:16px 18px; box-shadow:0 1px 2px rgba(15,27,45,.035);
      min-height:106px; margin-bottom:14px;
  }}
  .pit-card .label {{ font-size:11px; font-weight:700; letter-spacing:.09em;
      text-transform:uppercase; color:{FAINT}; margin-bottom:8px; }}
  .pit-card .value {{ font-size:25px; font-weight:730; letter-spacing:-.02em;
      color:{INK}; line-height:1.1; white-space:nowrap; }}
  .pit-card .sub {{ font-size:12px; color:{MUTED}; margin-top:7px;
      line-height:1.4; }}
  .pit-card .sub.up {{ color:{UP}; }}
  .pit-card .sub.down {{ color:{DOWN}; }}
  .pit-card.accent-up .value {{ color:{UP}; }}
  .pit-card.accent-down .value {{ color:{DOWN}; }}
  .pit-card.accent-blue .value {{ color:{BRAND}; }}

  .pit-section {{ display:flex; align-items:baseline; gap:.6rem;
      margin:1.7rem 0 .85rem 0; }}
  .pit-section-bar {{ width:3px; height:17px; border-radius:3px;
      background:{BRAND}; flex:none; align-self:center; }}
  .pit-section h3 {{ font-size:17px; font-weight:700; color:{INK}; margin:0; }}
  .pit-section .hint {{ font-size:12px; color:{FAINT}; margin-left:auto; }}

  .pit-pill {{ display:inline-block; padding:3px 10px; border-radius:999px;
      font-size:11px; font-weight:700; letter-spacing:.02em; }}
  .pit-pill.ok {{ background:#E4F5EF; color:{DOWN}; }}
  .pit-pill.warn {{ background:#FDF1DF; color:{AMBER}; }}
  .pit-pill.bad {{ background:#FCE9E9; color:{UP}; }}
  .pit-pill.info {{ background:#E8EFFD; color:{BRAND}; }}
  .pit-pill.mute {{ background:#EEF2F7; color:{MUTED}; }}

  .pit-callout {{ background:#FFFFFF; border:1px solid {BORDER};
      border-left:3px solid {BRAND}; border-radius:10px; padding:13px 16px;
      margin:12px 0; font-size:13px; color:{INK}; line-height:1.6; }}
  .pit-callout strong {{ color:{INK}; }}
  .pit-callout.warn {{ border-left-color:{AMBER}; background:#FFFCF6; }}
  .pit-callout.up {{ border-left-color:{UP}; background:#FFFAFA; }}
  .pit-callout .cap {{ font-size:11px; font-weight:700; letter-spacing:.09em;
      text-transform:uppercase; color:{FAINT}; margin-bottom:4px; }}

  .pit-empty {{ background:#FFFFFF; border:1px dashed {BORDER};
      border-radius:12px; padding:22px; text-align:center; }}
  .pit-empty .t {{ font-size:14px; font-weight:650; color:{INK}; }}
  .pit-empty .d {{ font-size:12px; color:{MUTED}; margin-top:5px;
      line-height:1.55; }}

  .pit-foot {{ font-size:11px; color:{FAINT}; line-height:1.7; }}
  .pit-kv {{ font-size:12px; color:{MUTED}; line-height:1.9; }}
  .pit-kv b {{ color:{INK}; font-weight:650; }}
  hr.pit {{ border:0; border-top:1px solid {BORDER}; margin:1.6rem 0; }}
  div[data-testid="stDataFrame"] {{ border:1px solid {BORDER};
      border-radius:12px; overflow:hidden; }}
  .stButton > button {{ border-radius:9px; font-weight:600; }}
  .stTabs [data-baseweb="tab"] {{ font-weight:600; }}
</style>
"""


def apply() -> None:
    """Inject the stylesheet once per rerun (idempotent)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


# --- Money & numbers ---------------------------------------------------------

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


# --- Building blocks ---------------------------------------------------------

def brand() -> None:
    st.markdown(
        "<div class='pit-brand'>"
        f"<div class='pit-brand-mark'>P</div>"
        "<div><div class='pit-brand-name'>Positive Impact</div>"
        "<div class='pit-brand-sub'>Paper Trading Lab</div></div>"
        "</div>",
        unsafe_allow_html=True,
    )


def page_header(eyebrow: str, title: str, subtitle: str) -> None:
    st.markdown(
        "<div class='pit-hero'>"
        f"<div class='pit-eyebrow'>{esc(eyebrow)}</div>"
        f"<div class='pit-title'>{esc(title)}</div>"
        f"<p class='pit-sub'>{esc(subtitle)}</p>"
        "</div>",
        unsafe_allow_html=True,
    )


def card(label: str, value: str, sub: Optional[str] = None,
         tone: str = "default") -> str:
    """Return the HTML for one KPI card (use inside a Streamlit column)."""
    accent = "" if tone == "default" else f" accent-{esc(tone)}"
    sub_class = tone if tone in ("up", "down") else ""
    sub_html = (
        f"<div class='sub {sub_class}'>{esc(sub)}</div>" if sub else ""
    )
    return (
        f"<div class='pit-card{accent}'>"
        f"<div class='label'>{esc(label)}</div>"
        f"<div class='value'>{esc(value)}</div>"
        f"{sub_html}</div>"
    )


def card_row(cards: Sequence[str]) -> None:
    """Render N cards in a responsive-looking equal column row."""
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
        "<div class='pit-section-bar'></div>"
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


# --- Charts ------------------------------------------------------------------

def style_figure(fig: go.Figure, height: int = 330,
                 y_title: Optional[str] = None,
                 legend: bool = False) -> go.Figure:
    """Apply the shared light palette to a Plotly figure."""
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=16, t=18, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, -apple-system, sans-serif",
                  size=12, color=MUTED),
        hoverlabel=dict(bgcolor="white", font_size=12,
                        bordercolor=BORDER),
        showlegend=legend,
        legend=dict(orientation="h", y=1.08, x=0) if legend else None,
        xaxis=dict(showgrid=False, linecolor=BORDER,
                   tickfont=dict(color=FAINT), title=None),
        yaxis=dict(showgrid=True, gridcolor=GRID, zeroline=False,
                   linecolor=BORDER, tickfont=dict(color=FAINT),
                   title=dict(text=y_title, font=dict(color=FAINT))
                   if y_title else None),
    )
    return fig
