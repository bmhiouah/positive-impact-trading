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
POLL_INTERVAL = 60.0      # seconds between trading cycles
TAKE_PROFIT_PCT = 0.05    # +5% from the reference price
STOP_LOSS_PCT = 0.03      # -3% from the reference price
QUANTITY = 1.0            # shares per long entry

# --- Universe and market data ----------------------------------------------
# Seed only. The live universe is stored in the `instruments` table and can be
# extended from the cockpit's Data page, so no code change is needed to add a
# stock. Everything downstream (ingestion, coverage, strategies, the runner)
# reads the universe from that one table.
DEFAULT_UNIVERSE = (
    "AAPL", "MSFT", "NVDA", "TSLA", "JPM", "SPUS", "HLAL",
)
HISTORY_DURATION = "1 Y"
HISTORY_BAR_SIZE = "1 day"
MIN_BARS_FOR_SIGNALS = 60
DATA_STALE_HOURS = 36      # coverage older than this is "partial", not "ready"
DATA_SYNC_COOLDOWN = 3600  # seconds between automatic refreshes by the runner
