# Positive Impact Trading — Paper Lab

A small, deliberately minimal platform to **experiment with algorithmic paper
trading**. Everything here is long-only and paper-only. When the project is
mature, the same ideas can be carried over to real trading.

Technical details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The original
AI-research-platform vision is kept (for context only) in
[`docs/archive/`](docs/archive/README.md).

## What it does

* Connects to **one** IB Gateway paper session and starts the algo on demand.
* Reads **capital from the IBKR account via the API** — it is never a setting.
* Records the **original paper money once**, then one clean row per day so you
  can watch the money evolve and see exactly which trades were done.
* Shows a **cash vs long-equity camembert** on the first page.
* Sends **every entry as a TP/SL bracket** — no naked orders, ever.

## There is nothing to configure

| | Value |
|---|---|
| Gateway | `127.0.0.1:4002` (paper) |
| Client id | `1` — one connection at a time |
| Market data | delayed (type 3) |
| Take profit | `+5%` from the reference price |
| Stop loss | `-3%` from the reference price |
| Journal | `data/marketmind.db` |

All of these are plain constants in `marketmind/config.py`. Host, port, client
id, capital and live/paper mode are **not** parameters, environment variables or
UI inputs. There is no `.env`: nothing in the code reads one.

Prerequisites: IB Gateway running and logged into the paper account, with
`Configure > API > Settings > Enable ActiveX and Socket Clients`, and no other
client occupying client id 1.

## Running it

```bash
# Cockpit (Overview is the first page)
python scripts/run_cockpit.py                 # or: streamlit run marketmind/cockpit/app.py

# Bot on its own (Ctrl+C to stop)
python -m marketmind.live_trading --interval 60   # or the installed `run-bot` script
```

On the **🏠 Overview** page, press **🔄 Read from IBKR** to record a fresh
observation without starting the algo. The first read (or the first Start Algo)
writes the immutable baseline — the original paper money. On **🤖 Trading**,
press **▶️ Start Algo**: it connects first, verifies the session by reading
account values, and only then starts the trading thread. If anything fails the
algo stays stopped and the real IBKR error is shown.

Stopping the algo leaves resting TP/SL brackets at IBKR, so open positions stay
protected overnight.

## Project layout

```
README.md                       this guide
docs/ARCHITECTURE.md            module map, lifecycle, bracket and journal design
docs/archive/                   pre-MVP vision documents (not runtime code)
marketmind/
  config.py                     fixed constants — the only place to change the session
  ib_compat.py                  Python 3.13+ event-loop bootstrap for ib_insync
  broker/ib_adapter.py          the single IBKR session + bracket builder
  compliance/                   ImpactExecutionEngine gate
  journal.py                    paper_baseline / paper_daily / paper_orders
  data/                         SQLite store, typed models, screening, IBKR ingestion
  strategies.py / backtester.py signals (BUY only) and historical backtests
  live_trading.py               LiveTradingRunner + CLI entry point
  cockpit/                      Streamlit app: Overview · Trading · Strategies · Analytics
scripts/
  run_cockpit.py                the one cockpit launcher
  ib_connection_probe.py        standalone connect → disconnect → reconnect + bracket test
tests/                          unit tests + opt-in live Gateway integration tests
data/                           local journal DB (git-ignored, created on first run)
```

## The journal

`marketmind/journal.py` owns three tables in `data/marketmind.db`:

| Table | Contents |
|---|---|
| `paper_baseline` | one immutable row: original capital / cash / equity, from the API |
| `paper_daily` | one row per UTC day (upserted): net liquidation, cash, equity, P&L, bracket count |
| `paper_orders` | every bracket sent: entry / TP / SL order ids and prices, status, notes |

The journal is the **single source of truth**. `record()` runs on every trading
cycle, so it writes *only* to `paper_daily` (one upserted row per day) and never
appends to the legacy `account_snapshots` / `portfolio_equity` tables — doing so
would recreate the row noise this lab cleaned up. All three journal tables are
inspectable from the Analytics data browser.

The camembert's split comes from the IBKR tags: `equity = GrossPositionValue`
(market value of the long book, `0` when flat) and `cash = NetLiquidation -
equity`. The residual between `NetLiquidation` and `TotalCashValue` on a flat
account is **accrued interest**, not a stock position, so it correctly stays in
cash.

## Orders

Every submitted entry is a three-leg bracket built in
`marketmind/broker/ib_adapter.py`:

```
BUY parent (market or limit, transmit=False)
  ├─ SELL LIMIT take-profit   ref × (1 + 5%)   (parentId, transmit=False)
  └─ SELL STOP  stop-loss     ref × (1 - 3%)   (parentId, transmit=True)
```

`ref` is the bid/ask mid (falling back to last / close). If no reference price
can be obtained, submission **fails instead of sending a naked order**. IB
cancels the remaining child automatically when either leg fills. All three legs
are sanitised for the Gateway v151 `EtradeOnly` rejection (error 10268).
Orders still pass through the `ImpactExecutionEngine` compliance gate; the
runner only opens BUY positions, and skips a symbol that already has a position
or a resting bracket.

## Architecture note

The whole `ib_insync` session (connect, data, orders, disconnect) is owned by
**one dedicated event-loop thread**. All public adapter methods are synchronous
and thread-safe, so Streamlit's script thread never manages an asyncio loop.
`connect()` always disconnects first and makes a single handshake attempt —
graceful `IB.disconnect()` is what frees client id 1 for the next run.

## Verifying

```bash
PY=/Users/badremhiouah/miniconda3/envs/py314/bin/python   # project environment

$PY scripts/ib_connection_probe.py            # connect -> disconnect -> reconnect
$PY scripts/ib_connection_probe.py --all      # + data + TP/SL bracket round trip
$PY -m pytest tests/                          # unit tests, no Gateway needed
MARKETMIND_IB_INTEGRATION=1 $PY -m pytest tests/test_ib_adapter.py  # live Gateway
```

The probe places a deliberately unfillable 1-share bracket, confirms IB
acknowledges it, cancels all three legs and disconnects. No position is opened.
Live tests are opt-in and need the Gateway on port 4002.

In this workspace, keep pytest's temp dirs inside the project — the sandbox
brokers filesystem calls under `/private/var/...` and can raise `EEXIST` /
`ENOENT` when `mkdir` hits an existing or missing parent:

```bash
T="$PWD/.workbuddy-ai/tmp/run-$(date +%s)" && mkdir -p "$T"
TMPDIR="$T" $PY -m pytest tests/
```

## MVP status

Connection, account reads, Start/Stop Algo, the cash/equity camembert, the clean
journal and TP/SL brackets are verified against a live paper Gateway.

The price/fundamental tables were emptied during the data cleanup, so a trading
cycle currently produces **0 signals / 0 orders**. `marketmind/data/ingestion.py`
and `enrichment.py` are available to load IBKR history; wiring them into the
signal loop and defining a long-entry strategy is the next step. This is a
strategy/data gap, not a connection or order problem.
