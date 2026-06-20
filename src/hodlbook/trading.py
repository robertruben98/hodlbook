"""Atomic trading engine for hodlbook.

Buys and sells mutate the portfolio's cash, the per-symbol holding, and append
an immutable trade record -- all in a single DynamoDB transaction, so either
every change lands or none does. Concurrency is handled with optimistic locking
on ``Portfolio.version``: each trade reads the version, then guards the
transaction with ``F("version") == v`` and bumps it. A lost race cancels the
transaction; the engine retries up to :data:`MAX_RETRIES` times before raising
:class:`~hodlbook.errors.TradeConflict`.

All monetary math uses :class:`~decimal.Decimal` -- never ``float``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydynantic import F, TransactionCanceledError, transaction

from .errors import (
    InsufficientFunds,
    InsufficientHoldings,
    InvalidOrder,
    TradeConflict,
)
from .repository import Repository
from .storage import Side

#: How many times a trade retries on a lost optimistic-lock race before failing.
MAX_RETRIES = 3


@dataclass(frozen=True)
class TradeResult:
    """Outcome of a trade: the persisted trade plus realized P&L.

    ``realized_pnl`` is always ``Decimal("0")`` for buys (cost basis only moves
    on a sell) and ``(price - avg_cost) * quantity`` for sells. The ``Trade``
    schema intentionally carries no P&L field; it is surfaced here instead.
    """

    trade: Any
    realized_pnl: Decimal


class TradingEngine:
    """Executes atomic buy/sell orders against a :class:`Repository`."""

    def __init__(
        self,
        repo: Repository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_gen: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self.repo = repo
        self.models = repo.models
        self.table = repo.table
        self._clock = clock
        self._id_gen = id_gen

    def buy(
        self,
        user_id: str,
        portfolio_id: str,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
    ) -> TradeResult:
        """Buy ``quantity`` of ``symbol`` at ``price``, atomically."""
        if quantity <= 0 or price <= 0:
            raise InvalidOrder("quantity and price must both be positive")

        cost = quantity * price

        for _ in range(MAX_RETRIES):
            portfolio: Any = self.models.Portfolio.get(user_id=user_id, portfolio_id=portfolio_id)
            if portfolio is None:
                raise InvalidOrder(f"unknown portfolio {user_id}/{portfolio_id}")
            version = portfolio.version
            cash = portfolio.cash
            if cash < cost:
                raise InsufficientFunds(
                    f"cash {cash} < cost {cost} for {quantity} {symbol} @ {price}"
                )

            holding: Any = self.repo.get_holding(portfolio_id, symbol)
            old_qty = holding.quantity if holding is not None else Decimal("0")
            old_avg = holding.avg_cost if holding is not None else Decimal("0")
            new_qty = old_qty + quantity
            avg_cost = (old_qty * old_avg + cost) / new_qty

            trade = self.models.Trade(
                portfolio_id=portfolio_id,
                trade_id=self._id_gen(),
                symbol=symbol,
                side=Side.BUY,
                quantity=quantity,
                price=price,
                ts=self._clock().isoformat(),
            )
            try:
                with transaction(self.table) as tx:
                    tx.update(
                        self.models.Portfolio,
                        key=(user_id, portfolio_id),
                        set={"cash": cash - cost},
                        add={"version": 1},
                        condition=(F("version") == version) & (F("cash") >= cost),
                    )
                    tx.put(
                        self.models.Holding(
                            portfolio_id=portfolio_id,
                            symbol=symbol,
                            quantity=new_qty,
                            avg_cost=avg_cost,
                        )
                    )
                    tx.put(trade)
            except TransactionCanceledError:
                continue
            return TradeResult(trade=trade, realized_pnl=Decimal("0"))

        raise TradeConflict(f"buy of {symbol} lost the version race after {MAX_RETRIES} retries")

    def sell(
        self,
        user_id: str,
        portfolio_id: str,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
    ) -> TradeResult:
        """Sell ``quantity`` of ``symbol`` at ``price``, atomically."""
        if quantity <= 0 or price <= 0:
            raise InvalidOrder("quantity and price must both be positive")

        proceeds = quantity * price

        for _ in range(MAX_RETRIES):
            portfolio: Any = self.models.Portfolio.get(user_id=user_id, portfolio_id=portfolio_id)
            if portfolio is None:
                raise InvalidOrder(f"unknown portfolio {user_id}/{portfolio_id}")
            version = portfolio.version
            cash = portfolio.cash

            holding: Any = self.repo.get_holding(portfolio_id, symbol)
            if holding is None or holding.quantity < quantity:
                have = holding.quantity if holding is not None else Decimal("0")
                raise InsufficientHoldings(f"have {have} {symbol}, tried to sell {quantity}")
            old_qty = holding.quantity
            avg_cost = holding.avg_cost
            remaining = old_qty - quantity
            realized_pnl = (price - avg_cost) * quantity

            trade = self.models.Trade(
                portfolio_id=portfolio_id,
                trade_id=self._id_gen(),
                symbol=symbol,
                side=Side.SELL,
                quantity=quantity,
                price=price,
                ts=self._clock().isoformat(),
            )
            try:
                with transaction(self.table) as tx:
                    tx.update(
                        self.models.Portfolio,
                        key=(user_id, portfolio_id),
                        set={"cash": cash + proceeds},
                        add={"version": 1},
                        condition=F("version") == version,
                    )
                    if remaining == 0:
                        tx.delete(
                            self.models.Holding,
                            key=(portfolio_id, symbol),
                            condition=F("quantity") == old_qty,
                        )
                    else:
                        tx.update(
                            self.models.Holding,
                            key=(portfolio_id, symbol),
                            set={"quantity": remaining},
                            condition=F("quantity") == old_qty,
                        )
                    tx.put(trade)
            except TransactionCanceledError:
                continue
            return TradeResult(trade=trade, realized_pnl=realized_pnl)

        raise TradeConflict(f"sell of {symbol} lost the version race after {MAX_RETRIES} retries")
