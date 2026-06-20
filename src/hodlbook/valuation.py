"""Mark-to-market valuation of a portfolio.

A :class:`Valuator` prices every holding through a :class:`~hodlbook.prices.PriceCache`,
computes per-holding market value and unrealized P&L, and rolls them up with the
portfolio's cash into a :class:`Valuation`. All arithmetic is :class:`~decimal.Decimal`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .errors import InvalidOrder
from .prices import PriceCache
from .repository import Repository


@dataclass(frozen=True)
class HoldingValuation:
    """A single holding marked to market."""

    symbol: str
    quantity: Decimal
    avg_cost: Decimal
    price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True)
class Valuation:
    """A whole-portfolio snapshot: cash plus marked-to-market holdings."""

    cash: Decimal
    holdings: list[HoldingValuation]
    holdings_value: Decimal
    total_value: Decimal
    total_unrealized_pnl: Decimal


class Valuator:
    """Computes a mark-to-market :class:`Valuation` for a portfolio."""

    def __init__(self, repo: Repository, cache: PriceCache) -> None:
        self.repo = repo
        self.cache = cache

    def value(self, user_id: str, portfolio_id: str) -> Valuation:
        portfolio = self.repo.get_portfolio(user_id, portfolio_id)
        if portfolio is None:
            raise InvalidOrder(f"unknown portfolio {user_id}/{portfolio_id}")

        holdings = self.repo.get_holdings(portfolio_id)
        symbols = [h.symbol for h in holdings]
        prices = self.cache.get_cached_prices(symbols) if symbols else {}

        valued: list[HoldingValuation] = []
        holdings_value = Decimal("0")
        total_unrealized_pnl = Decimal("0")
        for h in holdings:
            price = prices[h.symbol]
            market_value = h.quantity * price
            unrealized_pnl = (price - h.avg_cost) * h.quantity
            holdings_value += market_value
            total_unrealized_pnl += unrealized_pnl
            valued.append(
                HoldingValuation(
                    symbol=h.symbol,
                    quantity=h.quantity,
                    avg_cost=h.avg_cost,
                    price=price,
                    market_value=market_value,
                    unrealized_pnl=unrealized_pnl,
                )
            )

        cash: Decimal = portfolio.cash
        return Valuation(
            cash=cash,
            holdings=valued,
            holdings_value=holdings_value,
            total_value=cash + holdings_value,
            total_unrealized_pnl=total_unrealized_pnl,
        )
