"""Data enrichment pipeline.

Provides the top-level function to fetch and enrich a list of symbols,
returning enriched data ready for the impact-trading engine.

Design principles:
  * Cache-first — checks DuckDB store before hitting IBKR API
  * Incremental — only fetches missing/new data
  * Type-safe — returns Pydantic models throughout
  * Integration-ready — results feed directly into compliance screener
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Dict, Any, Optional

from ..data.models import PriceBar, FundamentalData, ScreenResult, DataUpdate
from ..data.storage import DataStore
from ..compliance.rules import AssetClass

logger = logging.getLogger(__name__)


async def enrich_symbol(
    symbol: str,
    store: DataStore,
    ingestor: Optional["IBKRDataIngestor"] = None,
) -> Dict[str, Any]:
    """Fetch and cache all data needed for impact screening of one symbol.

    Parameters
    ----------
    symbol : str
        Ticker symbol to enrich (e.g., "AAPL").
    store : DataStore
        Cache-first data store with connected source.
    ingestor : IBKRDataIngestor, optional
        IBKR data ingestor connected to Gateway. If None, uses cached data
        from the store only.

    Returns
    -------
    dict
        Enriched data containing:
        - symbol: str
        - fundamentals: FundamentalData or None
        - latest_bar: PriceBar or None
        - contract_details: dict or None
        - screen_result: ScreenResult
    """
    from ..data.ingestion import IBKRDataIngestor as _IBKRDataIngestor
    
    # Check if we already have fundamentals cached
    existing_fund = store.get_fundamental_data(symbol)
    existing_bar = store.get_latest_price(symbol)

    # Fetch fundamentals if not cached
    if existing_fund is None:
        if ingestor is not None:
            logger.info("Fetching fundamentals for %s (cache miss)", symbol)
            fundamentals = await ingestor.ingest_fundamentals(symbol, store)
        else:
            logger.debug("No fundamentals cached for %s and no ingestor provided", symbol)
            fundamentals = None
    else:
        logger.debug("Using cached fundamentals for %s", symbol)
        fundamentals = existing_fund

    # Fetch latest price bar if not cached
    if existing_bar is None:
        if ingestor is not None:
            logger.info("Fetching price bars for %s (cache miss)", symbol)
            bars = await ingestor.ingest_price_bars(symbol, store=store)
            latest_bar = store.get_latest_price(symbol)
        else:
            logger.debug("No price bar cached for %s and no ingestor provided", symbol)
            latest_bar = None
    else:
        logger.debug("Using cached price bar for %s", symbol)
        latest_bar = existing_bar

    # Fetch contract details
    contract_details = {}
    if ingestor is not None:
        logger.debug("Fetching contract details for %s", symbol)
        contract_details = await ingestor.ingest_contract_details(symbol, store)

    # Determine asset class
    if fundamentals is not None and fundamentals.market_cap is not None:
        if fundamentals.market_cap < 1e8:  # under $100M
            asset_class = AssetClass.SPOT_METAL
        elif fundamentals.sector and "ETF" in fundamentals.sector.upper():
            asset_class = AssetClass.ETF
        else:
            asset_class = AssetClass.EQUITY
    else:
        asset_class = AssetClass.EQUITY

    # Create screen result
    from marketmind.data.models import ScreenResult
    from datetime import datetime as _dt

    debt_ratio = fundamentals.debt_to_market_cap if fundamentals else None
    passed = True
    reasons = []

    # Apply impact-screening rules
    if debt_ratio is not None and debt_ratio >= 0.33:
        passed = False
        reasons.append(f"debt_to_market_cap >= 33% ({debt_ratio:.2%})")

    # Sector exclusion would be checked here against compliance rules
    # For now, we just record the sector
    sector = fundamentals.sector if fundamentals else None

    screen_result = ScreenResult(
        symbol=symbol.upper(),
        asset_class=asset_class,
        passed=passed,
        reasons=reasons,
        sector=sector,
        debt_to_market_cap=debt_ratio,
        screened_at=_dt.utcnow(),
    )

    # Store the screen result
    store.insert_screen_result(screen_result)

    return {
        "symbol": symbol.upper(),
        "fundamentals": fundamentals,
        "latest_bar": latest_bar,
        "contract_details": contract_details,
        "screen_result": screen_result,
    }


async def enrich_symbols(
    symbols: List[str],
    store: DataStore,
    ingestor: Optional["IBKRDataIngestor"] = None,
) -> List[Dict[str, Any]]:
    """Enrich a list of symbols with fundamental + market data.

    Parameters
    ----------
    symbols : list[str]
        Ticker symbols to enrich.
    store : DataStore
        Cache-first data store with connected source.
    ingestor : IBKRDataIngestor, optional
        IBKR data ingestor connected to Gateway. If None, uses cached data
        from the store only.

    Returns
    -------
    list[dict]
        One enriched dict per symbol, each containing:
        symbol, fundamentals, latest_bar, contract_details, screen_result
    """
    results = []
    for symbol in symbols:
        try:
            enrichment = await enrich_symbol(symbol, store, ingestor)
            results.append(enrichment)
        except Exception as exc:
            logger.warning("Failed to enrich %s: %s", symbol, exc)
            results.append({
                "symbol": symbol.upper(),
                "error": str(exc),
            })
    return results
