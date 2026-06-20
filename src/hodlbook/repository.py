"""A thin, typed repository over the hodlbook entity classes.

The :class:`Repository` is constructed from a :class:`~pydynantic.Table`, binds
the five entity classes to it via :func:`~hodlbook.storage.build_models`, and
exposes the storage-layer access patterns as plain methods. Business logic
(atomic trading, valuation) lives in later milestones; this layer only persists
and reads.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydynantic import ConditionCheckFailedError, F, Page, Table, attr_not_exists

from .storage import Direction, Models, OrderStatus, OrderType, Side, build_models


def _hash_token(raw: str) -> str:
    """Return the SHA-256 hex digest of a raw API token.

    Only the hash is ever persisted; the raw token is shown to the caller once
    at issue time and is otherwise unrecoverable.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


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
    def get_holding(self, portfolio_id: str, symbol: str) -> Any | None:
        return self.models.Holding.get(portfolio_id=portfolio_id, symbol=symbol)

    def get_holdings(self, portfolio_id: str) -> list[Any]:
        """All holdings for a portfolio (mirrors :meth:`list_alerts`)."""
        return (
            self.models.Holding.query.primary(portfolio_id=portfolio_id)
            .begins_with("HOLDING#")
            .all()
        )

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

    def list_trades(
        self, portfolio_id: str, *, cursor: str | None = None, limit: int | None = None
    ) -> Page[Any]:
        """Most-recent-first page of trades for a portfolio."""
        builder = (
            self.models.Trade.query.primary(portfolio_id=portfolio_id)
            .begins_with("TRADE#")
            .descending()
        )
        if limit is not None:
            builder = builder.limit(limit)
        return builder.page(cursor)

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
        """Mark an alert triggered, conditionally so a double-fire is a no-op.

        Guarded with ``F("triggered") == False`` so a re-fire / lost race raises
        :class:`pydynantic.ConditionCheckFailedError` rather than silently
        re-stamping an already-triggered alert.
        """
        alert: Any = self.models.Alert.get_or_raise(portfolio_id=portfolio_id, alert_id=alert_id)
        alert.triggered = True
        self.models.Alert.put(alert, condition=F("triggered") == False)  # noqa: E712

    def delete_alert(self, portfolio_id: str, alert_id: str) -> None:
        """Delete an alert by its primary key (idempotent)."""
        self.models.Alert.delete(portfolio_id=portfolio_id, alert_id=alert_id)

    # -- api keys -----------------------------------------------------------
    def issue_api_key(self, user_id: str) -> tuple[str, Any]:
        """Mint a new API key for ``user_id``.

        Returns ``(raw_token, entity)``. The raw token is generated here and
        returned ONLY from this method -- the table stores its SHA-256 hash, so
        the plaintext cannot be recovered afterwards.
        """
        raw = secrets.token_urlsafe(32)
        api_key = self.models.ApiKey(
            key_id=uuid4().hex,
            user_id=user_id,
            key_hash=_hash_token(raw),
        )
        entity = self.models.ApiKey.put(api_key)
        return raw, entity

    def get_api_key_by_hash(self, key_hash: str) -> Any | None:
        return self.models.ApiKey.get(key_hash=key_hash)

    def revoke_api_key(self, user_id: str, key_id: str) -> None:
        """Mark a user's API key revoked (no-op if it does not exist)."""
        for api_key in self.list_api_keys(user_id):
            if api_key.key_id == key_id:
                api_key.revoked = True
                self.models.ApiKey.put(api_key)
                return

    def list_api_keys(self, user_id: str) -> list[Any]:
        return self.models.ApiKey.query.by_user(user_id=user_id).begins_with("APIKEY#").all()

    # -- orders -------------------------------------------------------------
    def create_order(
        self,
        portfolio_id: str,
        order_id: str,
        user_id: str,
        symbol: str,
        side: Side,
        order_type: OrderType,
        quantity: Decimal,
        *,
        limit_price: Decimal | None = None,
        interval_seconds: int | None = None,
        next_run: datetime | None = None,
        remaining_runs: int | None = None,
    ) -> Any:
        """Create an OPEN order, failing if one already exists at that key."""
        order = self.models.Order(
            portfolio_id=portfolio_id,
            order_id=order_id,
            user_id=user_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            status=OrderStatus.OPEN,
            interval_seconds=interval_seconds,
            next_run=next_run,
            remaining_runs=remaining_runs,
        )
        return self.models.Order.put(order, condition=attr_not_exists("PK"))

    def get_order(self, portfolio_id: str, order_id: str) -> Any | None:
        return self.models.Order.get(portfolio_id=portfolio_id, order_id=order_id)

    def list_orders(self, portfolio_id: str) -> list[Any]:
        return (
            self.models.Order.query.primary(portfolio_id=portfolio_id).begins_with("ORDER#").all()
        )

    def list_open_orders_by_symbol(self, symbol: str) -> list[Any]:
        """All OPEN orders for ``symbol`` across portfolios (GSI2)."""
        return self.models.Order.query.by_status_symbol(
            status=OrderStatus.OPEN.value, symbol=symbol
        ).all()

    def cancel_order(self, portfolio_id: str, order_id: str) -> None:
        """Cancel an OPEN order, guarded so a cancel/fill race is safe.

        Guarded with ``F("status") == OrderStatus.OPEN``: if the order has
        already been filled or cancelled, the conditional put raises
        :class:`pydynantic.ConditionCheckFailedError`, which we swallow and
        treat as already-resolved (the cancel is a no-op).
        """
        order: Any = self.models.Order.get(portfolio_id=portfolio_id, order_id=order_id)
        if order is None:
            return
        order.status = OrderStatus.CANCELLED
        try:
            self.models.Order.put(order, condition=F("status") == OrderStatus.OPEN)
        except ConditionCheckFailedError:
            # Lost race: the order was filled/cancelled concurrently. No-op.
            return

    def mark_order_filled(self, order: Any) -> None:
        """Mark an order FILLED, guarded on ``status == OPEN`` AND ``version``.

        The version lock is applied automatically by ``put`` from the loaded
        order's version; we add ``F("status") == OPEN`` so a concurrent
        fill/cancel makes this a safe no-op. Raises
        :class:`pydynantic.ConditionCheckFailedError` on a lost race.
        """
        order.status = OrderStatus.FILLED
        self.models.Order.put(order, condition=F("status") == OrderStatus.OPEN)

    def advance_dca(self, order: Any) -> None:
        """Persist a DCA order's advanced schedule, guarded on ``status == OPEN``.

        Mirrors :meth:`mark_order_filled` -- the optimistic version lock plus the
        ``status == OPEN`` guard make a lost race a no-op. The caller mutates
        ``next_run`` / ``remaining_runs`` / ``status`` before calling.
        """
        self.models.Order.put(order, condition=F("status") == OrderStatus.OPEN)

    # -- snapshots ----------------------------------------------------------
    def put_snapshot(
        self,
        portfolio_id: str,
        taken_at: str,
        total_value: Decimal,
        cash: Decimal,
        holdings_value: Decimal,
        total_unrealized_pnl: Decimal,
    ) -> Any:
        snapshot = self.models.Snapshot(
            portfolio_id=portfolio_id,
            taken_at=taken_at,
            total_value=total_value,
            cash=cash,
            holdings_value=holdings_value,
            total_unrealized_pnl=total_unrealized_pnl,
        )
        return self.models.Snapshot.put(snapshot)

    def list_snapshots(
        self, portfolio_id: str, *, cursor: str | None = None, limit: int | None = None
    ) -> Page[Any]:
        """Most-recent-first page of value snapshots for a portfolio."""
        builder = (
            self.models.Snapshot.query.primary(portfolio_id=portfolio_id)
            .begins_with("SNAPSHOT#")
            .descending()
        )
        if limit is not None:
            builder = builder.limit(limit)
        return builder.page(cursor)

    # -- leaderboard --------------------------------------------------------
    def upsert_leaderboard_entry(
        self,
        user_id: str,
        portfolio_id: str,
        total_value: Decimal,
        rank_key: str,
        taken_at: str,
    ) -> Any:
        """Insert-or-replace a portfolio's leaderboard entry at its primary key.

        The primary key is stable per ``(user_id, portfolio_id)`` so re-snapshotting
        overwrites the prior entry rather than accumulating; ``rank_key`` (the
        zero-padded value) rides along on GSI1 for ranked reads.
        """
        entry = self.models.LeaderboardEntry(
            user_id=user_id,
            portfolio_id=portfolio_id,
            total_value=total_value,
            rank_key=rank_key,
            taken_at=taken_at,
        )
        return self.models.LeaderboardEntry.put(entry)

    def top_leaderboard(self, limit: int) -> list[Any]:
        """Top-``limit`` leaderboard entries by total value, highest first (GSI1)."""
        return self.models.LeaderboardEntry.query.by_value().descending().limit(limit).all()
