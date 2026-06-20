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

from pydynantic import F, Page, Table, attr_not_exists

from .storage import Direction, Models, Side, build_models


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
