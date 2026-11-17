"""Load and apply the shared ethics / exclusion filter file.

The filter file (``ethics/exclusions.json``) lists canonical excluded sector
ids plus human/IB aliases. Matching is normalized so ``Financial Services``,
``financial_services``, and ``Financials`` resolve the same way.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def normalize_sector_label(value: str) -> str:
    """Lowercase and collapse punctuation/whitespace to snake_case tokens."""
    return _TOKEN_RE.sub("_", value.lower().strip()).strip("_")


def default_ethics_path() -> Path:
    """Repo-root ``ethics/exclusions.json`` (next to ``marketmind/``)."""
    return Path(__file__).resolve().parents[2] / "ethics" / "exclusions.json"


@dataclass(frozen=True)
class EthicsFilter:
    """In-memory view of the shared exclusion file."""

    excluded_sector_ids: frozenset[str]
    sector_aliases: Mapping[str, str]  # normalized alias -> canonical id
    max_debt_to_market_cap: float
    whitelisted_etfs: frozenset[str]
    whitelisted_metals: frozenset[str]
    source_path: Optional[Path] = None
    version: int = 1
    _extra: Dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def is_excluded_sector(self, sector: Optional[str]) -> bool:
        """True when *sector* matches a canonical id or any of its aliases."""
        if sector is None:
            return False
        key = normalize_sector_label(sector)
        if not key:
            return False
        if key in self.excluded_sector_ids:
            return True
        return key in self.sector_aliases

    def matched_sector_id(self, sector: Optional[str]) -> Optional[str]:
        """Return the canonical excluded id for *sector*, or None."""
        if sector is None:
            return None
        key = normalize_sector_label(sector)
        if key in self.excluded_sector_ids:
            return key
        return self.sector_aliases.get(key)

    def is_debt_ratio_compliant(self, debt_to_market_cap: Optional[float]) -> bool:
        if debt_to_market_cap is None:
            return False
        return debt_to_market_cap < self.max_debt_to_market_cap

    def is_whitelisted_etf(self, symbol: str) -> bool:
        return symbol.upper().strip() in self.whitelisted_etfs

    def is_whitelisted_metal(self, symbol: str) -> bool:
        return symbol.upper().strip() in self.whitelisted_metals


def _build_alias_map(sectors: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for entry in sectors:
        sector_id = str(entry["id"]).strip()
        canon = normalize_sector_label(sector_id)
        aliases[canon] = sector_id
        for raw in entry.get("aliases", []) or []:
            aliases[normalize_sector_label(str(raw))] = sector_id
    return aliases


def ethics_filter_from_payload(
    payload: Mapping[str, Any],
    *,
    source_path: Optional[Path] = None,
) -> EthicsFilter:
    """Build an ``EthicsFilter`` from a decoded JSON object."""
    sectors = list(payload.get("excluded_sectors") or [])
    ids = frozenset(str(entry["id"]).strip() for entry in sectors)
    etfs = frozenset(
        str(symbol).upper().strip()
        for symbol in (payload.get("whitelisted_etfs") or [])
    )
    metals = frozenset(
        str(symbol).upper().strip()
        for symbol in (payload.get("whitelisted_metals") or [])
    )
    max_debt = float(payload.get("max_debt_to_market_cap", 0.33))
    return EthicsFilter(
        excluded_sector_ids=ids,
        sector_aliases=_build_alias_map(sectors),
        max_debt_to_market_cap=max_debt,
        whitelisted_etfs=etfs,
        whitelisted_metals=metals,
        source_path=source_path,
        version=int(payload.get("version", 1)),
    )


def load_ethics_filter(path: Optional[Path | str] = None) -> EthicsFilter:
    """Load the shared filter from disk (defaults to repo ``ethics/exclusions.json``)."""
    resolved = Path(path) if path is not None else default_ethics_path()
    with resolved.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Ethics filter must be a JSON object: {resolved}")
    return ethics_filter_from_payload(payload, source_path=resolved.resolve())


@lru_cache(maxsize=4)
def _cached_default_filter(path_key: str) -> EthicsFilter:
    return load_ethics_filter(Path(path_key))


def default_ethics_filter() -> EthicsFilter:
    """Cached load of the repo default filter file."""
    return _cached_default_filter(str(default_ethics_path().resolve()))


def clear_ethics_filter_cache() -> None:
    """Drop the cached default filter (tests that rewrite the file)."""
    _cached_default_filter.cache_clear()


def excluded_sector_ids(filter_: Optional[EthicsFilter] = None) -> frozenset[str]:
    """Canonical excluded sector ids from the shared filter."""
    active = filter_ or default_ethics_filter()
    return active.excluded_sector_ids


def iter_sector_aliases(filter_: Optional[EthicsFilter] = None) -> Iterable[str]:
    active = filter_ or default_ethics_filter()
    return active.sector_aliases.keys()
