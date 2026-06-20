"""Price feed: pluggable providers plus a TTL-aware cache over :class:`PriceTick`.

A :class:`PriceProvider` is the source of truth for a symbol's current price.
Two implementations ship here: :class:`MockPriceProvider` (deterministic, for
tests and offline use) and :class:`HttpPriceProvider` (thin CoinGecko client over
an injected ``httpx.Client``). :class:`PriceCache` sits in front of any provider,
persisting fetched prices as ``PriceTick`` rows and serving them until they
expire -- using an injected clock so freshness is testable and never depends on
DynamoDB's eventual TTL sweep.

All prices are :class:`~decimal.Decimal`; floats never touch money math.
"""

from __future__ import annotations

import typing
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .errors import UnknownSymbol
from .repository import Repository

#: CoinGecko's public simple-price endpoint base.
DEFAULT_BASE_URL = "https://api.coingecko.com/api/v3"


class PriceProvider(typing.Protocol):
    """A source of current USD prices keyed by symbol.

    Single method by design: a Protocol with only one required member avoids the
    default-method friction mypy raises when a Protocol mixes concrete and
    abstract members.
    """

    def get_prices(self, symbols: list[str]) -> dict[str, Decimal]: ...


class MockPriceProvider:
    """A deterministic provider for tests and offline runs.

    Prices drift purely as a function of ``(symbol, drift_step)`` -- no
    ``random``, no wall clock -- so a given step always yields the same price.
    """

    def __init__(self, prices: dict[str, Decimal], drift_step: int = 0) -> None:
        self.prices = prices
        self.drift_step = drift_step

    def get_prices(self, symbols: list[str]) -> dict[str, Decimal]:
        factor = Decimal(1) + Decimal(self.drift_step) / Decimal(100)
        out: dict[str, Decimal] = {}
        for symbol in symbols:
            if symbol not in self.prices:
                raise UnknownSymbol(symbol)
            out[symbol] = self.prices[symbol] * factor
        return out


class HttpPriceProvider:
    """Thin CoinGecko client. Network access only through the injected client."""

    def __init__(self, client: typing.Any, base_url: str = DEFAULT_BASE_URL) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")

    def get_prices(self, symbols: list[str]) -> dict[str, Decimal]:
        ids = ",".join(symbols)
        resp = self.client.get(
            f"{self.base_url}/simple/price",
            params={"ids": ids, "vs_currencies": "usd"},
        )
        resp.raise_for_status()
        data = resp.json()
        out: dict[str, Decimal] = {}
        for symbol in symbols:
            if symbol not in data or "usd" not in data[symbol]:
                raise UnknownSymbol(symbol)
            # str() first: float -> Decimal would smuggle binary rounding error in.
            out[symbol] = Decimal(str(data[symbol]["usd"]))
        return out


class PriceCache:
    """A TTL cache over :class:`PriceTick`, fronting any :class:`PriceProvider`."""

    def __init__(
        self,
        repo: Repository,
        provider: PriceProvider,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        ttl_seconds: int = 60,
    ) -> None:
        self.repo = repo
        self.provider = provider
        self._clock = clock
        self.ttl_seconds = ttl_seconds

    def get_cached_price(self, symbol: str) -> Decimal:
        now = self._clock()
        tick = self.repo.get_price(symbol)
        # Check expires_at against the injected clock ourselves: DynamoDB's TTL
        # deletion is eventual (can lag by ~48h), so a row may physically exist
        # well past its expiry -- never treat its mere presence as "fresh".
        if tick is not None and tick.expires_at is not None and tick.expires_at > now:
            price: Decimal = tick.price
            return price

        price = self.provider.get_prices([symbol])[symbol]
        self.repo.put_price(
            symbol,
            price,
            as_of=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        return price

    def get_cached_prices(self, symbols: list[str]) -> dict[str, Decimal]:
        return {symbol: self.get_cached_price(symbol) for symbol in symbols}
