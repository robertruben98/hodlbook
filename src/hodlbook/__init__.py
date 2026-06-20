"""hodlbook: a crypto paper-trading portfolio ledger built on pydynantic."""

from __future__ import annotations

from .alerts import AlertEvaluator, FiredAlert
from .analytics import Analytics
from .api import create_app
from .errors import (
    AuthenticationError,
    AuthorizationError,
    HodlbookError,
    InsufficientFunds,
    InsufficientHoldings,
    InvalidOrder,
    OrderNotFound,
    RateLimitExceeded,
    TradeConflict,
    UnknownSymbol,
)
from .observability import collecting_hook, logging_hook, setup_logging
from .orders import ExecutionResult, FilledOrder, OrderExecutor, SkippedOrder
from .prices import (
    HttpPriceProvider,
    MockPriceProvider,
    PriceCache,
    PriceProvider,
)
from .repository import Repository
from .settings import Settings, get_settings
from .storage import (
    TABLE_NAME,
    Direction,
    Models,
    OrderStatus,
    OrderType,
    Side,
    build_models,
    build_table,
    create_table,
)
from .trading import MAX_RETRIES, TradeResult, TradingEngine
from .valuation import HoldingValuation, Valuation, Valuator

__version__ = "2.0.0"

__all__ = [
    "MAX_RETRIES",
    "TABLE_NAME",
    # Services
    "Repository",
    "TradingEngine",
    "OrderExecutor",
    "AlertEvaluator",
    "Analytics",
    "Valuator",
    "create_app",
    # Storage / models
    "build_table",
    "build_models",
    "create_table",
    "Models",
    "Side",
    "Direction",
    "OrderType",
    "OrderStatus",
    # Prices
    "PriceProvider",
    "MockPriceProvider",
    "HttpPriceProvider",
    "PriceCache",
    # Results / values
    "TradeResult",
    "FilledOrder",
    "ExecutionResult",
    "SkippedOrder",
    "FiredAlert",
    "Valuation",
    "HoldingValuation",
    # Settings
    "Settings",
    "get_settings",
    # Observability
    "logging_hook",
    "collecting_hook",
    "setup_logging",
    # Errors
    "HodlbookError",
    "InsufficientFunds",
    "InsufficientHoldings",
    "InvalidOrder",
    "TradeConflict",
    "UnknownSymbol",
    "OrderNotFound",
    "AuthenticationError",
    "AuthorizationError",
    "RateLimitExceeded",
]
