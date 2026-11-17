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

**Current state (honest):** this design is **aspirational**, not implemented.

Today the live universe is the SQLite `instruments` table (add a ticker in the
cockpit UI; `DEFAULT_UNIVERSE` in `config.py` is only a seed). Signal rules live
in a **shared** catalogue (`strategy_catalog.py` and related modules), not in
per-stock files. There is no folder of per-ticker config that the runner loads.

Treat the file-per-stock model as the intended product shape for future work,
not as something the code already does.

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

## Grounding rule

Every product or strategy decision must be grounded in **Badre's stated
constraints** (this document, the README, and explicit operator input). Do not
substitute generic trading advice, textbook defaults, or “best practice”
playbooks when they conflict with those constraints.
