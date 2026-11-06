"""Data models for the market data layer.

All models use Pydantic for validation and serialization. They represent
the canonical schema for market data stored in SQLite and passed between
ingestion, enrichment, and screening components.
"""

from __future__ import annotations

from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict

from marketmind.compliance.rules import AssetClass


def _utc_naive() -> datetime:
    """Naive UTC now — ``datetime.utcnow()`` is deprecated on Python 3.14."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PriceBar(BaseModel):
    """A single OHLCV price bar for a symbol."""

    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(..., min_length=1, description="Ticker symbol, uppercased")
    timestamp: datetime = Field(..., description="Bar timestamp (UTC)")
    open: float = Field(..., gt=0, description="Open price")
    high: float = Field(..., gt=0, description="High price")
    low: float = Field(..., gt=0, description="Low price")
    close: float = Field(..., gt=0, description="Close price")
    volume: int = Field(..., ge=0, description="Volume")
    vwap: Optional[float] = Field(None, gt=0, description="Volume-weighted average price")
    bar_size: str = Field("1 day", description="Bar size (1 min, 5 mins, 1 day, etc.)")
    what_to_show: str = Field("ADJUSTED_LAST", description="Price type (TRADES, ADJUSTED_LAST, etc.)")
    source: str = Field("IBKR", description="Data source")

    @property
    def symbol_upper(self) -> str:
        return self.symbol.upper().strip()


class FundamentalData(BaseModel):
    """Fundamental data for compliance screening (debt ratio, sector, etc.)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(..., min_length=1, description="Ticker symbol, uppercased")
    sector: Optional[str] = Field(None, description="Business sector")
    industry: Optional[str] = Field(None, description="Industry sub-sector")
    market_cap: Optional[float] = Field(None, ge=0, description="Market capitalization")
    total_debt: Optional[float] = Field(None, ge=0, description="Total debt")
    debt_to_market_cap: Optional[float] = Field(
        None, ge=0, description="Total debt / market cap ratio"
    )
    pe_ratio: Optional[float] = Field(None, ge=0, description="Price-to-earnings ratio")
    pb_ratio: Optional[float] = Field(None, ge=0, description="Price-to-book ratio")
    dividend_yield: Optional[float] = Field(None, ge=0, description="Dividend yield")
    country: Optional[str] = Field(None, description="Country of incorporation")
    currency: str = Field("USD", description="Trading currency")
    last_updated: datetime = Field(
        default_factory=_utc_naive, description="When fundamentals were last fetched"
    )
    source: str = Field("IBKR", description="Data source")

    @property
    def symbol_upper(self) -> str:
        return self.symbol.upper().strip()

    @property
    def is_debt_ratio_compliant(self) -> bool:
        """True if debt-to-market-cap is under 33% (impact-trading constraint)."""
        if self.debt_to_market_cap is None:
            return False
        return self.debt_to_market_cap < 0.33


class ScreenResult(BaseModel):
    """Result of running the compliance screen on a symbol."""

    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(..., min_length=1)
    asset_class: AssetClass
    passed: bool
    reasons: List[str] = Field(default_factory=list)
    sector: Optional[str] = None
    debt_to_market_cap: Optional[float] = None
    screened_at: datetime = Field(default_factory=_utc_naive)


class DataUpdate(BaseModel):
    """Record of a data update operation for auditability."""

    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str
    update_type: str = Field(..., description="price, fundamental, screen, etc.")
    records_inserted: int = Field(..., ge=0)
    started_at: datetime = Field(default_factory=_utc_naive)
    completed_at: Optional[datetime] = None
    status: str = Field("success", description="success, partial, failed")
    error_message: Optional[str] = None
    source: str = "IBKR"


# ---------------------------------------------------------------------------
# Account & Portfolio Models
# ---------------------------------------------------------------------------


class AccountSnapshot(BaseModel):
    """A point-in-time snapshot of account values from IBKR."""

    model_config = ConfigDict(str_strip_whitespace=True)

    timestamp_utc: datetime = Field(default_factory=_utc_naive)
    account_id: Optional[str] = Field(None, description="IBKR account number")
    net_liquidation: float = Field(..., description="Net liquidation value (total portfolio value)")
    total_cash_value: float = Field(0.0, description="Total cash value")
    cash_balance: float = Field(0.0, description="Cash balance")
    buying_power: float = Field(0.0, description="Buying power available")
    available_funds: float = Field(0.0, description="Available funds")
    excess_liquidity: float = Field(0.0, description="Excess liquidity (margin cushion)")
    gross_position_value: float = Field(0.0, description="Gross value of all positions")
    unrealized_pnl: float = Field(0.0, description="Total unrealized P&L")
    realized_pnl: float = Field(0.0, description="Total realized P&L (today)")
    total_positions_value: float = Field(0.0, description="Total market value of positions")
    accrued_cash: float = Field(0.0, description="Accrued interest/dividends on idle cash")
    currency: str = Field("USD", description="Account base currency reported by IBKR")
    source: str = Field("IBKR", description="Data source")


class PositionRecord(BaseModel):
    """A single position held in the account."""

    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(..., min_length=1)
    con_id: Optional[int] = Field(None, description="IBKR contract ID")
    exchange: Optional[str] = Field(None, description="Exchange (SMART, NYSE, etc.)")
    currency: str = Field("USD", description="Trading currency")
    position: float = Field(..., description="Net position quantity (positive = long)")
    avg_cost: float = Field(0.0, description="Average cost basis")
    market_price: float = Field(0.0, description="Current market price")
    market_value: float = Field(0.0, description="Current market value (position * price)")
    unrealized_pnl: float = Field(0.0, description="Unrealized P&L")
    unrealized_pnl_pct: float = Field(0.0, description="Unrealized P&L percentage")
    asset_class: AssetClass = Field(AssetClass.EQUITY, description="Asset class")
    timestamp_utc: datetime = Field(default_factory=_utc_naive)
    source: str = Field("IBKR", description="Data source")

    @property
    def symbol_upper(self) -> str:
        return self.symbol.upper().strip()

    @property
    def is_long(self) -> bool:
        return self.position > 0

    @property
    def is_short(self) -> bool:
        return self.position < 0


class OrderRecord(BaseModel):
    """A single order (open, filled, cancelled, etc.)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    order_id: int
    symbol: str = Field(..., min_length=1)
    con_id: Optional[int] = Field(None)
    exchange: Optional[str] = None
    side: str = Field(..., description="BUY or SELL")
    order_type: str = Field(..., description="MKT, LMT, STP, etc.")
    quantity: float = Field(..., gt=0)
    filled_quantity: float = Field(0.0, ge=0)
    limit_price: Optional[float] = Field(None, description="Limit price if LMT order")
    stop_price: Optional[float] = Field(None, description="Stop price if STP order")
    avg_fill_price: Optional[float] = Field(None, description="Average fill price")
    status: str = Field(..., description="Submitted, Filled, Cancelled, Pending, etc.")
    time_in_force: Optional[str] = Field(None, description="DAY, GTC, IOC, etc.")
    account_id: Optional[str] = None
    reason: Optional[str] = Field(None, description="Reason for order (strategy signal, etc.)")
    timestamp_utc: datetime = Field(default_factory=_utc_naive)
    source: str = Field("IBKR", description="Data source")

    @property
    def symbol_upper(self) -> str:
        return self.symbol.upper().strip()

    @property
    def is_filled(self) -> bool:
        return self.status.upper() == "FILLED"

    @property
    def is_open(self) -> bool:
        return self.status.upper() in ("SUBMITTED", "PENDING", "PRE SUBMITTED")


class ExecutionRecord(BaseModel):
    """A single execution (fill) from IBKR."""

    model_config = ConfigDict(str_strip_whitespace=True)

    exec_id: str
    order_id: int
    symbol: str = Field(..., min_length=1)
    side: str = Field(..., description="BUY or SELL")
    quantity: float = Field(..., gt=0)
    price: float = Field(..., gt=0, description="Fill price")
    commission: float = Field(0.0, ge=0, description="Commission charged")
    currency: str = Field("USD", description="Commission currency")
    time: datetime = Field(..., description="Execution time")
    source: str = Field("IBKR", description="Data source")

    @property
    def symbol_upper(self) -> str:
        return self.symbol.upper().strip()


class MarketQuote(BaseModel):
    """A real-time market quote (bid/ask/last)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(..., min_length=1)
    con_id: Optional[int] = None
    bid: Optional[float] = Field(None, ge=0, description="Best bid price")
    ask: Optional[float] = Field(None, ge=0, description="Best ask price")
    last: Optional[float] = Field(None, ge=0, description="Last trade price")
    bid_size: Optional[float] = Field(None, ge=0, description="Bid size")
    ask_size: Optional[float] = Field(None, ge=0, description="Ask size")
    volume: Optional[int] = Field(None, ge=0, description="Today's volume")
    high: Optional[float] = Field(None, ge=0, description="Day's high")
    low: Optional[float] = Field(None, ge=0, description="Day's low")
    close: Optional[float] = Field(None, ge=0, description="Previous close")
    timestamp_utc: datetime = Field(default_factory=_utc_naive)
    source: str = Field("IBKR", description="Data source")

    @property
    def symbol_upper(self) -> str:
        return self.symbol.upper().strip()

    @property
    def spread(self) -> Optional[float]:
        """Bid-ask spread if both are available."""
        if self.bid is not None and self.ask is not None:
            return self.ask - self.bid
        return None

    @property
    def mid_price(self) -> Optional[float]:
        """Mid price between bid and ask."""
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return self.last


class ContractInfo(BaseModel):
    """Contract details for an instrument."""

    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(..., min_length=1)
    con_id: int
    exchange: str = Field(..., description="Primary exchange")
    currency: str = Field("USD", description="Trading currency")
    asset_class: AssetClass = Field(AssetClass.EQUITY)
    sector: Optional[str] = Field(None, description="Business sector")
    industry: Optional[str] = Field(None, description="Industry sub-sector")
    market_cap: Optional[float] = Field(None, ge=0)
    multiplier: float = Field(1.0, description="Contract multiplier")
    min_tick: Optional[float] = Field(None, ge=0, description="Minimum price increment")
    trading_class: Optional[str] = None
    source: str = Field("IBKR", description="Data source")

    @property
    def symbol_upper(self) -> str:
        return self.symbol.upper().strip()