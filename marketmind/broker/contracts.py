"""Currency-aware IBKR equity contracts.

Hardcoding ``Stock(symbol, "SMART", "USD")`` mis-resolves European tickers
(e.g. AIR → AAR Corp on NYSE instead of Airbus on SBF). Listing currency and
exchange come from stock specs first, then instrument meta, else USD/SMART.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional, Protocol

try:
    from ib_insync import Stock
except ImportError:  # pragma: no cover - unit tests may stub Stock
    Stock = None  # type: ignore[misc, assignment]


@dataclass(frozen=True)
class Listing:
    """How to ask IBKR for one equity."""

    symbol: str
    currency: str = "USD"
    exchange: str = "SMART"

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).strip().upper())
        object.__setattr__(
            self, "currency", str(self.currency or "USD").strip().upper() or "USD"
        )
        object.__setattr__(
            self, "exchange", str(self.exchange or "SMART").strip().upper() or "SMART"
        )


class SupportsListingLookup(Protocol):
    def resolve(self, symbol: str) -> Listing: ...


def make_stock(
    symbol: str,
    *,
    currency: str = "USD",
    exchange: str = "SMART",
):
    """Build an ib_insync ``Stock`` with the given listing currency/exchange."""
    if Stock is None:  # pragma: no cover
        raise RuntimeError("ib_insync is not available")
    listing = Listing(symbol, currency=currency, exchange=exchange)
    return Stock(listing.symbol, listing.exchange, listing.currency)


def listing_from_mapping(symbol: str, meta: Optional[Mapping[str, object]]) -> Listing:
    """Build a listing from a loose dict (instrument row, contract info, …)."""
    meta = meta or {}
    currency = meta.get("currency") or "USD"
    exchange = meta.get("exchange") or "SMART"
    return Listing(symbol, currency=str(currency), exchange=str(exchange))


class ListingBook:
    """Resolve per-symbol listings: explicit overrides → fallback USD/SMART.

    Callers load stock-spec currency/exchange into overrides, and may also
    seed from the ``instruments`` table. Specs win over instrument meta when
    both are registered (register specs after instruments, or use
    ``prefer_override=True`` which is the default for ``put``).
    """

    def __init__(self) -> None:
        self._listings: Dict[str, Listing] = {}

    def put(
        self,
        symbol: str,
        *,
        currency: str = "USD",
        exchange: str = "SMART",
        overwrite: bool = True,
    ) -> Listing:
        listing = Listing(symbol, currency=currency, exchange=exchange)
        key = listing.symbol
        if not overwrite and key in self._listings:
            return self._listings[key]
        self._listings[key] = listing
        return listing

    def load_mappings(self, rows: Mapping[str, Mapping[str, object]]) -> None:
        """Seed from ``{symbol: {currency, exchange}}`` without clobbering specs."""
        for symbol, meta in rows.items():
            self.put(
                symbol,
                currency=str(meta.get("currency") or "USD"),
                exchange=str(meta.get("exchange") or "SMART"),
                overwrite=False,
            )

    def load_specs(self, specs) -> None:
        """Register currency/exchange from file-driven stock specs (wins)."""
        for spec in specs:
            self.put(
                spec.ticker,
                currency=getattr(spec, "currency", "USD") or "USD",
                exchange=getattr(spec, "exchange", "SMART") or "SMART",
                overwrite=True,
            )

    def resolve(self, symbol: str) -> Listing:
        key = str(symbol or "").strip().upper()
        if key in self._listings:
            return self._listings[key]
        return Listing(key, currency="USD", exchange="SMART")

    def clear(self) -> None:
        self._listings.clear()

    def __contains__(self, symbol: object) -> bool:
        return str(symbol or "").strip().upper() in self._listings

    def __len__(self) -> int:
        return len(self._listings)


LookupFn = Callable[[str], Listing]
