"""Behavior tests for the atomic trading engine against a mocked DynamoDB table."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from hodlbook.errors import (
    InsufficientFunds,
    InsufficientHoldings,
    InvalidOrder,
    TradeConflict,
)
from hodlbook.repository import Repository
from hodlbook.storage import Side
from hodlbook.trading import MAX_RETRIES, TradingEngine

_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _fixed_clock() -> Callable[[], datetime]:
    """A clock that advances one second each call (stable, ordered timestamps)."""
    state = {"n": 0}

    def clock() -> datetime:
        ts = _EPOCH + timedelta(seconds=state["n"])
        state["n"] += 1
        return ts

    return clock


def _counter_id_gen() -> Callable[[], str]:
    state = {"n": 0}

    def gen() -> str:
        state["n"] += 1
        return f"trade-{state['n']:04d}"

    return gen


@pytest.fixture
def engine(repo: Repository) -> Iterator[TradingEngine]:
    yield TradingEngine(repo, clock=_fixed_clock(), id_gen=_counter_id_gen())


# -- buy --------------------------------------------------------------------
def test_buy_creates_holding_debits_cash_records_trade(
    repo: Repository, engine: TradingEngine
) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))

    result = engine.buy("u1", "p1", "BTC", quantity=Decimal("2"), price=Decimal("100"))

    assert result.realized_pnl == Decimal("0")
    assert result.trade.side is Side.BUY

    # cash debited
    portfolio = repo.get_portfolio("u1", "p1")
    assert portfolio is not None
    assert portfolio.cash == Decimal("800")
    assert portfolio.version == 2  # created at 1, bumped to 2

    # holding created
    holding = repo.get_holding("p1", "BTC")
    assert holding is not None
    assert holding.quantity == Decimal("2")
    assert holding.avg_cost == Decimal("100")

    # trade recorded
    trades = repo.list_trades("p1").items
    assert len(trades) == 1
    assert trades[0].trade_id == result.trade.trade_id
    assert trades[0].quantity == Decimal("2")


def test_second_buy_weights_avg_cost(repo: Repository, engine: TradingEngine) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("10000"))

    engine.buy("u1", "p1", "BTC", quantity=Decimal("2"), price=Decimal("100"))
    engine.buy("u1", "p1", "BTC", quantity=Decimal("3"), price=Decimal("200"))

    holding = repo.get_holding("p1", "BTC")
    assert holding is not None
    assert holding.quantity == Decimal("5")
    # (2*100 + 3*200) / 5 = 800/5 = 160
    assert holding.avg_cost == Decimal("160")

    portfolio = repo.get_portfolio("u1", "p1")
    assert portfolio is not None
    assert portfolio.cash == Decimal("10000") - Decimal("200") - Decimal("600")


def test_buy_insufficient_funds(repo: Repository, engine: TradingEngine) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("50"))
    with pytest.raises(InsufficientFunds):
        engine.buy("u1", "p1", "BTC", quantity=Decimal("1"), price=Decimal("100"))
    # nothing changed
    assert repo.get_holding("p1", "BTC") is None
    portfolio = repo.get_portfolio("u1", "p1")
    assert portfolio is not None
    assert portfolio.cash == Decimal("50")


def test_buy_unknown_portfolio(engine: TradingEngine) -> None:
    with pytest.raises(InvalidOrder):
        engine.buy("nobody", "nope", "BTC", quantity=Decimal("1"), price=Decimal("1"))


@pytest.mark.parametrize(
    ("quantity", "price"),
    [
        (Decimal("0"), Decimal("100")),
        (Decimal("-1"), Decimal("100")),
        (Decimal("1"), Decimal("0")),
        (Decimal("1"), Decimal("-5")),
    ],
)
def test_buy_invalid_quantity_or_price(
    repo: Repository, engine: TradingEngine, quantity: Decimal, price: Decimal
) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))
    with pytest.raises(InvalidOrder):
        engine.buy("u1", "p1", "BTC", quantity=quantity, price=price)


# -- sell -------------------------------------------------------------------
def test_sell_credits_cash_reduces_qty_realized_pnl(
    repo: Repository, engine: TradingEngine
) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))
    engine.buy("u1", "p1", "BTC", quantity=Decimal("4"), price=Decimal("100"))  # cash 600

    result = engine.sell("u1", "p1", "BTC", quantity=Decimal("1"), price=Decimal("150"))

    # (150 - 100) * 1 = 50
    assert result.realized_pnl == Decimal("50")
    assert result.trade.side is Side.SELL

    portfolio = repo.get_portfolio("u1", "p1")
    assert portfolio is not None
    assert portfolio.cash == Decimal("600") + Decimal("150")

    holding = repo.get_holding("p1", "BTC")
    assert holding is not None
    assert holding.quantity == Decimal("3")
    assert holding.avg_cost == Decimal("100")  # avg_cost unchanged on sell

    trades = repo.list_trades("p1").items
    assert len(trades) == 2


def test_full_sell_removes_holding(repo: Repository, engine: TradingEngine) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))
    engine.buy("u1", "p1", "BTC", quantity=Decimal("2"), price=Decimal("100"))

    result = engine.sell("u1", "p1", "BTC", quantity=Decimal("2"), price=Decimal("120"))

    assert result.realized_pnl == (Decimal("120") - Decimal("100")) * Decimal("2")
    assert repo.get_holding("p1", "BTC") is None

    portfolio = repo.get_portfolio("u1", "p1")
    assert portfolio is not None
    assert portfolio.cash == Decimal("800") + Decimal("240")


def test_sell_insufficient_holdings_too_few(repo: Repository, engine: TradingEngine) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))
    engine.buy("u1", "p1", "BTC", quantity=Decimal("1"), price=Decimal("100"))
    with pytest.raises(InsufficientHoldings):
        engine.sell("u1", "p1", "BTC", quantity=Decimal("2"), price=Decimal("100"))


def test_sell_unknown_symbol(repo: Repository, engine: TradingEngine) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))
    with pytest.raises(InsufficientHoldings):
        engine.sell("u1", "p1", "ETH", quantity=Decimal("1"), price=Decimal("100"))


def test_sell_unknown_portfolio(engine: TradingEngine) -> None:
    with pytest.raises(InvalidOrder):
        engine.sell("nobody", "nope", "BTC", quantity=Decimal("1"), price=Decimal("1"))


@pytest.mark.parametrize(
    ("quantity", "price"),
    [(Decimal("0"), Decimal("100")), (Decimal("1"), Decimal("0"))],
)
def test_sell_invalid_quantity_or_price(
    repo: Repository, engine: TradingEngine, quantity: Decimal, price: Decimal
) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))
    with pytest.raises(InvalidOrder):
        engine.sell("u1", "p1", "BTC", quantity=quantity, price=price)


# -- concurrency ------------------------------------------------------------
def test_buy_retries_on_stale_version_then_succeeds(
    repo: Repository, engine: TradingEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single out-of-band version bump between read and commit forces one
    retry; the second attempt reads the fresh version and succeeds."""
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))

    real_get = engine.models.Portfolio.get
    state = {"calls": 0}

    def racing_get(**kwargs: object) -> object:
        portfolio = real_get(**kwargs)
        state["calls"] += 1
        # On the first read only, bump the row's version out-of-band on a
        # *separate* fetched instance (a fresh instance put auto-increments
        # version), so the instance handed back to the engine keeps the now-stale
        # version and its commit loses the race, forcing one retry.
        if state["calls"] == 1 and portfolio is not None:
            stale = real_get(**kwargs)
            engine.models.Portfolio.put(stale)
        return portfolio

    monkeypatch.setattr(engine.models.Portfolio, "get", racing_get)

    result = engine.buy("u1", "p1", "BTC", quantity=Decimal("1"), price=Decimal("100"))
    assert result.trade.side is Side.BUY
    assert state["calls"] >= 2  # at least one retry happened

    holding = repo.get_holding("p1", "BTC")
    assert holding is not None
    assert holding.quantity == Decimal("1")


def test_buy_persistent_staleness_raises_trade_conflict(
    repo: Repository, engine: TradingEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every read is immediately invalidated out-of-band, so every commit loses
    the race; after MAX_RETRIES the engine raises TradeConflict."""
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))

    real_get = engine.models.Portfolio.get
    state = {"calls": 0}

    def always_racing_get(**kwargs: object) -> object:
        portfolio = real_get(**kwargs)
        state["calls"] += 1
        if portfolio is not None:
            stale = real_get(**kwargs)
            engine.models.Portfolio.put(stale)
        return portfolio

    monkeypatch.setattr(engine.models.Portfolio, "get", always_racing_get)

    with pytest.raises(TradeConflict):
        engine.buy("u1", "p1", "BTC", quantity=Decimal("1"), price=Decimal("100"))
    assert state["calls"] == MAX_RETRIES


def test_sell_retries_on_stale_version_then_succeeds(
    repo: Repository, engine: TradingEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single out-of-band version bump between read and commit forces the sell
    path to retry once; the second attempt reads the fresh version and succeeds."""
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))
    engine.buy("u1", "p1", "BTC", quantity=Decimal("2"), price=Decimal("100"))

    real_get = engine.models.Portfolio.get
    state = {"calls": 0}

    def racing_get(**kwargs: object) -> object:
        portfolio = real_get(**kwargs)
        state["calls"] += 1
        if state["calls"] == 1 and portfolio is not None:
            stale = real_get(**kwargs)
            engine.models.Portfolio.put(stale)
        return portfolio

    monkeypatch.setattr(engine.models.Portfolio, "get", racing_get)

    result = engine.sell("u1", "p1", "BTC", quantity=Decimal("1"), price=Decimal("150"))
    assert result.trade.side is Side.SELL
    assert state["calls"] >= 2

    holding = repo.get_holding("p1", "BTC")
    assert holding is not None
    assert holding.quantity == Decimal("1")


def test_sell_persistent_staleness_raises_trade_conflict(
    repo: Repository, engine: TradingEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every read is immediately invalidated out-of-band, so the sell loses the
    race on every commit; after MAX_RETRIES the engine raises TradeConflict."""
    repo.create_portfolio("u1", "p1", cash=Decimal("1000"))
    engine.buy("u1", "p1", "BTC", quantity=Decimal("5"), price=Decimal("100"))

    real_get = engine.models.Portfolio.get
    state = {"calls": 0}

    def always_racing_get(**kwargs: object) -> object:
        portfolio = real_get(**kwargs)
        state["calls"] += 1
        if portfolio is not None:
            stale = real_get(**kwargs)
            engine.models.Portfolio.put(stale)
        return portfolio

    monkeypatch.setattr(engine.models.Portfolio, "get", always_racing_get)

    with pytest.raises(TradeConflict):
        engine.sell("u1", "p1", "BTC", quantity=Decimal("1"), price=Decimal("150"))
    assert state["calls"] == MAX_RETRIES
