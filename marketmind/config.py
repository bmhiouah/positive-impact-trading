"""Fixed constants for the paper-trading lab.

There is nothing to configure. This project talks to exactly ONE IB Gateway
paper session at a time, and capital is never supplied by a user — it is always
read back from the account through the API.

These values are deliberately plain module constants rather than environment
settings: one fixed session, one long-only paper account, no live-trading mode.
Change a constant here if the lab itself ever changes.
"""

from __future__ import annotations

# --- The single IB Gateway paper session -----------------------------------
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 4002          # Gateway paper port. Always.
IBKR_CLIENT_ID = 1        # One connection at a time. Always.
MARKET_DATA_TYPE = 3      # 3 = delayed data (paper accounts have no live feed)

# --- Lab behaviour ----------------------------------------------------------
DB_PATH = "data/marketmind.db"
POLL_INTERVAL = 60.0      # seconds between connection checks and fill reads

# The one live rule. Buy when the stock's own return from 12 months ago to
# 1 month ago is positive. The stop is two 20-day average ranges under the
# highest close since entry, and it only moves up. There is no take-profit.
# Each entry risks the same small fraction of the account; the share count
# comes from that budget divided by the stop distance.
MOMENTUM_FORMATION = 252   # trading days, about twelve months
MOMENTUM_SKIP = 21         # skip the last month (short-term reversal)
ATR_PERIOD = 20
ATR_MULTIPLE = 2.0
RISK_FRACTION = 0.005          # of net liquidation, per trade
MAX_POSITION_FRACTION = 0.20   # notional cap, so a tiny stop cannot buy the book
MAX_OPEN_POSITIONS = 4         # names held at once
MAX_BOOK_RISK = 0.02           # sum of stop-distances, as a fraction of the account

# --- Universe and market data ----------------------------------------------
# Seed only. The live universe is stored in the `instruments` table and can be
# extended from the cockpit's Data page, so no code change is needed to add a
# stock. Everything downstream (ingestion, coverage, strategies, the runner)
# reads the universe from that one table.
#
# File-driven pipeline (aspirational → scaffolded): per-ticker JSON under
# STOCKS_DIR and the shared ethics filter at ETHICS_FILTER_PATH. The paper
# runner still uses `instruments` today; specs feed backtests / eligibility.
DEFAULT_UNIVERSE = (
    "AAPL", "MSFT", "NVDA", "TSLA", "JPM", "SPUS", "HLAL",
)
STOCKS_DIR = "stocks"
ETHICS_FILTER_PATH = "ethics/exclusions.json"
HISTORY_DURATION = "5 Y"  # 12-1 momentum needs a year, and a check needs more
HISTORY_BAR_SIZE = "1 day"
MIN_BARS_FOR_SIGNALS = MOMENTUM_FORMATION + 1
BACKTEST_SESSIONS = 126   # ~6 months of daily bars for fee-aware study path
DATA_STALE_HOURS = 36      # coverage older than this is "partial", not "ready"
DATA_SYNC_COOLDOWN = 3600  # seconds between automatic refreshes by the runner
