"""Book limit and the SEC filing parser. Neither test touches the network."""

from __future__ import annotations

from marketmind.data import DataStore
from marketmind.filings import (
    apply_to_screen,
    asset_growth,
    debt_ratio,
    gross_profitability,
    picture_from_payloads,
    sector_from_sic,
    total_debt,
)
from marketmind.risk_gate import admit


def _facts():
    def usd(tag_points):
        return {"units": {"USD": tag_points}}

    annual = [
        {"end": "2024-09-28", "val": 300.0, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
        {"end": "2025-09-27", "val": 360.0, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
    ]
    # A quarterly point must not be treated as a year.
    quarterly = {"end": "2025-12-27", "val": 999.0, "fp": "Q1", "form": "10-Q", "filed": "2026-02-01"}
    return {"facts": {"us-gaap": {
        "Assets": usd(annual + [quarterly]),
        "GrossProfit": usd([
            {"end": "2024-09-28", "val": 120.0, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
            {"end": "2025-09-27", "val": 180.0, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
        ]),
        "LongTermDebtNoncurrent": usd([
            {"end": "2025-09-27", "val": 70.0, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
        ]),
        "LongTermDebtCurrent": usd([
            {"end": "2025-09-27", "val": 10.0, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
        ]),
        "CommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2025-09-27", "val": 20.0, "form": "10-K", "filed": "2025-11-01"},
        ]}},
    }}}


def test_debt_profitability_and_asset_growth_use_the_annual_report() -> None:
    facts = _facts()
    assert total_debt(facts) == 80.0
    assert gross_profitability(facts) == 180.0 / 360.0
    assert asset_growth(facts) == (360.0 / 300.0) - 1.0
    assert debt_ratio(80.0, 20.0, 10.0) == 80.0 / 200.0


def test_long_term_debt_total_is_preferred_when_the_filer_reports_it() -> None:
    facts = _facts()
    facts["facts"]["us-gaap"]["LongTermDebt"] = {"units": {"USD": [
        {"end": "2025-09-27", "val": 80.0, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
    ]}}
    assert total_debt(facts) == 80.0


def test_sic_maps_only_the_excluded_businesses() -> None:
    assert sector_from_sic(3571, "Electronic Computers") is None
    assert sector_from_sic(6021, "National Commercial Banks") == "financial_services"
    assert sector_from_sic(2111, "Cigarettes") == "tobacco"
    assert sector_from_sic(2082, "Malt Beverages") == "alcohol"
    assert sector_from_sic(3484, "Small Arms") == "weapons"
    assert sector_from_sic(7993, "Coin-Operated Amusement") is None
    assert sector_from_sic(7999, "Gambling") == "gambling"


def test_a_typed_ratio_is_not_overwritten_by_a_filing(tmp_path) -> None:
    store = DataStore(tmp_path / "lab.db")
    picture = picture_from_payloads(
        "AAPL", {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        {"sic": "3571", "sicDescription": "Electronic Computers", "name": "Apple Inc."},
        _facts(),
    )
    assert picture.sector is None
    assert picture.gross_profitability == 0.5
    from marketmind.market_data import MarketDataService
    market = MarketDataService(store)
    market.set_fundamental("AAPL", sector="Technology", debt_to_market_cap=0.12)
    assert apply_to_screen(store, picture, price=10.0) == "kept"
    assert store.get_fundamental_data("AAPL").debt_to_market_cap == 0.12
    assert store.get_fundamental_data("AAPL").source == "COCKPIT"
    store.close()


def test_a_missing_ratio_is_filled_from_the_filing(tmp_path) -> None:
    store = DataStore(tmp_path / "lab.db")
    picture = picture_from_payloads(
        "JPM", {"cik_str": 19617, "ticker": "JPM", "title": "JPMorgan Chase & Co."},
        {"sic": "6021", "sicDescription": "National Commercial Banks"},
        _facts(),
    )
    assert picture.sector == "financial_services"
    assert apply_to_screen(store, picture, price=10.0) == "saved"
    saved = store.get_fundamental_data("JPM")
    assert saved.source == "EDGAR"
    assert saved.debt_to_market_cap == 80.0 / 200.0
    assert saved.sector == "financial_services"
    store.close()


def test_the_book_stops_at_four_names_and_at_two_percent() -> None:
    full = admit(100_000, 4, 1_000, 500)
    assert full.allowed is False
    assert "4" in full.reason
    too_much = admit(100_000, 1, 1_500, 600)
    assert too_much.allowed is False
    room = admit(100_000, 1, 500, 500)
    assert room.allowed is True
    assert admit(0, 0, 0, 100).allowed is False
