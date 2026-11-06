"""Cockpit state management for the paper-trading lab.

The trading runner lives in a background thread and must survive Streamlit
script reruns and browser reconnects. It is therefore held in a process-wide
``st.cache_resource`` object instead of ``st.session_state``.

``start_algo`` is deliberately simple and synchronous:

    stop previous run -> connect to IBKR (mandatory) -> read capital -> trade

No asyncio, no ``run_until_complete``, no "fake running" flags, and no
connection settings — the lab always uses the one fixed paper session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import streamlit as st

from marketmind import config
from marketmind.data import DataStore
from marketmind.journal import PaperJournal
from marketmind.live_trading import LiveTradingRunner

logger = logging.getLogger(__name__)


@dataclass
class CockpitState:
    """Shared cockpit state (one instance per Streamlit server process)."""

    algo_running: bool = False
    algo_runner: Optional[Any] = None
    algo_last_result: Optional[Dict[str, Any]] = None
    algo_last_error: Optional[str] = None
    ibkr_connected: bool = False
    ibkr_last_error: Optional[str] = None
    ibkr_account: Optional[str] = None
    ibkr_server_version: Optional[int] = None
    cycle_count: int = 0
    last_cycle_at: Optional[str] = None
    db_path: str = config.DB_PATH
    live_snapshot: Optional[Any] = field(default=None, repr=False)
    snapshot_at: Optional[str] = field(default=None, repr=False)
    refresh_error: Optional[str] = None


@st.cache_resource
def _global_state() -> CockpitState:
    """Process-wide state, shared by every session/rerun."""
    return CockpitState()


@st.cache_resource
def get_store() -> DataStore:
    """One DataStore per Streamlit process (thread-local SQLite connections)."""
    return DataStore(config.DB_PATH)


@st.cache_resource
def get_journal() -> PaperJournal:
    """The single paper-trading journal used by every page."""
    return PaperJournal(get_store())


def get_state() -> CockpitState:
    """Return the shared CockpitState, initializing it if needed."""
    if "cockpit_state" not in st.session_state:
        st.session_state.cockpit_state = _global_state()
    return st.session_state.cockpit_state


def sync_status(state: Optional[CockpitState] = None) -> CockpitState:
    """Refresh displayed status from the live runner (called on every rerun)."""
    state = state or get_state()
    runner = state.algo_runner
    if runner is None:
        state.algo_running = False
        state.ibkr_connected = False
        return state

    try:
        state.algo_running = bool(runner.is_running())
        state.ibkr_connected = bool(runner.is_connected())
        state.algo_last_result = runner.last_result or state.algo_last_result
        state.algo_last_error = runner.last_error
        state.cycle_count = runner.cycle_count
        state.last_cycle_at = runner.last_cycle_at
        info = runner.adapter.connection_info
        if info is not None:
            state.ibkr_account = info.account
            state.ibkr_server_version = info.server_version
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not sync runner status: %s", exc)
    return state


def stop_algo() -> None:
    """Stop the trading thread and close the IBKR API session cleanly."""
    state = get_state()
    runner = state.algo_runner
    if runner is not None:
        try:
            runner.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while stopping algo: %s", exc)
            state.ibkr_last_error = str(exc)

    state.algo_runner = None
    state.algo_running = False
    state.ibkr_connected = False
    state.algo_last_result = None
    state.algo_last_error = None
    state.cycle_count = 0
    state.last_cycle_at = None
    state.ibkr_account = None
    state.ibkr_server_version = None


def start_algo(
    db_path: str = config.DB_PATH,
    poll_interval: float = config.POLL_INTERVAL,
) -> None:
    """Connect to IBKR Gateway, read capital from the API, then start the algo.

    Connection is MANDATORY and happens BEFORE the trading thread is created.
    On failure, state shows the real error and the algo stays stopped.
    """
    state = get_state()
    stop_algo()  # full teardown so no stale session can poison the new one

    runner = LiveTradingRunner(
        db_path=db_path,
        poll_interval=poll_interval,
    )

    try:
        info = runner.start()
    except Exception as exc:  # noqa: BLE001
        try:
            runner.stop()
        except Exception:  # noqa: BLE001
            pass
        state.algo_runner = None
        state.algo_running = False
        state.ibkr_connected = False
        state.ibkr_last_error = str(exc)
        state.algo_last_error = str(exc)
        logger.error("Start Algo failed: %s", exc)
        return

    state.algo_runner = runner
    state.algo_running = True
    state.ibkr_connected = True
    state.ibkr_last_error = None
    state.algo_last_error = None
    state.algo_last_result = None
    state.cycle_count = 0
    state.ibkr_account = info["account"]
    state.ibkr_server_version = info["server_version"]
    state.db_path = db_path
    logger.info("Start Algo succeeded: %s", info)


def refresh_snapshot() -> bool:
    """Read a fresh account snapshot without starting the algo.

    Opens the fixed client-id-1 session for a few seconds, writes the
    observation to the journal, and disconnects. Only allowed while the algo
    is stopped, since there can be only one connection at a time.
    """
    from datetime import datetime, timezone

    from marketmind.broker import IBKRAdapter

    state = get_state()
    if state.algo_running:
        state.refresh_error = "Stop the algo first — only one connection is allowed."
        return False

    adapter = IBKRAdapter()
    try:
        adapter.connect()
        snapshot = adapter.get_account_snapshot()
        journal = get_journal()
        journal.ensure_baseline(snapshot)
        journal.record(snapshot)
        state.live_snapshot = snapshot
        state.snapshot_at = datetime.now(timezone.utc).isoformat()
        state.ibkr_account = snapshot.account_id
        state.refresh_error = None
        return True
    except Exception as exc:  # noqa: BLE001
        state.refresh_error = str(exc)
        logger.error("Snapshot refresh failed: %s", exc)
        return False
    finally:
        adapter.disconnect()
