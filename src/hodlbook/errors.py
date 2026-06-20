"""Domain error hierarchy for hodlbook's trading engine.

These errors are the only failures the trading API surfaces to callers. They are
deliberately independent of pydynantic's exceptions: the engine catches
pydynantic's ``TransactionCanceledError``/``ConditionCheckFailedError``
internally (to drive optimistic-lock retries) and translates them into these
domain errors, so storage-layer concerns never leak out.
"""

from __future__ import annotations


class HodlbookError(Exception):
    """Base class for every error raised by the hodlbook trading engine."""


class InsufficientFunds(HodlbookError):
    """A buy was rejected because the portfolio lacks enough cash."""


class InsufficientHoldings(HodlbookError):
    """A sell was rejected because the portfolio lacks enough of the symbol."""


class InvalidOrder(HodlbookError):
    """An order was malformed (bad quantity/price, or unknown portfolio)."""


class TradeConflict(HodlbookError):
    """A trade lost the optimistic-lock race repeatedly and gave up retrying."""
