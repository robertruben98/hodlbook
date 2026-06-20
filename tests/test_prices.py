"""Tests for price providers and the TTL-aware PriceCache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from hodlbook.errors import UnknownSymbol
from hodlbook.prices import HttpPriceProvider, MockPriceProvider, PriceCache
from hodlbook.repository import Repository


def test_mock_provider_is_deterministic_per_step() -> None:
    provider = MockPriceProvider({"bitcoin": Decimal("100")})
    assert provider.get_prices(["bitcoin"]) == provider.get_prices(["bitcoin"])
    assert provider.get_prices(["bitcoin"])["bitcoin"] == Decimal("100")


def test_mock_provider_drifts_with_step() -> None:
    base = MockPriceProvider({"bitcoin": Decimal("100")}, drift_step=0)
    drifted = MockPriceProvider({"bitcoin": Decimal("100")}, drift_step=10)
    assert base.get_prices(["bitcoin"])["bitcoin"] == Decimal("100")
    # 100 * (1 + 10/100) == 110
    assert drifted.get_prices(["bitcoin"])["bitcoin"] == Decimal("110")


def test_mock_provider_unknown_symbol() -> None:
    provider = MockPriceProvider({"bitcoin": Decimal("100")})
    with pytest.raises(UnknownSymbol):
        provider.get_prices(["dogecoin"])


def test_cache_miss_writes_a_tick(repo: Repository) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    provider = MockPriceProvider({"bitcoin": Decimal("100")})
    cache = PriceCache(repo, provider, clock=lambda: now, ttl_seconds=60)

    price = cache.get_cached_price("bitcoin")
    assert price == Decimal("100")

    tick = repo.get_price("bitcoin")
    assert tick is not None
    assert tick.price == Decimal("100")
    assert tick.as_of == now
    assert tick.expires_at == now + timedelta(seconds=60)


def test_cache_hit_serves_cached_within_ttl(repo: Repository) -> None:
    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    provider = MockPriceProvider({"bitcoin": Decimal("100")})
    cache = PriceCache(repo, provider, clock=lambda: clock["now"], ttl_seconds=60)

    assert cache.get_cached_price("bitcoin") == Decimal("100")

    # Advance the clock WITHIN the ttl and change what the provider would return.
    clock["now"] += timedelta(seconds=30)
    provider.prices["bitcoin"] = Decimal("999")

    # Still cached: the OLD value, provider untouched.
    assert cache.get_cached_price("bitcoin") == Decimal("100")


def test_cache_refetches_after_expiry(repo: Repository) -> None:
    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    provider = MockPriceProvider({"bitcoin": Decimal("100")})
    cache = PriceCache(repo, provider, clock=lambda: clock["now"], ttl_seconds=60)

    assert cache.get_cached_price("bitcoin") == Decimal("100")

    # Advance PAST the ttl and change the provider's price.
    clock["now"] += timedelta(seconds=61)
    provider.prices["bitcoin"] = Decimal("250")

    assert cache.get_cached_price("bitcoin") == Decimal("250")
    tick = repo.get_price("bitcoin")
    assert tick is not None
    assert tick.price == Decimal("250")


def test_cache_get_cached_prices_loops(repo: Repository) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    provider = MockPriceProvider({"bitcoin": Decimal("100"), "ethereum": Decimal("20")})
    cache = PriceCache(repo, provider, clock=lambda: now)

    prices = cache.get_cached_prices(["bitcoin", "ethereum"])
    assert prices == {"bitcoin": Decimal("100"), "ethereum": Decimal("20")}


def test_http_provider_via_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/simple/price")
        assert request.url.params["ids"] == "bitcoin,ethereum"
        assert request.url.params["vs_currencies"] == "usd"
        return httpx.Response(
            200,
            json={"bitcoin": {"usd": 65000.5}, "ethereum": {"usd": 3200}},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        provider = HttpPriceProvider(client)
        prices = provider.get_prices(["bitcoin", "ethereum"])

    # str()->Decimal, no float rounding error.
    assert prices == {"bitcoin": Decimal("65000.5"), "ethereum": Decimal("3200")}


def test_http_provider_unknown_symbol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"bitcoin": {"usd": 65000}})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        provider = HttpPriceProvider(client)
        with pytest.raises(UnknownSymbol):
            provider.get_prices(["bitcoin", "dogecoin"])
