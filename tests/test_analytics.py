"""Analytics tests: snapshotting over time, returns math, and leaderboard ranking.

Covers the snapshot series (recent-first ordering + count), the returns
percentage math (exact Decimal), leaderboard ranking by total value descending
-- including the zero-padded ``rank_key`` edge where a ~9-value portfolio must
rank below a ~100-value one -- and the auth contract on every endpoint.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from hodlbook.analytics import Analytics
from hodlbook.api import create_app
from hodlbook.prices import MockPriceProvider, PriceCache
from hodlbook.repository import Repository
from hodlbook.storage import Side, build_table, create_table
from hodlbook.trading import TradingEngine
from hodlbook.valuation import Valuator


class _Clock:
    """A mutable, injectable clock so snapshot timestamps are deterministic."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def dynamodb_client() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_table(client)
        yield client


@pytest.fixture
def repo(dynamodb_client: Any) -> Repository:
    return Repository(build_table(dynamodb_client))


def _analytics(repo: Repository, provider: MockPriceProvider, clock: _Clock) -> Analytics:
    cache = PriceCache(repo, provider, clock=clock, ttl_seconds=0)
    valuator = Valuator(repo, cache)
    return Analytics(repo, valuator, clock=clock)


def _seed_portfolio(
    repo: Repository, clock: _Clock, *, user_id: str, portfolio_id: str, qty: Decimal
) -> None:
    """Create a portfolio holding ``qty`` bitcoin via the trading engine."""
    repo.create_portfolio(user_id, portfolio_id, Decimal("1000000"))
    engine = TradingEngine(repo, clock=clock)
    engine.buy(user_id, portfolio_id, "bitcoin", qty, Decimal("100"))


# -- snapshot series + returns ----------------------------------------------
def test_snapshots_over_time_order_count_and_returns(repo: Repository) -> None:
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    # 1 bitcoin held; base price 100 -> total drifts purely with the provider.
    provider = MockPriceProvider({"bitcoin": Decimal("100")}, drift_step=0)
    _seed_portfolio(repo, clock, user_id="u1", portfolio_id="p1", qty=Decimal("1"))
    analytics = _analytics(repo, provider, clock)

    # cash after buying 1 @ 100 = 999900. holdings_value = price * 1.
    # step 0 -> price 100 -> total 1000000; step 10 -> price 110 -> total 1000010.
    first = analytics.take_snapshot("u1", "p1")
    clock.advance(60)
    provider.drift_step = 10
    second = analytics.take_snapshot("u1", "p1")

    assert first.total_value == Decimal("1000000")
    assert second.total_value == Decimal("1000010")

    series = analytics.series("p1").items
    assert len(series) == 2
    # Recent-first: the second (later taken_at) snapshot heads the page.
    assert series[0].taken_at == second.taken_at
    assert series[1].taken_at == first.taken_at
    assert series[0].taken_at > series[1].taken_at

    # return_pct = (1000010 - 1000000) / 1000000 * 100 = 0.001
    assert (
        analytics.returns("p1")
        == (Decimal("1000010") - Decimal("1000000")) / Decimal("1000000") * 100
    )


def test_series_limit_caps_page(repo: Repository) -> None:
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    provider = MockPriceProvider({})
    repo.create_portfolio("u1", "p1", Decimal("10"))
    analytics = _analytics(repo, provider, clock)
    for _ in range(3):
        analytics.take_snapshot("u1", "p1")
        clock.advance(60)

    page = analytics.series("p1", limit=2)
    assert len(page.items) == 2


def test_returns_zero_with_fewer_than_two_snapshots(repo: Repository) -> None:
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    provider = MockPriceProvider({"bitcoin": Decimal("100")})
    _seed_portfolio(repo, clock, user_id="u1", portfolio_id="p1", qty=Decimal("1"))
    analytics = _analytics(repo, provider, clock)

    assert analytics.returns("p1") == Decimal("0")  # no snapshots
    analytics.take_snapshot("u1", "p1")
    assert analytics.returns("p1") == Decimal("0")  # one snapshot


def test_returns_zero_when_baseline_value_is_zero(repo: Repository) -> None:
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    provider = MockPriceProvider({})
    repo.create_portfolio("u1", "p1", Decimal("0"))  # no cash, no holdings
    analytics = _analytics(repo, provider, clock)

    analytics.take_snapshot("u1", "p1")
    clock.advance(60)
    analytics.take_snapshot("u1", "p1")
    # First snapshot total_value is 0 -> no baseline -> 0.
    assert analytics.returns("p1") == Decimal("0")


def test_take_snapshot_unknown_portfolio_raises(repo: Repository) -> None:
    from hodlbook.errors import InvalidOrder

    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    analytics = _analytics(repo, MockPriceProvider({}), clock)
    with pytest.raises(InvalidOrder):
        analytics.take_snapshot("u1", "ghost")


# -- leaderboard ranking ----------------------------------------------------
def test_leaderboard_ranks_by_value_including_padded_key_edge(repo: Repository) -> None:
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    provider = MockPriceProvider({"bitcoin": Decimal("1")})
    analytics = _analytics(repo, provider, clock)

    # Three portfolios with deliberately uneven totals. The "~9" vs "~100" pair
    # is the zero-padding edge: lexical "9" > "100", so without left-padding the
    # 9-value portfolio would wrongly outrank the 100-value one.
    repo.create_portfolio("ua", "pa", Decimal("9"))
    repo.create_portfolio("ub", "pb", Decimal("100"))
    repo.create_portfolio("uc", "pc", Decimal("50"))
    analytics.take_snapshot("ua", "pa")
    analytics.take_snapshot("ub", "pb")
    analytics.take_snapshot("uc", "pc")

    board = analytics.leaderboard(10)
    assert [e.portfolio_id for e in board] == ["pb", "pc", "pa"]
    assert [e.total_value for e in board] == [Decimal("100"), Decimal("50"), Decimal("9")]


def test_leaderboard_limit_returns_top_n(repo: Repository) -> None:
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    analytics = _analytics(repo, MockPriceProvider({}), clock)
    for i, cash in enumerate([Decimal("5"), Decimal("200"), Decimal("75"), Decimal("3")]):
        repo.create_portfolio(f"u{i}", f"p{i}", cash)
        analytics.take_snapshot(f"u{i}", f"p{i}")

    top2 = analytics.leaderboard(2)
    assert [e.total_value for e in top2] == [Decimal("200"), Decimal("75")]


def test_resnapshot_overwrites_leaderboard_entry(repo: Repository) -> None:
    # The leaderboard primary key is stable per portfolio, so re-snapshotting
    # replaces the entry rather than producing duplicates.
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    provider = MockPriceProvider({})
    repo.create_portfolio("u1", "p1", Decimal("10"))
    analytics = _analytics(repo, provider, clock)

    analytics.take_snapshot("u1", "p1")
    clock.advance(60)
    analytics.take_snapshot("u1", "p1")

    board = analytics.leaderboard(10)
    assert len([e for e in board if e.portfolio_id == "p1"]) == 1


# -- API: app wiring + auth -------------------------------------------------
@pytest.fixture
def analytics_app(dynamodb_client: Any) -> Iterator[tuple[TestClient, _Clock, MockPriceProvider]]:
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    provider = MockPriceProvider({"bitcoin": Decimal("100")}, drift_step=0)
    app = create_app(dynamodb_client, provider=provider, clock=clock)
    with TestClient(app) as client:
        yield client, clock, provider


@pytest.fixture
def auth_headers(repo: Repository) -> dict[str, str]:
    raw, _ = repo.issue_api_key("u1")
    return {"Authorization": f"Bearer {raw}"}


def test_api_snapshot_series_and_returns_flow(
    analytics_app: tuple[TestClient, _Clock, MockPriceProvider],
    auth_headers: dict[str, str],
) -> None:
    client, clock, provider = analytics_app
    client.post(
        "/portfolios",
        json={"user_id": "u1", "portfolio_id": "p1", "cash": "1000000"},
        headers=auth_headers,
    )
    client.post(
        "/portfolios/u1/p1/orders",
        json={"symbol": "bitcoin", "side": Side.BUY.value, "quantity": "1", "price": "100"},
        headers=auth_headers,
    )

    r1 = client.post("/portfolios/u1/p1/snapshots", headers=auth_headers)
    assert r1.status_code == 201
    assert Decimal(r1.json()["total_value"]) == Decimal("1000000")

    clock.advance(60)
    provider.drift_step = 10  # bitcoin 100 -> 110
    r2 = client.post("/portfolios/u1/p1/snapshots", headers=auth_headers)
    assert r2.status_code == 201
    assert Decimal(r2.json()["total_value"]) == Decimal("1000010")

    series = client.get("/portfolios/u1/p1/snapshots", headers=auth_headers)
    items = series.json()["items"]
    assert len(items) == 2
    assert items[0]["taken_at"] > items[1]["taken_at"]  # recent-first

    returns = client.get("/portfolios/u1/p1/returns", headers=auth_headers)
    body = returns.json()
    assert len(body["series"]["items"]) == 2
    assert (
        Decimal(body["return_pct"])
        == (Decimal("1000010") - Decimal("1000000")) / Decimal("1000000") * 100
    )


def test_api_snapshot_unknown_portfolio_404(
    analytics_app: tuple[TestClient, _Clock, MockPriceProvider],
    auth_headers: dict[str, str],
) -> None:
    client, _, _ = analytics_app
    resp = client.post("/portfolios/u1/ghost/snapshots", headers=auth_headers)
    assert resp.status_code == 404


def test_api_snapshots_wrong_tenant_403(
    analytics_app: tuple[TestClient, _Clock, MockPriceProvider],
    auth_headers: dict[str, str],
) -> None:
    client, _, _ = analytics_app
    # auth_headers authenticates u1; hitting u2's resources -> 403.
    assert client.post("/portfolios/u2/p1/snapshots", headers=auth_headers).status_code == 403
    assert client.get("/portfolios/u2/p1/snapshots", headers=auth_headers).status_code == 403
    assert client.get("/portfolios/u2/p1/returns", headers=auth_headers).status_code == 403


def test_api_snapshots_no_token_401(
    analytics_app: tuple[TestClient, _Clock, MockPriceProvider],
) -> None:
    client, _, _ = analytics_app
    assert client.post("/portfolios/u1/p1/snapshots").status_code == 401
    assert client.get("/portfolios/u1/p1/snapshots").status_code == 401
    assert client.get("/portfolios/u1/p1/returns").status_code == 401


def test_api_leaderboard_any_valid_token_and_shape(
    analytics_app: tuple[TestClient, _Clock, MockPriceProvider],
    repo: Repository,
    auth_headers: dict[str, str],
) -> None:
    client, clock, _ = analytics_app
    # Seed three portfolios under different principals via the analytics service.
    analytics = _analytics(repo, MockPriceProvider({}), clock)
    repo.create_portfolio("ua", "pa", Decimal("9"))
    repo.create_portfolio("ub", "pb", Decimal("100"))
    repo.create_portfolio("uc", "pc", Decimal("50"))
    analytics.take_snapshot("ua", "pa")
    analytics.take_snapshot("ub", "pb")
    analytics.take_snapshot("uc", "pc")

    # A valid token for u1 (who owns none of these) can still read -- cross-tenant.
    resp = client.get("/leaderboard", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert [e["portfolio_id"] for e in entries] == ["pb", "pc", "pa"]
    assert [e["rank"] for e in entries] == [1, 2, 3]
    # Cross-tenant safety: no user_id / cash / holdings leak.
    assert set(entries[0].keys()) == {"portfolio_id", "total_value", "rank"}


def test_api_leaderboard_no_token_401(
    analytics_app: tuple[TestClient, _Clock, MockPriceProvider],
) -> None:
    client, _, _ = analytics_app
    assert client.get("/leaderboard").status_code == 401
