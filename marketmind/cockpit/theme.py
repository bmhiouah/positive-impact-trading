"""One visual system for the whole cockpit.

Colours follow the overview diagram: navy surfaces, emerald for what is
allowed to happen, red for a loss or a stop. Gains are green and losses are
red. Status pills use the same emerald, so "running" and "up" agree with
each other. Cash is blue on purpose — it is neither a gain nor a loss.
"""

from __future__ import annotations

import html
from typing import Iterable, Optional, Sequence

import plotly.graph_objects as go
import streamlit as st

# --- Palette -----------------------------------------------------------------
INK = "#EAF1FB"
MUTED = "#A9BAD3"
FAINT = "#7F93AD"
BORDER = "#27406A"
SURFACE = "#13294B"
PAGE = "#0B1F3A"
BRAND = "#10B981"
OK = "#2DD4BF"
CASH = "#60A5FA"
EQUITY = "#10B981"
UP = "#34D399"        # gains / rising prices
DOWN = "#F87171"      # losses / falling prices
AMBER = "#FBBF24"
GRID = "rgba(169,186,211,.14)"

NAV_KEY = "nav"

_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF ", "JPY": "¥"}

_CSS = f"""
<style>
  .stApp {{ background: {PAGE}; }}
  .block-container {{
      padding-top: 2.6rem; padding-bottom: 2.6rem; max-width: 1240px;
  }}
  header[data-testid="stHeader"] {{ background: transparent; }}
  div[data-testid="stDecoration"] {{ display: none; }}
  div[data-testid="stToolbar"] {{ display: none; }}
  footer {{ display: none; }}
  #MainMenu {{ display: none; }}

  section[data-testid="stSidebar"] {{
      background: #0c1c33; border-right: 1px solid {BORDER};
  }}
  section[data-testid="stSidebar"] .block-container {{
      padding-top: 1.35rem; padding-bottom: 1.2rem;
  }}
  section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{
      gap: 0.4rem;
  }}
  h1, h2, h3, h4, h5, p, span, div, label {{
      font-family: Inter, "Segoe UI", Helvetica, Arial, sans-serif;
  }}

  .pit-brand {{ display:flex; align-items:center; gap:.7rem; margin:0 0 .85rem 0; }}
  .pit-brand-mark {{
      width:36px; height:36px; border-radius:11px; flex:none;
      background:linear-gradient(135deg, {BRAND}, {OK});
      color:#04281c; font-weight:800; font-size:16px;
      display:flex; align-items:center; justify-content:center;
  }}
  .pit-brand-name {{ font-size:15px; font-weight:700; color:{INK}; line-height:1.15; }}
  .pit-brand-sub {{ font-size:11px; color:{FAINT}; letter-spacing:.08em;
      text-transform:uppercase; margin-top:2px; }}

  .pit-nav-label {{ font-size:11px; font-weight:700; letter-spacing:.14em;
      text-transform:uppercase; color:{FAINT}; margin:1rem 2px .35rem; }}

  section[data-testid="stSidebar"] div.stButton {{ margin-bottom: .15rem; }}
  section[data-testid="stSidebar"] div.stButton button {{
      min-height: 38px;
      padding: .35rem .75rem .35rem .85rem !important;
      border-radius: 10px !important;
      font-weight: 600 !important;
      box-shadow: none !important;
  }}
  section[data-testid="stSidebar"] div.stButton button > div {{
      justify-content: flex-start !important;
      width: 100%;
  }}
  section[data-testid="stSidebar"] div.stButton button p {{
      text-align: left !important;
      width: 100% !important;
      margin: 0;
  }}
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"],
  section[data-testid="stSidebar"] button[kind="secondary"] {{
      background: transparent !important;
      color: {MUTED} !important;
      border: 1px solid transparent !important;
  }}
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"],
  section[data-testid="stSidebar"] button[kind="primary"] {{
      background: rgba(16,185,129,.16) !important;
      color: {INK} !important;
      border: 1px solid rgba(16,185,129,.35) !important;
      box-shadow: inset 3px 0 0 {BRAND} !important;
  }}

  .pit-hero {{ margin: 0 0 1rem 0; }}
  .pit-eyebrow {{ font-size:11px; font-weight:700; letter-spacing:.14em;
      text-transform:uppercase; color:{OK}; margin-bottom:.3rem; }}
  .pit-title {{ font-size:28px; line-height:1.12; font-weight:750;
      letter-spacing:-.02em; color:{INK}; margin:0 0 .35rem 0; }}
  .pit-sub {{ font-size:14px; color:{MUTED}; margin:0; max-width:720px;
      line-height:1.5; }}

  .pit-card {{
      background:{SURFACE}; border:1px solid {BORDER}; border-radius:14px;
      padding:14px 16px; min-height:102px; margin-bottom:12px;
  }}
  .pit-card .label {{ font-size:11px; font-weight:700; letter-spacing:.09em;
      text-transform:uppercase; color:{FAINT}; margin-bottom:8px; }}
  .pit-card .value {{ font-size:24px; font-weight:730; letter-spacing:-.02em;
      color:{INK}; line-height:1.1; }}
  .pit-card .sub {{ font-size:12px; color:{MUTED}; margin-top:7px;
      line-height:1.4; }}
  .pit-card .sub.up {{ color:{UP}; }}
  .pit-card .sub.down {{ color:{DOWN}; }}
  .pit-card.accent-up .value {{ color:{UP}; }}
  .pit-card.accent-down .value {{ color:{DOWN}; }}
  .pit-card.accent-blue .value,
  .pit-card.accent-brand .value {{ color:{BRAND}; }}
  .pit-card.pit-status {{ min-height:0; padding:12px 14px; margin-bottom:4px; }}
  .pit-status-row {{
      display:flex; justify-content:space-between; align-items:center;
      margin-bottom:8px; font-size:12px; color:{MUTED};
  }}
  .pit-status-row:last-of-type {{ margin-bottom:0; }}

  .pit-section {{ display:flex; align-items:baseline; gap:.6rem;
      margin:1.25rem 0 .7rem 0; }}
  .pit-section-bar {{ width:3px; height:16px; border-radius:3px;
      background:{BRAND}; flex:none; align-self:center; }}
  .pit-section h3 {{ font-size:16px; font-weight:700; color:{INK}; margin:0; }}
  .pit-section .hint {{ font-size:12px; color:{FAINT}; margin-left:auto; }}

  .pit-pill {{ display:inline-block; padding:3px 10px; border-radius:999px;
      font-size:11px; font-weight:700; letter-spacing:.02em; }}
  .pit-pill.ok {{ background:rgba(16,185,129,.16); color:{BRAND}; }}
  .pit-pill.warn {{ background:rgba(251,191,36,.16); color:{AMBER}; }}
  .pit-pill.bad {{ background:rgba(248,113,113,.16); color:{DOWN}; }}
  .pit-pill.info {{ background:rgba(45,212,191,.14); color:{OK}; }}
  .pit-pill.mute {{ background:rgba(169,186,211,.12); color:{MUTED}; }}

  .pit-callout {{ background:#10243f; border:1px solid {BORDER};
      border-left:3px solid {OK}; border-radius:12px; padding:12px 14px;
      margin:0 0 12px 0; font-size:13px; color:{INK}; line-height:1.55; }}
  .pit-callout strong, .pit-callout b {{ color:{INK}; }}
  .pit-callout.warn {{ border-left-color:{AMBER}; background:#1c2433; }}
  .pit-callout.up {{ border-left-color:{DOWN}; background:#241820; }}
  .pit-callout .cap {{ font-size:11px; font-weight:700; letter-spacing:.09em;
      text-transform:uppercase; color:{FAINT}; margin-bottom:4px; }}
  .pit-callout code {{
      font-family: "DejaVu Sans Mono", ui-monospace, monospace;
      font-size:12px; color:{OK}; background:rgba(45,212,191,.1);
      padding:1px 5px; border-radius:5px;
  }}

  .pit-empty {{ background:{SURFACE}; border:1px dashed {BORDER};
      border-radius:14px; padding:26px 22px; text-align:center; }}
  .pit-empty .t {{ font-size:15px; font-weight:700; color:{INK}; }}
  .pit-empty .d {{ font-size:13px; color:{MUTED}; margin-top:6px;
      line-height:1.5; }}

  .pit-foot {{ font-size:11px; color:{FAINT}; line-height:1.65; margin-top:0.2rem; }}
  .pit-kv {{ font-size:12px; color:{MUTED}; line-height:1.7; margin-top:.85rem; }}
  .pit-kv b {{ color:{INK}; font-weight:650; }}
  .pit-side-stats {{
      display:grid; grid-template-columns:1fr 1fr; gap:10px 12px;
      margin-top:0.85rem; margin-bottom:0.35rem;
  }}
  .pit-side-stats b {{ display:block; color:{INK}; font-size:16px;
      font-weight:700; letter-spacing:-.02em; }}
  .pit-side-stats span {{ display:block; color:{FAINT}; font-size:11px;
      margin-top:1px; }}

  div[data-testid="stDataFrame"] {{
      border:1px solid {BORDER}; border-radius:12px; overflow:hidden;
  }}
  div[data-testid="stPlotlyChart"] {{
      background:{SURFACE}; border:1px solid {BORDER};
      border-radius:14px; padding:4px 4px 0;
  }}
  div[data-testid="stForm"] {{
      border:1px solid {BORDER}; border-radius:14px; background:#10243f;
      padding:4px 12px 8px;
  }}
  div[data-testid="stExpander"] {{
      border:1px solid {BORDER}; border-radius:12px; background:#10243f;
  }}
  .stButton > button {{
      border-radius:10px; font-weight:600; min-height:40px;
  }}
  .stButton > button[kind="primary"],
  .stButton > button[data-testid="stBaseButton-primary"] {{
      background:{BRAND}; color:#04281c; border:1px solid {BRAND};
  }}
  .stButton > button[kind="secondary"],
  .stButton > button[data-testid="stBaseButton-secondary"] {{
      background:{SURFACE}; color:{INK}; border:1px solid {BORDER};
  }}
  .stTabs [data-baseweb="tab-list"] {{ gap: 2px; }}
  .stTabs [data-baseweb="tab"] {{ font-weight:600; border-radius:8px 8px 0 0; }}
</style>
"""


def apply() -> None:
    """Inject the stylesheet once per rerun (idempotent)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def go(page: str) -> None:
    """Open another cockpit page on the next rerun."""
    st.session_state[NAV_KEY] = page
    st.rerun()


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


# --- Building blocks ---------------------------------------------------------

def brand() -> None:
    st.markdown(
        "<div class='pit-brand'>"
        f"<div class='pit-brand-mark'>P</div>"
        "<div><div class='pit-brand-name'>Positive Impact</div>"
        "<div class='pit-brand-sub'>Paper trading lab</div></div>"
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
    accent = "" if tone in ("default", "mute") else f" accent-{esc(tone)}"
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
    """Apply the shared navy palette to a Plotly figure."""
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=12, t=16, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Segoe UI, sans-serif",
                  size=12, color=MUTED),
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
