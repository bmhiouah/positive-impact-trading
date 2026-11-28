"""Broker adapters for order execution.

Currently provides an Interactive Brokers (IBKR) Gateway adapter that
integrates with the Impact-compliant execution engine.
"""

from .contracts import Listing, ListingBook, make_stock
from .ib_adapter import IBKRAdapter, OrderFill, format_gateway_error

__all__ = [
    "IBKRAdapter",
    "OrderFill",
    "Listing",
    "ListingBook",
    "make_stock",
    "format_gateway_error",
]