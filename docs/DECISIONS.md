# Project decisions and constraints

Product constraints for Positive Impact Trading. These are **Badre's stated
decisions**, not generic trading advice. Strategy, compliance, and trading code
must follow them when they change — this file does not implement them.

## Priority: ticker → signal → backtest → paper

The top product priority is the end-to-end pipeline:

1. **Add a new ticker**
2. **Produce a signal** for that name
3. **Backtest** the idea
4. **Paper-trade** it through IBKR

Work that does not move this pipeline forward is secondary. Architecture polish,
extra UI surfaces, and parallel strategy stacks should wait unless they unblock
a step above.

## Target design: file-driven, one file per stock

**Decision:** the pipeline should be file-driven. Each stock gets its own file
(ticker, parameters, signal rules). The lab picks those files up from a folder
— adding or changing a name is editing a file, not changing runner code.

**Current state (honest):** scaffolded, not yet the live runner source of truth.

A `stocks/` folder holds one JSON file per ticker (signal entry + per-stock
ATR / optional fixed TP-SL). `marketmind.stock_specs` loads that folder. The
live universe is still the SQLite `instruments` table (cockpit add; 
`DEFAULT_UNIVERSE` is only a seed), and the paper runner still uses the shared
contest / catalogue rules. Wire the runner to stock files in a later step.

## Operator context: France + US stocks via IBKR

Badre is French and trades **US stocks through Interactive Brokers (IBKR)**.

Therefore, when backtests and strategy logic are extended later:

* Model **IBKR fees / commissions** that apply to this account and product,
  not a generic US retail fee schedule.
* Account for **France-specific constraints** that affect a French resident
  trading US equities (tax, reporting, product access, or other rules Badre
  states) — do not invent constraints that were never specified.
* Prefer EUR account realities already visible in the paper lab (e.g. base
  currency vs USD contracts) over assumptions copied from US-only tutorials.

This file records the constraint; it does not encode fee tables or tax rules.

## North star: tidy work that makes money with positive impact

**Decision:** keep the lab tidy and organized. When choosing between paths,
optimize for the end goal: **make money in a positive way**, with trading that
has a **positive impact**. Clean structure and clarity serve that goal; busywork
and sprawl do not.

**Current state (honest):** the paper lab is deliberately small and long-only,
with an Impact compliance gate, but this north star is a **product constraint
for future choices**, not a claim that every module already maximizes impact
P&L. Prefer organization that advances the ticker → signal → backtest → paper
pipeline and the impact screen over parallel features that do not.

## Eligibility filters: which stocks may be bought

**Decision:** impose the **right filters** so the platform can decide which
stocks are eligible to buy and which are not. Eligibility is a first-class
product rule, not an afterthought at order time.

**Current state (honest):** a compliance gate already exists
(`ImpactExecutionEngine`, sector / debt rules, impact ETF whitelist) and can
block equity BUYs. Excluded sectors, debt cap, and whitelists now load from
the shared file `ethics/exclusions.json`, with alias-aware sector matching for
IB/UI labels. Remaining gaps: incomplete fundamentals on the paper path, and
impact still competing as a signal rather than only as a hard pre-filter.
Continue hardening eligibility rather than inventing new screens in this doc.

## Human usability: trading cockpit UX

**Decision:** the platform must be **humanly usable**, like a plane cockpit for
trading — best possible UX for the operator, not a developer-only console.

Required shape:

* **One click to run** the algo (or as close as Gateway prerequisites allow)
* **Clear background status** that it is running (and when it is not)
* A **direct view** of stocks, portfolio, and open positions
* A **signal indicator** when the algorithm emits a buy
* **Cockpit-style ergonomics**: scan-first layout, obvious controls, no hunting
  for the live state

**Current state (honest):** a Streamlit cockpit already provides Start/Stop
Algo, an “Algo running / stopped” status, Trading / Strategies / Journal pages,
coverage of the universe, and open positions / orders while the session is up.
That is a **partial** match. Gaps relative to this decision include: richer
always-visible running status, a clearer dedicated buy-signal indicator, and
cockpit ergonomics that fully match “plane cockpit for trading” rather than a
capable but still improvable lab UI. Do not pretend the UX bar is already met;
raise the cockpit toward this bar without breaking the pipeline priority.

## Grounding rule

Every product or strategy decision must be grounded in **Badre's stated
constraints** (this document, the README, and explicit operator input). Do not
substitute generic trading advice, textbook defaults, or “best practice”
playbooks when they conflict with those constraints.
