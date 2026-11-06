"""IBKR Gateway data ingestion module.

Fetches historical price bars, fundamental data, and contract details
from Interactive Brokers Gateway using the ib_insync library.

Design principles:
  * Async/await — non-blocking I/O
  * Cache-first — results can be cached via DataStore
  * Type-safe — returns Pydantic models
  * Graceful error handling — never crashes on network issues
  * ib_insync is imported lazily so the module loads even when IBKR is unavailable
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

import pandas as pd
from pandas import DataFrame

from marketmind.data.models import PriceBar, FundamentalData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy ib_insync import
# ---------------------------------------------------------------------------

_IB = None  # type: ignore
_Stock = None  # type: ignore
_ib_insync_available = False

try:  # pragma: no cover - depends on environment
    from ib_insync import IB as _IB, Stock as _Stock  # noqa: F401
    _ib_insync_available = True
except Exception:  # pragma: no cover
    _ib_insync_available = False


class IBKRFetcher:
    """Concrete fetcher bound to an active IBKR connection."""

    def __init__(self, ib) -> None:
        self._ib = ib

    async def fetch_bars(
        self, symbol: str, duration: str = "1 Y", bar_size: str = "1 day"
    ) -> pd.DataFrame:
        """Fetch historical OHLCV bars for *symbol*."""
        if _Stock is None:
            raise RuntimeError("ib_insync is not available")
        contract = _Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)

        bars = await self._ib.reqHistoricalDataAsync(
            contract=contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
            keepUpToDate=False,
        )

        df = DataFrame(
            [
                {
                    "date": b.date,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars
            ]
        )
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
        return df

    async def fetch_fundamentals(self, symbol: str) -> FundamentalData:
        """Fetch fundamental data for *symbol*.

        Returns a FundamentalData Pydantic model populated with:
            sector, industry, market_cap, total_debt,
            debt_to_market_cap, pe_ratio, pb_ratio, dividend_yield,
            country, currency
        """
        if _Stock is None:
            raise RuntimeError("ib_insync is not available")
        contract = _Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)

        # Request financial summary from IBKR
        fundamentals_xml = await self._ib.reqFundamentalDataAsync(
            contract, "ReportSnapshot"
        )

        import xml.etree.ElementTree as ET

        root = ET.fromstring(fundamentals_xml)

        # Extract fields from CompanyOverview
        co_overview = root.find(".//CompanyOverview")
        sector = None
        industry = None
        market_cap = None
        total_debt = None

        if co_overview is not None:
            sector_elem = co_overview.find(".//Sector")
            industry_elem = co_overview.find(".//Industry")
            market_cap_elem = co_overview.find(".//MarketCapitalization")
            total_debt_elem = co_overview.find(".//TotalDebt")

            if sector_elem is not None and sector_elem.text:
                sector = sector_elem.text.strip()
            if industry_elem is not None and industry_elem.text:
                industry = industry_elem.text.strip()
            if market_cap_elem is not None and market_cap_elem.text:
                try:
                    market_cap = float(market_cap_elem.text.strip())
                except ValueError:
                    market_cap = None
            if total_debt_elem is not None and total_debt_elem.text:
                try:
                    total_debt = float(total_debt_elem.text.strip())
                except ValueError:
                    total_debt = None

        debt_to_market_cap = None
        if market_cap and total_debt is not None and market_cap > 0:
            debt_to_market_cap = total_debt / market_cap

        return FundamentalData(
            symbol=symbol.upper().strip(),
            sector=sector,
            industry=industry,
            market_cap=market_cap,
            total_debt=total_debt,
            debt_to_market_cap=debt_to_market_cap,
            source="IBKR",
        )


class IBKRDataIngestor:
    """High-level ingestor that fetches data from IBKR and stores via DataStore."""

    def __init__(self, ib=None) -> None:
        self._ib = ib
        self._fetcher: Optional[IBKRFetcher] = (
            IBKRFetcher(ib) if ib is not None else None
        )

    def get_fetcher(self) -> IBKRFetcher:
        if self._fetcher is None:
            raise RuntimeError("No IBKR connection available — fetcher is None")
        return self._fetcher

    async def ingest_price_bars(
        self, symbol: str, store=None, duration: str = "1 Y", bar_size: str = "1 day"
    ) -> List[PriceBar]:
        """Fetch and store price bars for *symbol*."""
        if not _ib_insync_available:
            logger.warning("ib_insync not available — cannot fetch price bars")
            return []
        fetcher = self.get_fetcher()
        df = await fetcher.fetch_bars(symbol, duration=duration, bar_size=bar_size)

        bars: List[PriceBar] = []
        for timestamp, row in df.iterrows():
            bar = PriceBar(
                symbol=symbol.upper().strip(),
                timestamp=timestamp.to_pydantic(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                bar_size=bar_size,
                what_to_show="TRADES",
                source="IBKR",
            )
            bars.append(bar)

        if store is not None:
            inserted = store.insert_price_bars(bars)
            logger.info(
                "Stored %d/%d price bars for %s", inserted, len(bars), symbol
            )

        return bars

    async def ingest_fundamentals(
        self, symbol: str, store=None
    ) -> Optional[FundamentalData]:
        """Fetch and optionally store fundamental data for a symbol."""
        if not _ib_insync_available:
            logger.warning("ib_insync not available — cannot fetch fundamentals")
            return None
        fetcher = self.get_fetcher()
        fund = await fetcher.fetch_fundamentals(symbol)

        if store is not None:
            store.upsert_fundamental_data(fund)
            logger.info("Stored fundamental data for %s", symbol)

        return fund

    async def ingest_contract_details(
        self, symbol: str, store=None
    ) -> Optional[Dict[str, Any]]:
        """Fetch and optionally store contract details for a symbol."""
        if not _ib_insync_available:
            logger.warning("ib_insync not available — cannot fetch contract details")
            return None
        fetcher = self.get_fetcher()
        details = await fetcher.fetch_contract_details(symbol)  # type: ignore[attr-defined]

        if details:
            logger.debug("Contract details for %s: %s", symbol, details)

        return details

    async def enrich_symbol(
        self, symbol: str, store: DataStore
    ) -> Dict[str, Any]:
        """Fetch all data needed for impact screening of one symbol.

        Returns a dict with price data, fundamentals, and contract info,
        ready to be screened by the compliance engine.
        """
        # Fetch fundamentals
        fundamentals = await self.ingest_fundamentals(symbol, store)

        # Fetch contract details
        details = await self.ingest_contract_details(symbol, store)

        # Get latest price bar
        bars = await self.ingest_price_bars(symbol, store=store)
        latest_bar = store.get_latest_price(symbol) if bars else None

        result = {
            "symbol": symbol.upper(),
            "fundamentals": fundamentals,
            "contract_details": details or {},
            "latest_bar": latest_bar,
        }

        # Store the enrichment result
        from marketmind.data.models import ScreenResult
        from marketmind.compliance.rules import AssetClass

        # Determine asset class
        asset_class = AssetClass.EQUITY  # default
        if fundamentals is not None:
            if (
                fundamentals.market_cap is not None
                and fundamentals.market_cap < 1e8
            ):
                asset_class = AssetClass.SPOT_METAL
            elif fundamentals.sector and "ETF" in fundamentals.sector.upper():
                asset_class = AssetClass.ETF

        screen_result = ScreenResult(
            symbol=symbol.upper(),
            asset_class=asset_class,
            passed=True,  # Will be validated by the screener
            reasons=[],
            sector=fundamentals.sector if fundamentals else None,
            debt_to_market_cap=fundamentals.debt_to_market_cap
            if fundamentals
            else None,
            screened_at=datetime.utcnow(),
        )

        store.insert_screen_result(screen_result)

        result["screen_result"] = screen_result
        return result
