"""Broker adapters for order execution.

Currently provides an Interactive Brokers (IBKR) Gateway adapter that
integrates with the Impact-compliant execution engine.
"""

from .ib_adapter import IBKRAdapter, OrderFill

__all__ = ["IBKRAdapter", "OrderFill"]