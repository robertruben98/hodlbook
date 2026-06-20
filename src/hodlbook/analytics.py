"""Portfolio analytics: value snapshots, a returns series, and a leaderboard.

An :class:`Analytics` service marks a portfolio to market through the
:class:`~hodlbook.valuation.Valuator`, persists the result as a timestamped
:class:`Snapshot`, and upserts a :class:`LeaderboardEntry` so the portfolio can
be ranked across tenants. The leaderboard sorts on a zero-padded ``rank_key``
because the GSI sort key is String-typed: lexical order over a fixed-width
left-padded integer equals numeric order, so a descending GSI query returns the
highest-valued portfolios first.

The clock is injected so snapshot timestamps -- and therefore ordering and the
returns window -- are deterministic in tests. All money is
:class:`~decimal.Decimal`; floats never touch the math.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydynantic import Page

from .repository import Repository
from .valuation import Valuator

#: Width of the zero-padded value field in a leaderboard ``rank_key``. Values
#: are stored as integer cents, so 20 digits covers up to ~1.8e16 cents
#: (~$1.8e14) with room to spare while keeping every key the same length.
_RANK_WIDTH = 20


def _rank_key(total_value: Decimal, portfolio_id: str) -> str:
    """Encode ``total_value`` into a lexically-sortable GSI sort key.

    Scales to integer cents, then left zero-pads to a fixed width so string order
    equals numeric order (``9`` -> ``...0900`` sorts below ``100`` -> ``...10000``).
    The ``portfolio_id`` suffix makes the key unique and breaks value ties.
    """
    value_scaled = int((total_value * 100).to_integral_value())
    return f"{value_scaled:0{_RANK_WIDTH}d}#{portfolio_id}"


class Analytics:
    """Snapshotting, returns, and leaderboard ranking over a portfolio's value."""

    def __init__(
        self,
        repo: Repository,
        valuator: Valuator,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repo = repo
        self.valuator = valuator
        self._clock = clock

    def take_snapshot(self, user_id: str, portfolio_id: str) -> Any:
        """Mark to market, persist a :class:`Snapshot`, and refresh the leaderboard.

        Raises :class:`~hodlbook.errors.InvalidOrder` (-> 422) if the portfolio
        does not exist -- the valuator does the existence check.
        """
        v = self.valuator.value(user_id, portfolio_id)
        taken_at = self._clock().isoformat()
        snapshot = self.repo.put_snapshot(
            portfolio_id,
            taken_at,
            total_value=v.total_value,
            cash=v.cash,
            holdings_value=v.holdings_value,
            total_unrealized_pnl=v.total_unrealized_pnl,
        )
        self.repo.upsert_leaderboard_entry(
            user_id,
            portfolio_id,
            total_value=v.total_value,
            rank_key=_rank_key(v.total_value, portfolio_id),
            taken_at=taken_at,
        )
        return snapshot

    def series(
        self, portfolio_id: str, *, cursor: str | None = None, limit: int | None = None
    ) -> Page[Any]:
        """A most-recent-first page of value snapshots for a portfolio."""
        return self.repo.list_snapshots(portfolio_id, cursor=cursor, limit=limit)

    def returns(self, portfolio_id: str) -> Decimal:
        """Percent change of total value from the first to the latest snapshot.

        Returns ``Decimal("0")`` when fewer than two snapshots exist or the first
        value is zero (no baseline to measure against).
        """
        # An unbounded list_snapshots returns every snapshot most-recent-first;
        # the latest is the head and the first (baseline) is the tail.
        snapshots = self.repo.list_snapshots(portfolio_id).items
        if len(snapshots) < 2:
            return Decimal("0")
        latest_value: Decimal = snapshots[0].total_value
        first_value: Decimal = snapshots[-1].total_value
        if first_value == 0:
            return Decimal("0")
        return (latest_value - first_value) / first_value * Decimal("100")

    def leaderboard(self, limit: int) -> list[Any]:
        """The top-``limit`` portfolios ranked by total value, highest first."""
        return self.repo.top_leaderboard(limit)
