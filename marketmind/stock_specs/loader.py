"""Load one-file-per-stock specs from a folder.

Each ``*.json`` file describes a ticker, optional enable flag, signal rule,
and per-stock exit parameters (ATR multiple plus optional fixed % targets).
Adding a name is dropping a file in ``stocks/`` — not changing runner code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from marketmind import config

_KNOWN_ENTRIES = frozenset({"m12", "m6", "m3", "trend", "breakout", "dip"})


@dataclass(frozen=True)
class StockSpec:
    """One stock's file-driven parameters."""

    ticker: str
    enabled: bool = True
    currency: str = "USD"
    exchange: str = "SMART"
    entry: str = "m12"
    formation: Optional[int] = None
    skip: Optional[int] = None
    atr_period: int = 20
    atr_multiple: float = 2.0
    target_r: Optional[float] = None
    fixed_take_profit_pct: Optional[float] = None
    fixed_stop_pct: Optional[float] = None
    max_hold: Optional[int] = None
    notes: str = ""
    source_path: Optional[Path] = None

    @property
    def symbol(self) -> str:
        return self.ticker


def default_stocks_dir() -> Path:
    """Repo-root ``stocks/`` folder (sibling of ``marketmind/``)."""
    return Path(__file__).resolve().parents[2] / "stocks"


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def stock_spec_from_payload(
    payload: Mapping[str, Any],
    *,
    source_path: Optional[Path] = None,
) -> StockSpec:
    """Build a ``StockSpec`` from a decoded JSON object."""
    ticker = str(payload.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("Stock spec requires a non-empty 'ticker'")

    signal = payload.get("signal") or {}
    exits = payload.get("exits") or payload.get("risk") or {}
    if not isinstance(signal, Mapping):
        raise ValueError(f"{ticker}: 'signal' must be an object")
    if not isinstance(exits, Mapping):
        raise ValueError(f"{ticker}: 'exits' must be an object")

    entry = str(signal.get("entry") or signal.get("rule") or "m12").strip().lower()
    if entry not in _KNOWN_ENTRIES:
        raise ValueError(
            f"{ticker}: unknown signal entry {entry!r}; "
            f"expected one of {sorted(_KNOWN_ENTRIES)}"
        )

    atr_multiple = float(
        exits.get("atr_multiple", getattr(config, "ATR_MULTIPLE", 2.0))
    )
    if atr_multiple <= 0:
        raise ValueError(f"{ticker}: atr_multiple must be positive")

    atr_period = int(exits.get("atr_period", getattr(config, "ATR_PERIOD", 20)))
    if atr_period <= 0:
        raise ValueError(f"{ticker}: atr_period must be positive")

    fixed_tp = _optional_float(exits.get("fixed_take_profit_pct"))
    fixed_sl = _optional_float(exits.get("fixed_stop_pct"))
    if fixed_tp is not None and fixed_tp <= 0:
        raise ValueError(f"{ticker}: fixed_take_profit_pct must be positive")
    if fixed_sl is not None and fixed_sl <= 0:
        raise ValueError(f"{ticker}: fixed_stop_pct must be positive")

    return StockSpec(
        ticker=ticker,
        enabled=bool(payload.get("enabled", True)),
        currency=str(payload.get("currency") or "USD").upper(),
        exchange=str(payload.get("exchange") or "SMART").upper(),
        entry=entry,
        formation=_optional_int(signal.get("formation")),
        skip=_optional_int(signal.get("skip")),
        atr_period=atr_period,
        atr_multiple=atr_multiple,
        target_r=_optional_float(exits.get("target_r")),
        fixed_take_profit_pct=fixed_tp,
        fixed_stop_pct=fixed_sl,
        max_hold=_optional_int(exits.get("max_hold")),
        notes=str(payload.get("notes") or ""),
        source_path=source_path,
    )


def load_stock_spec(path: Path | str) -> StockSpec:
    """Load a single stock spec JSON file."""
    resolved = Path(path)
    with resolved.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Stock spec must be a JSON object: {resolved}")
    return stock_spec_from_payload(payload, source_path=resolved.resolve())


def load_stock_specs(
    directory: Optional[Path | str] = None,
    *,
    enabled_only: bool = False,
) -> List[StockSpec]:
    """Load every ``*.json`` stock file from *directory* (sorted by ticker).

    Duplicate tickers raise ``ValueError``. Non-JSON files are ignored.
    """
    root = Path(directory) if directory is not None else default_stocks_dir()
    if not root.is_dir():
        raise FileNotFoundError(f"Stock specs folder not found: {root}")

    specs: List[StockSpec] = []
    seen: Dict[str, Path] = {}
    for path in sorted(root.glob("*.json")):
        spec = load_stock_spec(path)
        if spec.ticker in seen:
            raise ValueError(
                f"Duplicate ticker {spec.ticker}: {seen[spec.ticker]} and {path}"
            )
        seen[spec.ticker] = path
        if enabled_only and not spec.enabled:
            continue
        specs.append(spec)
    return specs


def specs_by_ticker(
    specs: Sequence[StockSpec],
) -> Dict[str, StockSpec]:
    """Index specs by ticker symbol."""
    return {spec.ticker: spec for spec in specs}


def iter_enabled(specs: Iterable[StockSpec]) -> List[StockSpec]:
    return [spec for spec in specs if spec.enabled]
