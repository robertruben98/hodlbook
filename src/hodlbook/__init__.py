"""hodlbook: a crypto paper-trading portfolio ledger built on pydynantic."""

from __future__ import annotations

from .errors import (
    HodlbookError,
    InsufficientFunds,
    InsufficientHoldings,
    InvalidOrder,
    TradeConflict,
    UnknownSymbol,
)
from .prices import (
    HttpPriceProvider,
    MockPriceProvider,
    PriceCache,
    PriceProvider,
)
from .repository import Repository
from .storage import (
    TABLE_NAME,
    Direction,
    Models,
    Side,
    build_models,
    build_table,
    create_table,
)
from .trading import MAX_RETRIES, TradeResult, TradingEngine
from .valuation import HoldingValuation, Valuation, Valuator

__version__ = "0.1.0"

__all__ = [
    "MAX_RETRIES",
    "TABLE_NAME",
    "Direction",
    "HodlbookError",
    "HoldingValuation",
    "HttpPriceProvider",
    "InsufficientFunds",
    "InsufficientHoldings",
    "InvalidOrder",
    "MockPriceProvider",
    "Models",
    "PriceCache",
    "PriceProvider",
    "Repository",
    "Side",
    "TradeConflict",
    "TradeResult",
    "TradingEngine",
    "UnknownSymbol",
    "Valuation",
    "Valuator",
    "build_models",
    "build_table",
    "create_table",
]
