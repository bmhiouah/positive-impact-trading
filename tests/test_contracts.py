"""Currency-aware IB Stock listings (no Gateway)."""

from __future__ import annotations

from marketmind.broker.contracts import Listing, ListingBook, make_stock
from marketmind.stock_specs.loader import stock_spec_from_payload


def test_listing_normalizes_fields() -> None:
    listing = Listing(" air ", currency="eur", exchange="smart")
    assert listing.symbol == "AIR"
    assert listing.currency == "EUR"
    assert listing.exchange == "SMART"


def test_make_stock_uses_currency() -> None:
    contract = make_stock("AIR", currency="EUR", exchange="SMART")
    assert contract.symbol == "AIR"
    assert contract.currency == "EUR"
    assert contract.exchange == "SMART"


def test_usd_default_still_works() -> None:
    contract = make_stock("AAPL")
    assert contract.currency == "USD"
    assert contract.exchange == "SMART"


def test_listing_book_specs_override_instrument_meta() -> None:
    book = ListingBook()
    book.load_mappings({"AIR": {"currency": "USD", "exchange": "NYSE"}})
    assert book.resolve("AIR").currency == "USD"
    book.load_specs([
        stock_spec_from_payload({
            "ticker": "AIR",
            "currency": "EUR",
            "exchange": "SMART",
            "signal": {"entry": "m12"},
            "exits": {"atr_multiple": 2.0},
        })
    ])
    listing = book.resolve("AIR")
    assert listing.currency == "EUR"
    assert listing.exchange == "SMART"


def test_unknown_symbol_defaults_to_usd_smart() -> None:
    book = ListingBook()
    listing = book.resolve("ZZZ")
    assert listing.currency == "USD"
    assert listing.exchange == "SMART"


def test_adapter_stock_uses_listing_book() -> None:
    from marketmind.broker.ib_adapter import IBKRAdapter

    adapter = IBKRAdapter()
    adapter.listings.put("AIR", currency="EUR", exchange="SMART")
    contract = adapter._stock("AIR")
    assert contract.currency == "EUR"
    assert contract.symbol == "AIR"
    # Unlisted seed name stays USD.
    aapl = adapter._stock("AAPL")
    assert aapl.currency == "USD"
