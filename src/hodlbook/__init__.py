"""hodlbook: a crypto paper-trading portfolio ledger built on pydynantic."""

from __future__ import annotations

from .errors import (
    HodlbookError,
    InsufficientFunds,
    InsufficientHoldings,
    InvalidOrder,
    TradeConflict,
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

__version__ = "0.1.0"

__all__ = [
    "MAX_RETRIES",
    "TABLE_NAME",
    "Direction",
    "HodlbookError",
    "InsufficientFunds",
    "InsufficientHoldings",
    "InvalidOrder",
    "Models",
    "Repository",
    "Side",
    "TradeConflict",
    "TradeResult",
    "TradingEngine",
    "build_models",
    "build_table",
    "create_table",
]
