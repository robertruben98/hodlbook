"""Price-alert evaluator for hodlbook.

An :class:`AlertEvaluator` pulls current prices for a set of symbols, scans the
armed (un-triggered) alerts on each symbol via the GSI2 ``by_symbol`` index, and
fires any whose threshold the price has crossed -- ABOVE alerts when
``price >= threshold``, BELOW alerts when ``price <= threshold``.

Firing is idempotent: marking an alert triggered is guarded with a conditional
put, so a second pass over the same prices fires nothing. A lost race (another
evaluator firing the same alert first) raises
:class:`pydynantic.ConditionCheckFailedError`, which is caught and skipped.

All comparisons use :class:`~decimal.Decimal` -- floats never touch price math.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pydynantic

from .repository import Repository
from .storage import Direction


class PriceSource(typing.Protocol):
    """A source of current USD prices keyed by symbol.

    :class:`~hodlbook.prices.PriceCache` already satisfies this Protocol.
    """

    def get_cached_prices(self, symbols: list[str]) -> dict[str, Decimal]: ...


@dataclass(frozen=True)
class FiredAlert:
    """An alert that fired, paired with the price that crossed its threshold."""

    alert: Any
    price: Decimal


class AlertEvaluator:
    """Evaluates armed alerts against current prices and fires the crossers."""

    def __init__(self, repo: Repository, prices: PriceSource) -> None:
        self.repo = repo
        self.prices = prices

    def evaluate(self, symbols: list[str]) -> list[FiredAlert]:
        """Fire every armed alert on ``symbols`` whose threshold the price crosses."""
        current = self.prices.get_cached_prices(symbols)
        fired: list[FiredAlert] = []

        for symbol in symbols:
            price = current[symbol]
            for alert in self.repo.list_alerts_by_symbol(symbol):
                if alert.triggered is not False:
                    continue
                if alert.direction is Direction.ABOVE:
                    crossed = price >= alert.threshold
                else:  # Direction.BELOW
                    crossed = price <= alert.threshold
                if not crossed:
                    continue
                try:
                    self.repo.mark_alert_triggered(alert.portfolio_id, alert.alert_id)
                except pydynantic.ConditionCheckFailedError:
                    # Lost race: another evaluator already fired this alert.
                    continue
                fired.append(FiredAlert(alert=alert, price=price))

        return fired
