"""Paper-lab data layer: typed models, SQLite storage, screening and ingestion.

``storage.py`` / ``models.py`` / ``screener.py`` back the live MVP.
``ingestion.py`` and ``enrichment.py`` hold the IBKR historical-data pipeline
that will feed the signal loop once price data is loaded (not wired in yet).
"""

from .models import PriceBar, FundamentalData, ScreenResult, DataUpdate
from .storage import DataStore
from .ingestion import IBKRDataIngestor
from .screener import DataIntegratedScreener, screen_symbol
from .enrichment import enrich_symbol, enrich_symbols

__all__ = [
    "PriceBar",
    "FundamentalData",
    "ScreenResult",
    "DataUpdate",
    "DataStore",
    "IBKRDataIngestor",
    "DataIntegratedScreener",
    "screen_symbol",
    "enrich_symbol",
    "enrich_symbols",
]
