# MarketMind — Project Roadmap & Backbone Plan

> **This document is the backbone of the project.** We refer back to it at every
> step: before starting a task, confirm which Phase/Milestone it belongs to and
> what its Definition of Done is. Update the Progress Tracker as we advance.

## 1. Vision
> **From market data to actionable, risk-aware trading ideas.**

MarketMind is an AI-powered quantitative research platform connected to
Interactive Brokers. It combines market-data engineering, quantitative signals,
statistical testing, RAG over financial documents, LangGraph multi-agent
workflows, MCP tool servers, LLM evaluation, backtesting, risk management,
observability, and paper trading to identify and evaluate trading opportunities.

**Non-negotiable philosophy (from the architecture):**
**Agents propose → math validates → risk gates → then we paper-trade.**
No idea reaches IBKR paper trading without passing statistical validation and the Risk Agent.

## 2. The Architecture We Are Building (data flow)

```
 ┌──────────────┐
 │ Market Data  │  IBKR API · Historical bars · News
 └──────┬───────┘
        ▼
 ┌──────────────┐
 │ Data Eng.    │  Pandas · NumPy · SQL · cleaning · validation
 └──┬────────┬──┘
    ▼        ▼
Quant Eng.  Research DB ──► RAG System
 │  technical │  embeddings/vector
 │  + stats   │        │
 └────┬───────┘        │
      ▼                ▼
        Opportunity Engine
              │
              ▼
      LangGraph Research Agent
       ├── Quant Agent
       ├── News Agent
       └── Fundamental Agent
              ▼
         Risk Agent
              ▼
        Final Thesis
        ├── Backtesting
        └── Paper Trading ──► IBKR
```

Cross-cutting layers (built alongside, from early on): **MCP tool servers**,
**LLM evaluation**, **observability / monitoring**, **API layer**, **tests**.


## 3. Guiding Principles
1. **Data first.** Nothing is built before a reliable, reproducible data layer.
2. **Quant + RAG are parallel pillars** that feed one Opportunity Engine.
3. **Agents orchestrate; they do not replace rigor.** Statistical testing stays in code.
4. **Every thesis is backtested before paper trading.** No exceptions.
5. **Risk is a gate, not an afterthought.** Position sizing & limits enforced in code.
6. **Observability + LLM evaluation from day one** (Phase 0 scaffolding), not bolted on later.
7. **Modular, tested, reproducible.** Every component has unit tests and a config entry point.

## 4. Proposed Tech Stack (to lock in Phase 0)
| Concern | Choice (proposed) | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| Package/env | `uv` or `poetry` + `pyproject.toml` | Lock in Phase 0 |
| Config | `pydantic-settings` + `.env` / YAML | No secrets in code |
| Logging | `loguru` or std `logging` (structured) | Wired to observability |
| Broker data | `ib_insync` (IBKR TWS/Gateway) | Historical + news + paper orders |
| Data | `pandas`, `numpy`, (`polars` optional) | |
| Storage | Parquet (research) + DuckDB/PostgreSQL (SQL) | |
| RAG | LlamaIndex or LangChain + embeddings + vector store (FAISS/Chroma/pgvector) | Decide in Phase 3 |
| Agents | **LangGraph** (required) | Multi-agent orchestration |
| LLM provider | Anthropic / OpenAI — provider-agnostic wrapper | Config-swappable |
| MCP | Model Context Protocol Python SDK | Tool servers for agents |
| Eval | **RAGAS** (RAG) + **LangSmith** (tracing/eval) | Phase 6 |
| Backtesting | `vectorbt` or `backtrader` (or custom) | Decide in Phase 7 |
| Observability | LangSmith + Prometheus/Grafana or Streamlit dashboard | Phase 8 |
| API | FastAPI + uvicorn | Phase 8 |
| Tests | `pytest` + `pytest-cov` | Every phase |

*(These are recommendations. Anything marked "Decide" is a locked-in decision point.)*

## 5. Repository Layout (from original_tree idea, extended)
```
marketmind/
├── data/
│   ├── ingestion/     # IBKR API clients, historical + news fetch
│   ├── cleaning/      # normalization, missing/outlier handling
│   ├── validation/    # schema + sanity checks
│   └── storage/       # parquet writers, SQL loaders
├── database/          # schema, migrations, connections, research DB
├── strategies/        # signal & strategy definitions (technical/statistical)
├── backtesting/       # backtest engine + reports
├── agents/            # LangGraph graph + Quant/News/Fundamental/Risk agents
├── rag/               # embeddings, vector store, retrieval, chunking
├── evaluation/        # RAGAS + agent eval harness
├── monitoring/        # observability, dashboards, tracing hooks
├── api/               # FastAPI app, routes, schemas
├── mcp/               # MCP tool servers exposed to agents
├── tests/             # mirrors the above
├── configs/           # YAML/env configs
├── notebooks/         # exploratory research (not shipped logic)
└── pyproject.toml · README.md · ROADMAP.md
```

## 6. Phases, Milestones & Definition of Done

### Phase 0 — Foundation & Setup
- Init repo structure (layout above), `pyproject.toml`, virtual env.
- Config system (pydantic-settings + .env), structured logging, secrets handling.
- Testing harness (`pytest`), lint/format (ruff/black), pre-commit.
- CI skeleton; observability/tracing stub; `.gitignore`.
- **DoD:** `pytest` runs green on a smoke test; config loads; `README` + this roadmap committed.

### Phase 1 — Data Layer (Market Data → Data Engineering)
- IBKR connection (ib_insync): historical bars, news feed.
- Ingestion → cleaning → validation → storage pipeline for OHLCV + news.
- SQL database schema + research DB layout; migrations.
- **DoD:** reproducible pipeline pulls/stores data for a symbol universe; validation catches bad rows; unit + integration tests.

### Phase 2 — Quant Engine
- Technical signal library (trend, momentum, volatility, mean-reversion).
- Statistical analysis & testing module (significance, stationarity, correlation).
- Strategy abstraction to combine signals into candidate strategies.
- **DoD:** signals are pure, tested functions; stats module produces testable outputs; documented strategy interface.

### Phase 3 — RAG System
- Ingest financial documents (filings, notes); chunking + embeddings + vector store.
- Retrieval API with reranking; evaluation scaffolding.
- **DoD:** retrieval returns relevant context for sample queries; retrieval covered by tests; storage configurable.

### Phase 4 — Opportunity Engine
- Merge Quant signals + RAG context → ranked candidate opportunities.
- Scoring/ranking logic; explanation payload per candidate.
- **DoD:** given data + context, produces ranked, explainable candidates; fully tested.

### Phase 5 — Multi-Agent Research (LangGraph) + MCP
- LangGraph orchestration graph; Quant/News/Fundamental agents.
- MCP tool servers exposing data/quant/rag tools to agents.
- Risk Agent enforcing limits; Final Thesis synthesis node.
- **DoD:** a graph run turns candidates into a risk-checked thesis; state is traceable; agents use MCP tools; tests cover graph logic.

### Phase 6 — LLM & Agent Evaluation
- RAGAS for RAG quality; agent-output eval harness; golden datasets; LangSmith tracing.
- **DoD:** repeatable eval suite with scores; regressions detectable in CI.

### Phase 7 — Backtesting
- Backtest engine integrating strategies/theses; metrics (CAGR, Sharpe, drawdown, etc.).
- Report generation; walk-forward/robustness checks.
- **DoD:** theses backtested with reproducible results + reports; engine unit-tested; results feed Risk gating.

### Phase 8 — Paper Trading, API & Observability
- IBKR paper-trading execution with risk gates; order/position management.
- FastAPI layer to trigger runs/inspect theses.
- Monitoring dashboard + alerting.
- **DoD:** end-to-end loop (data → thesis → backtest → paper trade) runs on IBKR paper account; API serves key endpoints; metrics visible.

### Phase 9 — Hardening & Release
- Performance, error handling, retries, docs, deployment notes, security review.
- **DoD:** full loop stable & documented; coverage target met; release notes.

## 7. Progress Tracker
| Phase | Status | Notes |
|---|---|---|
| 0 — Foundation | ⬜ Not started | |
| 1 — Data Layer | ⬜ | |
| 2 — Quant Engine | ⬜ | |
| 3 — RAG System | ⬜ | |
| 4 — Opportunity Engine | ⬜ | |
| 5 — Agents + MCP | ⬜ | |
| 6 — Evaluation | ⬜ | |
| 7 — Backtesting | ⬜ | |
| 8 — Paper Trading/API/Observability | ⬜ | |
| 9 — Hardening | ⬜ | |

## 8. Open Decisions (lock during the phase noted)
- Env/dependency tool: `uv` vs `poetry` (Phase 0)
- LLM provider(s) & model defaults (Phase 5)
- RAG framework + vector store (Phase 3)
- Backtesting library (Phase 7)
- Universe of symbols & data granularity (Phase 1)
- Observability backend (Phase 8)

## 9. Definition of Done (global)
- Feature has unit tests (and integration tests where relevant) — green in CI.
- Config-driven; no hardcoded secrets/paths.
- Logs/traces wired; evaluation updated when agents/RAG change.
- Docs updated (README + this ROADMAP); Progress Tracker advanced.
- Committed with a clear message on the working branch.

---
_Last updated: 2026-10-09 · Maintained as the single source of truth for scope & progress._

