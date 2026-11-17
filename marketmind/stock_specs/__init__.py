"""File-driven per-stock specs (ticker, signal, TP/SL params)."""

from marketmind.stock_specs.loader import (
    StockSpec,
    default_stocks_dir,
    load_stock_spec,
    load_stock_specs,
)

__all__ = [
    "StockSpec",
    "default_stocks_dir",
    "load_stock_spec",
    "load_stock_specs",
]
