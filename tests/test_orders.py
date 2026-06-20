"""Behavior tests for the advanced-order executor and order API endpoints.

Covers LIMIT (buy/sell trigger), DCA (per-interval fills + stop), cancellation,
the insufficient-funds skip path, list/get, and the guarded double-pass no-op.
All against a mocked DynamoDB table with injected clocks and id generators.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hodlbook.orders import ExecutionResult, FilledOrder, OrderExecutor, SkippedOrder
from hodlbook.prices import MockPriceProvider, PriceCache
from hodlbook.repository import Repository
from hodlbook.storage import OrderStatus, OrderType, Side
from hodlbook.trading import TradingEngine

_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Clock:
    """A mutable injected clock the tests advance by hand."""

    def __init__(self, start: datetime = _EPOCH) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _engine(repo: Repository, clock: _Clock) -> TradingEngine:
    state = {"n": 0}

    def id_gen() -> str:
        state["n"] += 1
        return f"trade-{state['n']:04d}"

    return TradingEngine(repo, clock=clock, id_gen=id_gen)


def _executor(repo: Repository, prices: dict[str, Decimal], clock: _Clock) -> OrderExecutor:
    cache = PriceCache(repo, MockPriceProvider(prices), clock=clock)
    return OrderExecutor(repo, _engine(repo, clock), cache, clock=clock)


# -- LIMIT ------------------------------------------------------------------
def test_limit_buy_fills_only_at_or_below_limit(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        limit_price=Decimal("45000"),
    )

    clock = _Clock()
    # Price above the limit -> not filled.
    res = _executor(repo, {"bitcoin": Decimal("50000")}, clock).execute(["bitcoin"])
    assert res.fills == []
    assert repo.get_order("p1", "o1").status is OrderStatus.OPEN

    # Price drops to the limit -> fills at market price. Advance past the price
    # cache TTL (60s) so the new, lower price is fetched rather than the cached one.
    clock.advance(120)
    res = _executor(repo, {"bitcoin": Decimal("45000")}, clock).execute(["bitcoin"])
    assert len(res.fills) == 1
    assert isinstance(res.fills[0], FilledOrder)
    assert res.fills[0].fill_price == Decimal("45000")
    assert repo.get_order("p1", "o1").status is OrderStatus.FILLED
    # Status change rewrote the GSI2 partition -> no longer an open order.
    assert repo.list_open_orders_by_symbol("bitcoin") == []


def test_limit_sell_fills_only_at_or_above_limit(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    # Seed a holding to sell.
    repo.upsert_holding("p1", "bitcoin", Decimal("5"), Decimal("30000"))
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.SELL,
        OrderType.LIMIT,
        Decimal("1"),
        limit_price=Decimal("60000"),
    )

    clock = _Clock()
    # Below the limit -> not filled.
    res = _executor(repo, {"bitcoin": Decimal("55000")}, clock).execute(["bitcoin"])
    assert res.fills == []
    assert repo.get_order("p1", "o1").status is OrderStatus.OPEN

    # Rises to the limit -> fills (advance past the price cache TTL).
    clock.advance(120)
    res = _executor(repo, {"bitcoin": Decimal("60000")}, clock).execute(["bitcoin"])
    assert len(res.fills) == 1
    assert repo.get_order("p1", "o1").status is OrderStatus.FILLED


# -- DCA --------------------------------------------------------------------
def test_dca_fills_per_due_interval_and_stops_after_runs(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("200000"))
    clock = _Clock()
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.DCA,
        Decimal("1"),
        interval_seconds=3600,
        next_run=clock(),
        remaining_runs=3,
    )
    prices = {"bitcoin": Decimal("50000")}

    # Run 1: due immediately.
    res = _executor(repo, prices, clock).execute(["bitcoin"])
    assert len(res.fills) == 1
    order = repo.get_order("p1", "o1")
    assert order.remaining_runs == 2
    assert order.status is OrderStatus.OPEN

    # Same instant: next_run is now in the future -> not due, no fill.
    res = _executor(repo, prices, clock).execute(["bitcoin"])
    assert res.fills == []
    assert repo.get_order("p1", "o1").remaining_runs == 2

    # Advance one interval -> run 2.
    clock.advance(3600)
    res = _executor(repo, prices, clock).execute(["bitcoin"])
    assert len(res.fills) == 1
    assert repo.get_order("p1", "o1").remaining_runs == 1

    # Advance again -> run 3, the last one: becomes FILLED.
    clock.advance(3600)
    res = _executor(repo, prices, clock).execute(["bitcoin"])
    assert len(res.fills) == 1
    final = repo.get_order("p1", "o1")
    assert final.remaining_runs == 0
    assert final.status is OrderStatus.FILLED
    assert repo.list_open_orders_by_symbol("bitcoin") == []

    # Further passes do nothing (no open orders).
    clock.advance(3600)
    assert _executor(repo, prices, clock).execute(["bitcoin"]).fills == []


# -- cancel -----------------------------------------------------------------
def test_cancel_marks_cancelled_and_removes_from_open_query(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        limit_price=Decimal("45000"),
    )
    assert len(repo.list_open_orders_by_symbol("bitcoin")) == 1

    repo.cancel_order("p1", "o1")
    assert repo.get_order("p1", "o1").status is OrderStatus.CANCELLED
    assert repo.list_open_orders_by_symbol("bitcoin") == []

    # A cancelled order is never eligible for a fill.
    clock = _Clock()
    res = _executor(repo, {"bitcoin": Decimal("40000")}, clock).execute(["bitcoin"])
    assert res.fills == []


def test_cancel_missing_order_is_noop(repo: Repository) -> None:
    # No order at that key: cancel returns cleanly without raising.
    repo.cancel_order("p1", "does-not-exist")
    assert repo.get_order("p1", "does-not-exist") is None


# -- insufficient funds -----------------------------------------------------
def test_insufficient_funds_leaves_order_open_and_skips(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100"))  # too little for 1 BTC
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        limit_price=Decimal("45000"),
    )

    clock = _Clock()
    res = _executor(repo, {"bitcoin": Decimal("40000")}, clock).execute(["bitcoin"])

    assert res.fills == []
    assert len(res.skipped) == 1
    assert isinstance(res.skipped[0], SkippedOrder)
    assert repo.get_order("p1", "o1").status is OrderStatus.OPEN
    # Still queryable as open so a later pass can retry once funded.
    assert len(repo.list_open_orders_by_symbol("bitcoin")) == 1


def test_insufficient_holdings_on_dca_sell_advances_without_burning_run(
    repo: Repository,
) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))  # no holding to sell
    clock = _Clock()
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.SELL,
        OrderType.DCA,
        Decimal("1"),
        interval_seconds=3600,
        next_run=clock(),
        remaining_runs=3,
    )

    res = _executor(repo, {"bitcoin": Decimal("50000")}, clock).execute(["bitcoin"])

    assert res.fills == []
    assert len(res.skipped) == 1
    order = repo.get_order("p1", "o1")
    assert order.status is OrderStatus.OPEN
    # Run NOT burned, but next_run advanced so we do not busy-retry the same tick.
    assert order.remaining_runs == 3
    assert order.next_run == _EPOCH + timedelta(seconds=3600)


# -- double pass / guarded no-op --------------------------------------------
def test_double_pass_does_not_double_fill(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        limit_price=Decimal("50000"),
    )
    clock = _Clock()
    prices = {"bitcoin": Decimal("50000")}

    first = _executor(repo, prices, clock).execute(["bitcoin"])
    assert len(first.fills) == 1

    # Order is now FILLED and gone from the open partition -> nothing to fill.
    second = _executor(repo, prices, clock).execute(["bitcoin"])
    assert second.fills == []

    # Exactly one trade was recorded.
    trades = repo.list_trades("p1").items
    assert len(trades) == 1


def test_cancel_after_fill_is_guarded_noop(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        limit_price=Decimal("50000"),
    )
    clock = _Clock()
    _executor(repo, {"bitcoin": Decimal("50000")}, clock).execute(["bitcoin"])
    assert repo.get_order("p1", "o1").status is OrderStatus.FILLED

    # Cancelling a filled order is a guarded no-op; status stays FILLED.
    repo.cancel_order("p1", "o1")
    assert repo.get_order("p1", "o1").status is OrderStatus.FILLED


def test_limit_fill_lost_status_race_keeps_trade(repo: Repository) -> None:
    # The atomic trade lands, but the follow-up status update loses a race
    # (mark_order_filled raises). The pass must not crash and not record a fill.
    from pydynantic import ConditionCheckFailedError

    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        limit_price=Decimal("50000"),
    )

    def race(_order: Any) -> None:
        raise ConditionCheckFailedError("lost race")

    repo.mark_order_filled = race  # type: ignore[method-assign]

    clock = _Clock()
    res = _executor(repo, {"bitcoin": Decimal("50000")}, clock).execute(["bitcoin"])
    assert res.fills == []
    # The trade still landed atomically.
    assert len(repo.list_trades("p1").items) == 1


def test_dca_advance_lost_race_is_noop(repo: Repository) -> None:
    from pydynantic import ConditionCheckFailedError

    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    clock = _Clock()
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.DCA,
        Decimal("1"),
        interval_seconds=3600,
        next_run=clock(),
        remaining_runs=3,
    )

    def race(_order: Any) -> None:
        raise ConditionCheckFailedError("lost race")

    repo.advance_dca = race  # type: ignore[method-assign]

    res = _executor(repo, {"bitcoin": Decimal("50000")}, clock).execute(["bitcoin"])
    # The fill is still recorded; only the schedule persistence lost the race.
    assert len(res.fills) == 1


def test_dca_with_no_remaining_runs_is_ineligible(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    clock = _Clock()
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.DCA,
        Decimal("1"),
        interval_seconds=3600,
        next_run=clock(),
        remaining_runs=0,
    )
    res = _executor(repo, {"bitcoin": Decimal("50000")}, clock).execute(["bitcoin"])
    assert res.fills == []
    assert res.skipped == []


def test_market_order_is_never_scanned(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.MARKET,
        Decimal("1"),
    )
    clock = _Clock()
    res = _executor(repo, {"bitcoin": Decimal("50000")}, clock).execute(["bitcoin"])
    assert res.fills == []
    assert isinstance(res, ExecutionResult)


# -- list / get repo --------------------------------------------------------
def test_list_and_get_orders(repo: Repository) -> None:
    repo.create_portfolio("u1", "p1", cash=Decimal("100000"))
    repo.create_order(
        "p1",
        "o1",
        "u1",
        "bitcoin",
        Side.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        limit_price=Decimal("45000"),
    )
    repo.create_order(
        "p1",
        "o2",
        "u1",
        "ethereum",
        Side.BUY,
        OrderType.LIMIT,
        Decimal("2"),
        limit_price=Decimal("2000"),
    )
    ids = {o.order_id for o in repo.list_orders("p1")}
    assert ids == {"o1", "o2"}
    assert repo.get_order("p1", "o1").symbol == "bitcoin"
    assert repo.get_order("p1", "missing") is None


# -- API endpoints ----------------------------------------------------------
@pytest.fixture
def authed_client(api_client: TestClient, auth_headers: dict[str, str]) -> Iterator[Any]:
    """The shared api_client with auth headers attached and a portfolio created."""
    api_client.headers.update(auth_headers)
    api_client.post("/portfolios", json={"user_id": "u1", "portfolio_id": "p1", "cash": "100000"})
    yield api_client


def test_api_create_limit_order(authed_client: TestClient) -> None:
    resp = authed_client.post(
        "/portfolios/u1/p1/orders/limit",
        json={"symbol": "bitcoin", "side": "BUY", "quantity": "1", "limit_price": "45000"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["order_type"] == "LIMIT"
    assert body["status"] == "OPEN"
    assert body["limit_price"] == "45000"


def test_api_create_dca_order(authed_client: TestClient) -> None:
    resp = authed_client.post(
        "/portfolios/u1/p1/orders/dca",
        json={
            "symbol": "bitcoin",
            "side": "BUY",
            "quantity": "1",
            "interval_seconds": 3600,
            "total_runs": 5,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["order_type"] == "DCA"
    assert body["remaining_runs"] == 5
    assert body["next_run"] is not None


def test_api_list_get_cancel_order(authed_client: TestClient) -> None:
    created = authed_client.post(
        "/portfolios/u1/p1/orders/limit",
        json={"symbol": "bitcoin", "side": "BUY", "quantity": "1", "limit_price": "45000"},
    ).json()
    order_id = created["order_id"]

    listed = authed_client.get("/portfolios/u1/p1/orders")
    assert listed.status_code == 200
    assert [o["order_id"] for o in listed.json()] == [order_id]

    got = authed_client.get(f"/portfolios/u1/p1/orders/{order_id}")
    assert got.status_code == 200
    assert got.json()["order_id"] == order_id

    cancelled = authed_client.delete(f"/portfolios/u1/p1/orders/{order_id}")
    assert cancelled.status_code == 204
    assert authed_client.get(f"/portfolios/u1/p1/orders/{order_id}").json()["status"] == "CANCELLED"


def test_api_get_missing_order_404(authed_client: TestClient) -> None:
    resp = authed_client.get("/portfolios/u1/p1/orders/nope")
    assert resp.status_code == 404
    assert resp.json()["error"] == "OrderNotFound"


def test_api_cancel_missing_order_404(authed_client: TestClient) -> None:
    resp = authed_client.delete("/portfolios/u1/p1/orders/nope")
    assert resp.status_code == 404


def test_api_order_on_missing_portfolio_404(authed_client: TestClient) -> None:
    resp = authed_client.post(
        "/portfolios/u1/ghost/orders/limit",
        json={"symbol": "bitcoin", "side": "BUY", "quantity": "1", "limit_price": "45000"},
    )
    assert resp.status_code == 404


def test_api_dca_on_missing_portfolio_404(authed_client: TestClient) -> None:
    resp = authed_client.post(
        "/portfolios/u1/ghost/orders/dca",
        json={
            "symbol": "bitcoin",
            "side": "BUY",
            "quantity": "1",
            "interval_seconds": 3600,
            "total_runs": 5,
        },
    )
    assert resp.status_code == 404


def test_api_orders_require_auth(api_client: TestClient) -> None:
    # No auth header -> 401 from get_principal.
    resp = api_client.get("/portfolios/u1/p1/orders")
    assert resp.status_code == 401


def test_api_orders_reject_other_tenant(api_client: TestClient, repo: Repository) -> None:
    raw, _ = repo.issue_api_key("intruder")
    resp = api_client.get("/portfolios/u1/p1/orders", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 403
