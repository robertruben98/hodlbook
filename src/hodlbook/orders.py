"""Advanced-order executor for hodlbook: limit + DCA orders.

An :class:`OrderExecutor` pulls current prices for a set of symbols, scans the
OPEN orders on each symbol via the GSI2 ``by_status_symbol`` index, and fills
the ones whose trigger condition the price has met:

* LIMIT BUY fills when ``price <= limit_price``; LIMIT SELL when
  ``price >= limit_price``.
* DCA fills on every due interval (``next_run <= now`` with ``remaining_runs``
  left), buying/selling at the market price each tick.

Fills go through the atomic :class:`~hodlbook.trading.TradingEngine`, so cash and
holdings stay balance-safe. The order-status follow-up is a *separate* guarded
update (never folded into the engine's transaction): a LIMIT order is marked
FILLED, a DCA order decrements ``remaining_runs`` and advances ``next_run``,
becoming FILLED only when it runs out. The guard (``status == OPEN`` plus the
optimistic version lock) makes a second pass a no-op -- no double-fill.

A fill that the engine rejects for funds/holdings does not crash the pass: the
order is left OPEN and recorded in ``skipped`` (a DCA tick still advances
``next_run`` so it does not busy-retry, but does NOT burn a run).

All comparisons use :class:`~decimal.Decimal`; floats never touch price math.
"""

from __future__ import annotations

import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pydynantic

from .errors import InsufficientFunds, InsufficientHoldings
from .repository import Repository
from .storage import OrderStatus, OrderType, Side
from .trading import TradingEngine


class PriceSource(typing.Protocol):
    """A source of current USD prices keyed by symbol.

    :class:`~hodlbook.prices.PriceCache` already satisfies this Protocol.
    """

    def get_cached_prices(self, symbols: list[str]) -> dict[str, Decimal]: ...


@dataclass(frozen=True)
class FilledOrder:
    """An order that filled, paired with the market price it filled at."""

    order: Any
    fill_price: Decimal


@dataclass(frozen=True)
class SkippedOrder:
    """An order whose fill the engine rejected (insufficient funds/holdings)."""

    order: Any
    reason: str


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of one :meth:`OrderExecutor.execute` pass."""

    fills: list[FilledOrder] = field(default_factory=list)
    skipped: list[SkippedOrder] = field(default_factory=list)


class OrderExecutor:
    """Evaluates OPEN advanced orders against current prices and fills them."""

    def __init__(
        self,
        repo: Repository,
        engine: TradingEngine,
        prices: PriceSource,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repo = repo
        self.engine = engine
        self.prices = prices
        self._clock = clock

    def execute(self, symbols: list[str]) -> ExecutionResult:
        """Fill every OPEN order on ``symbols`` whose trigger the price has met."""
        current = self.prices.get_cached_prices(symbols)
        result = ExecutionResult()

        for symbol in symbols:
            price = current[symbol]
            for order in self.repo.list_open_orders_by_symbol(symbol):
                if not self._is_eligible(order, price):
                    continue
                self._fill(order, price, result)

        return result

    def _is_eligible(self, order: Any, price: Decimal) -> bool:
        if order.order_type is OrderType.LIMIT:
            if order.side is Side.BUY:
                return bool(price <= order.limit_price)
            return bool(price >= order.limit_price)
        if order.order_type is OrderType.DCA:
            if order.remaining_runs is None or order.remaining_runs <= 0:
                return False
            return order.next_run is not None and order.next_run <= self._clock()
        # MARKET orders are filled synchronously at creation, never scanned here.
        return False

    def _fill(self, order: Any, price: Decimal, result: ExecutionResult) -> None:
        """Execute one order's fill at market ``price`` and persist the follow-up."""
        try:
            self._trade(order, price)
        except (InsufficientFunds, InsufficientHoldings) as exc:
            # Leave the order OPEN. For DCA, advance the schedule so we do not
            # busy-retry the same due tick -- but do NOT burn a run.
            if order.order_type is OrderType.DCA:
                self._advance_dca_schedule(order, decrement=False)
            result.skipped.append(SkippedOrder(order=order, reason=str(exc)))
            return

        if order.order_type is OrderType.DCA:
            self._advance_dca_schedule(order, decrement=True)
        else:  # LIMIT
            try:
                self.repo.mark_order_filled(order)
            except pydynantic.ConditionCheckFailedError:
                # Lost race: filled/cancelled concurrently. The trade already
                # landed atomically; skip the status follow-up.
                return
        result.fills.append(FilledOrder(order=order, fill_price=price))

    def _trade(self, order: Any, price: Decimal) -> None:
        if order.side is Side.BUY:
            self.engine.buy(order.user_id, order.portfolio_id, order.symbol, order.quantity, price)
        else:
            self.engine.sell(order.user_id, order.portfolio_id, order.symbol, order.quantity, price)

    def _advance_dca_schedule(self, order: Any, *, decrement: bool) -> None:
        if decrement:
            order.remaining_runs -= 1
        if order.interval_seconds is not None and order.next_run is not None:
            order.next_run = order.next_run + timedelta(seconds=order.interval_seconds)
        if decrement and order.remaining_runs <= 0:
            order.status = OrderStatus.FILLED
        try:
            self.repo.advance_dca(order)
        except pydynantic.ConditionCheckFailedError:
            # Lost race: the order was filled/cancelled concurrently. No-op.
            return
