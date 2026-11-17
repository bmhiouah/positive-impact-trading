"""Shared positive-impact ethics / exclusion filter (file-driven)."""

from marketmind.ethics.filter import (
    EthicsFilter,
    default_ethics_path,
    load_ethics_filter,
    normalize_sector_label,
)

__all__ = [
    "EthicsFilter",
    "default_ethics_path",
    "load_ethics_filter",
    "normalize_sector_label",
]
