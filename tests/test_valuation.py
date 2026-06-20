"""Tests for mark-to-market portfolio valuation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from hodlbook.errors import InvalidOrder
from hodlbook.prices import MockPriceProvider, PriceCache
from hodlbook.repository import Repository
from hodlbook.trading import TradingEngine
from hodlbook.valuation import Valuator


def _valuator(repo: Repository, prices: dict[str, Decimal]) -> Valuator:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cache = PriceCache(repo, MockPriceProvider(prices), clock=lambda: now)
    return Valuator(repo, cache)


def test_value_two_holdings(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    engine = TradingEngine(repo)
    # Buy 2 BTC @ 50000 (cost 100000 -> cash 0... use a bigger cash buffer)
    repo.create_portfolio("u2", "p2", cash=Decimal("1000000"))
    engine.buy("u2", "p2", "bitcoin", Decimal("2"), Decimal("50000"))
    engine.buy("u2", "p2", "ethereum", Decimal("10"), Decimal("2000"))

    # cash now: 1000000 - 100000 - 20000 = 880000
    valuator = _valuator(repo, {"bitcoin": Decimal("60000"), "ethereum": Decimal("1500")})
    v = valuator.value("u2", "p2")

    assert v.cash == Decimal("880000")

    by_symbol = {h.symbol: h for h in v.holdings}
    btc = by_symbol["bitcoin"]
    eth = by_symbol["ethereum"]

    # BTC: 2 @ avg_cost 50000, price 60000
    assert btc.market_value == Decimal("120000")  # 2 * 60000
    assert btc.unrealized_pnl == Decimal("20000")  # (60000-50000)*2

    # ETH: 10 @ avg_cost 2000, price 1500
    assert eth.market_value == Decimal("15000")  # 10 * 1500
    assert eth.unrealized_pnl == Decimal("-5000")  # (1500-2000)*10

    assert v.holdings_value == Decimal("135000")  # 120000 + 15000
    assert v.total_value == Decimal("1015000")  # 880000 + 135000
    assert v.total_unrealized_pnl == Decimal("15000")  # 20000 - 5000


def test_value_no_holdings(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("500"))
    valuator = _valuator(repo, {})
    v = valuator.value("u1", "p1")
    assert v.holdings == []
    assert v.holdings_value == Decimal("0")
    assert v.total_value == Decimal("500")
    assert v.total_unrealized_pnl == Decimal("0")


def test_value_unknown_portfolio_raises(repo: Repository) -> None:
    valuator = _valuator(repo, {})
    with pytest.raises(InvalidOrder):
        valuator.value("nope", "nope")
