"""Repository behavior tests against a mocked DynamoDB table."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydynantic import ConditionCheckFailedError, ItemNotFoundError

from hodlbook.repository import Repository
from hodlbook.storage import Direction, Side


# -- portfolios -------------------------------------------------------------
def test_create_and_get_portfolio(repo: Repository) -> None:
    created = repo.create_portfolio("u1", "p1", cash=Decimal("1000"))
    assert created.user_id == "u1"
    assert created.cash == Decimal("1000")

    fetched = repo.get_portfolio("u1", "p1")
    assert fetched is not None
    assert fetched.portfolio_id == "p1"
    assert fetched.cash == Decimal("1000")


def test_create_portfolio_default_cash(repo: Repository) -> None:
    created = repo.create_portfolio("u1", "p1")
    assert created.cash == Decimal("0")


def test_get_missing_portfolio_returns_none(repo: Repository) -> None:
    assert repo.get_portfolio("nobody", "nope") is None


def test_create_portfolio_is_create_only(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))
    with pytest.raises(ConditionCheckFailedError):
        repo.create_portfolio("u1", "p1", cash=Decimal("9999"))
    # Original value untouched.
    fetched = repo.get_portfolio("u1", "p1")
    assert fetched is not None
    assert fetched.cash == Decimal("1000")


def test_create_portfolio_stamps_version_and_timestamps(repo: Repository) -> None:
    before = datetime.now(timezone.utc)
    created = repo.create_portfolio("u1", "p1")
    assert created.version == 1
    assert created.created_at is not None
    assert created.updated_at is not None
    assert created.created_at >= before


# -- holdings ---------------------------------------------------------------
def test_upsert_holding_round_trip(repo: Repository) -> None:
    repo.upsert_holding("p1", "BTC", quantity=Decimal("2"), avg_cost=Decimal("100"))
    holding = repo.models.Holding.get(portfolio_id="p1", symbol="BTC")
    assert holding is not None
    assert holding.quantity == Decimal("2")
    assert holding.avg_cost == Decimal("100")


def test_upsert_holding_overwrites(repo: Repository) -> None:
    repo.upsert_holding("p1", "BTC", quantity=Decimal("2"), avg_cost=Decimal("100"))
    repo.upsert_holding("p1", "BTC", quantity=Decimal("5"), avg_cost=Decimal("120"))
    holding = repo.models.Holding.get(portfolio_id="p1", symbol="BTC")
    assert holding is not None
    assert holding.quantity == Decimal("5")
    assert holding.avg_cost == Decimal("120")


# -- trades -----------------------------------------------------------------
def _seed_trades(repo: Repository, portfolio_id: str, symbol: str, n: int) -> None:
    for i in range(n):
        repo.record_trade(
            portfolio_id=portfolio_id,
            trade_id=f"t{i}",
            symbol=symbol,
            side=Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
            ts=f"2024-01-{i + 1:02d}T00:00:00Z",
        )


def test_record_trade_round_trip(repo: Repository) -> None:
    trade = repo.record_trade(
        portfolio_id="p1",
        trade_id="t1",
        symbol="BTC",
        side=Side.SELL,
        quantity=Decimal("3"),
        price=Decimal("250"),
        ts="2024-01-01T00:00:00Z",
    )
    assert trade.side == Side.SELL
    fetched = repo.models.Trade.get(portfolio_id="p1", trade_id="t1", ts="2024-01-01T00:00:00Z")
    assert fetched is not None
    assert fetched.quantity == Decimal("3")
    assert fetched.side == Side.SELL


def test_list_trades_descending(repo: Repository) -> None:
    _seed_trades(repo, "p1", "BTC", 3)
    page = repo.list_trades("p1")
    ids = [t.trade_id for t in page.items]
    # Newest ts first.
    assert ids == ["t2", "t1", "t0"]


def test_list_trades_pagination_cursor(repo: Repository) -> None:
    _seed_trades(repo, "p1", "BTC", 5)
    # Drive pagination across a small page via cursor continuation.
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = (
            repo.models.Trade.query.primary(portfolio_id="p1")
            .begins_with("TRADE#")
            .descending()
            .limit(2)
            .page(cursor)
        )
        seen.extend(t.trade_id for t in page.items)
        pages += 1
        if not page.has_more:
            break
        cursor = page.cursor
    assert seen == ["t4", "t3", "t2", "t1", "t0"]
    assert pages == 3  # 2 + 2 + 1


def test_list_trades_by_symbol_gsi1(repo: Repository) -> None:
    # Two portfolios trading the same symbol show up under one GSI1 partition.
    repo.record_trade(
        "p1", "t1", "BTC", Side.BUY, Decimal("1"), Decimal("100"), "2024-01-01T00:00:00Z"
    )
    repo.record_trade(
        "p2", "t2", "BTC", Side.SELL, Decimal("2"), Decimal("110"), "2024-01-02T00:00:00Z"
    )
    repo.record_trade(
        "p1", "t3", "ETH", Side.BUY, Decimal("3"), Decimal("50"), "2024-01-03T00:00:00Z"
    )

    page = repo.list_trades_by_symbol("BTC")
    ids = [t.trade_id for t in page.items]
    assert ids == ["t2", "t1"]  # descending by ts


# -- prices -----------------------------------------------------------------
def test_put_and_get_price(repo: Repository) -> None:
    as_of = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    expires = as_of + timedelta(minutes=5)
    repo.put_price("BTC", Decimal("50000"), as_of=as_of, expires_at=expires)

    tick = repo.get_price("BTC")
    assert tick is not None
    assert tick.price == Decimal("50000")
    assert tick.expires_at is not None


def test_get_missing_price_returns_none(repo: Repository) -> None:
    assert repo.get_price("DOGE") is None


def test_price_ttl_stored_as_number(repo: Repository) -> None:
    as_of = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    expires = as_of + timedelta(minutes=5)
    repo.put_price("BTC", Decimal("50000"), as_of=as_of, expires_at=expires)
    # Inspect the raw item: TTL must be a DynamoDB Number (epoch seconds).
    raw = repo.table.client.get_item(
        TableName=repo.table.name,
        Key={"PK": {"S": "PRICE#BTC"}, "SK": {"S": "TICK"}},
    )["Item"]
    assert "N" in raw["expires_at"]
    assert int(raw["expires_at"]["N"]) == int(expires.timestamp())


# -- alerts -----------------------------------------------------------------
def test_create_get_alert(repo: Repository) -> None:
    repo.create_alert("p1", "a1", "BTC", Direction.ABOVE, Decimal("60000"))
    alert = repo.get_alert("p1", "a1")
    assert alert is not None
    assert alert.direction == Direction.ABOVE
    assert alert.threshold == Decimal("60000")
    assert alert.triggered is False


def test_get_missing_alert_returns_none(repo: Repository) -> None:
    assert repo.get_alert("p1", "nope") is None


def test_list_alerts(repo: Repository) -> None:
    repo.create_alert("p1", "a1", "BTC", Direction.ABOVE, Decimal("60000"))
    repo.create_alert("p1", "a2", "ETH", Direction.BELOW, Decimal("2000"))
    repo.create_alert("p2", "a3", "BTC", Direction.ABOVE, Decimal("70000"))

    alerts = repo.list_alerts("p1")
    ids = sorted(a.alert_id for a in alerts)
    assert ids == ["a1", "a2"]


def test_list_alerts_by_symbol_gsi2(repo: Repository) -> None:
    repo.create_alert("p1", "a1", "BTC", Direction.ABOVE, Decimal("60000"))
    repo.create_alert("p2", "a2", "BTC", Direction.BELOW, Decimal("50000"))
    repo.create_alert("p1", "a3", "ETH", Direction.ABOVE, Decimal("3000"))

    btc = repo.list_alerts_by_symbol("BTC")
    ids = sorted(a.alert_id for a in btc)
    assert ids == ["a1", "a2"]


def test_mark_alert_triggered(repo: Repository) -> None:
    repo.create_alert("p1", "a1", "BTC", Direction.ABOVE, Decimal("60000"))
    repo.mark_alert_triggered("p1", "a1")
    alert = repo.get_alert("p1", "a1")
    assert alert is not None
    assert alert.triggered is True


def test_mark_alert_triggered_missing_raises(repo: Repository) -> None:
    with pytest.raises(ItemNotFoundError):
        repo.mark_alert_triggered("p1", "ghost")
