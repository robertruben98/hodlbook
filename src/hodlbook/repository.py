"""A thin, typed repository over the hodlbook entity classes.

The :class:`Repository` is constructed from a :class:`~pydynantic.Table`, binds
the five entity classes to it via :func:`~hodlbook.storage.build_models`, and
exposes the storage-layer access patterns as plain methods. Business logic
(atomic trading, valuation) lives in later milestones; this layer only persists
and reads.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydynantic import Page, Table, attr_not_exists

from .storage import Direction, Models, Side, build_models


class Repository:
    """Persistence helpers for hodlbook entities, bound to one table."""

    def __init__(self, table: Table) -> None:
        self.table = table
        self.models: Models = build_models(table)

    # -- portfolios ---------------------------------------------------------
    def create_portfolio(
        self, user_id: str, portfolio_id: str, cash: Decimal = Decimal("0")
    ) -> Any:
        """Create a portfolio, failing if one already exists at that key."""
        portfolio = self.models.Portfolio(user_id=user_id, portfolio_id=portfolio_id, cash=cash)
        return self.models.Portfolio.put(portfolio, condition=attr_not_exists("PK"))

    def get_portfolio(self, user_id: str, portfolio_id: str) -> Any | None:
        return self.models.Portfolio.get(user_id=user_id, portfolio_id=portfolio_id)

    # -- holdings -----------------------------------------------------------
    def upsert_holding(
        self, portfolio_id: str, symbol: str, quantity: Decimal, avg_cost: Decimal
    ) -> Any:
        holding = self.models.Holding(
            portfolio_id=portfolio_id,
            symbol=symbol,
            quantity=quantity,
            avg_cost=avg_cost,
        )
        return self.models.Holding.put(holding)

    # -- trades -------------------------------------------------------------
    def record_trade(
        self,
        portfolio_id: str,
        trade_id: str,
        symbol: str,
        side: Side,
        quantity: Decimal,
        price: Decimal,
        ts: str,
    ) -> Any:
        trade = self.models.Trade(
            portfolio_id=portfolio_id,
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            ts=ts,
        )
        return self.models.Trade.put(trade)

    def list_trades(self, portfolio_id: str, *, cursor: str | None = None) -> Page[Any]:
        """Most-recent-first page of trades for a portfolio."""
        return (
            self.models.Trade.query.primary(portfolio_id=portfolio_id)
            .begins_with("TRADE#")
            .descending()
            .page(cursor)
        )

    def list_trades_by_symbol(self, symbol: str, *, cursor: str | None = None) -> Page[Any]:
        """Most-recent-first page of trades for a symbol across portfolios (GSI1)."""
        return (
            self.models.Trade.query.by_symbol(symbol=symbol)
            .begins_with("TRADE#")
            .descending()
            .page(cursor)
        )

    # -- prices -------------------------------------------------------------
    def put_price(self, symbol: str, price: Decimal, as_of: datetime, expires_at: datetime) -> Any:
        tick = self.models.PriceTick(symbol=symbol, price=price, as_of=as_of, expires_at=expires_at)
        return self.models.PriceTick.put(tick)

    def get_price(self, symbol: str) -> Any | None:
        return self.models.PriceTick.get(symbol=symbol)

    # -- alerts -------------------------------------------------------------
    def create_alert(
        self,
        portfolio_id: str,
        alert_id: str,
        symbol: str,
        direction: Direction,
        threshold: Decimal,
    ) -> Any:
        alert = self.models.Alert(
            portfolio_id=portfolio_id,
            alert_id=alert_id,
            symbol=symbol,
            direction=direction,
            threshold=threshold,
        )
        return self.models.Alert.put(alert)

    def list_alerts(self, portfolio_id: str) -> list[Any]:
        return (
            self.models.Alert.query.primary(portfolio_id=portfolio_id).begins_with("ALERT#").all()
        )

    def list_alerts_by_symbol(self, symbol: str) -> list[Any]:
        return self.models.Alert.query.by_symbol(symbol=symbol).all()

    def get_alert(self, portfolio_id: str, alert_id: str) -> Any | None:
        return self.models.Alert.get(portfolio_id=portfolio_id, alert_id=alert_id)

    def mark_alert_triggered(self, portfolio_id: str, alert_id: str) -> None:
        alert: Any = self.models.Alert.get_or_raise(portfolio_id=portfolio_id, alert_id=alert_id)
        alert.triggered = True
        self.models.Alert.put(alert)
