"""File-driven stock spec loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketmind.stock_specs.loader import (
    default_stocks_dir,
    load_stock_spec,
    load_stock_specs,
    stock_spec_from_payload,
)


def test_default_stocks_dir_has_seed_universe() -> None:
    specs = load_stock_specs(default_stocks_dir())
    tickers = {spec.ticker for spec in specs}
    assert {"AAPL", "MSFT", "NVDA", "TSLA", "JPM", "SPUS", "HLAL"} <= tickers


def test_seed_aapl_matches_incumbent_defaults() -> None:
    spec = load_stock_spec(default_stocks_dir() / "AAPL.json")
    assert spec.ticker == "AAPL"
    assert spec.enabled is True
    assert spec.entry == "m12"
    assert spec.atr_multiple == pytest.approx(2.0)
    assert spec.atr_period == 20
    assert spec.target_r is None
    assert spec.fixed_take_profit_pct is None
    assert spec.fixed_stop_pct is None


def test_nvda_has_per_stock_target() -> None:
    spec = load_stock_spec(default_stocks_dir() / "NVDA.json")
    assert spec.atr_multiple == pytest.approx(2.5)
    assert spec.target_r == pytest.approx(2.0)


def test_enabled_only_skips_disabled(tmp_path: Path) -> None:
    (tmp_path / "AAA.json").write_text(
        json.dumps({
            "ticker": "AAA",
            "enabled": True,
            "signal": {"entry": "m12"},
            "exits": {"atr_multiple": 2.0},
        }),
        encoding="utf-8",
    )
    (tmp_path / "BBB.json").write_text(
        json.dumps({
            "ticker": "BBB",
            "enabled": False,
            "signal": {"entry": "m6"},
            "exits": {"atr_multiple": 1.5},
        }),
        encoding="utf-8",
    )
    all_specs = load_stock_specs(tmp_path)
    assert {s.ticker for s in all_specs} == {"AAA", "BBB"}
    enabled = load_stock_specs(tmp_path, enabled_only=True)
    assert [s.ticker for s in enabled] == ["AAA"]


def test_duplicate_ticker_raises(tmp_path: Path) -> None:
    payload = {
        "ticker": "DUP",
        "signal": {"entry": "trend"},
        "exits": {"atr_multiple": 2.0},
    }
    (tmp_path / "a.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate ticker"):
        load_stock_specs(tmp_path)


def test_unknown_entry_raises() -> None:
    with pytest.raises(ValueError, match="unknown signal entry"):
        stock_spec_from_payload({
            "ticker": "ZZZ",
            "signal": {"entry": "magic"},
            "exits": {"atr_multiple": 2.0},
        })


def test_fixed_pct_exits_parse() -> None:
    spec = stock_spec_from_payload({
        "ticker": "XYZ",
        "signal": {"entry": "breakout", "formation": 20},
        "exits": {
            "atr_multiple": 1.5,
            "fixed_take_profit_pct": 0.05,
            "fixed_stop_pct": 0.03,
            "max_hold": 63,
        },
    })
    assert spec.entry == "breakout"
    assert spec.formation == 20
    assert spec.fixed_take_profit_pct == pytest.approx(0.05)
    assert spec.fixed_stop_pct == pytest.approx(0.03)
    assert spec.max_hold == 63


def test_missing_stocks_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_stock_specs(tmp_path / "missing")
