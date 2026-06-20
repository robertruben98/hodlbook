"""hodlbook: a crypto paper-trading portfolio ledger built on pydynantic."""

from __future__ import annotations

from .alerts import AlertEvaluator, FiredAlert
from .api import create_app
from .errors import (
    HodlbookError,
    InsufficientFunds,
    InsufficientHoldings,
    InvalidOrder,
    TradeConflict,
    UnknownSymbol,
)
from .observability import collecting_hook, logging_hook
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

__version__ = "1.0.0"

__all__ = [
    "MAX_RETRIES",
    "TABLE_NAME",
    "AlertEvaluator",
    "Direction",
    "FiredAlert",
    "collecting_hook",
    "logging_hook",
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
    "create_app",
    "build_table",
    "create_table",
]
