"""End-to-end hodlbook demo: a small crypto paper-trading story, fully offline.

Runs entirely on ``moto`` (no AWS credentials, no network). Tells a story:
create a portfolio, buy BTC/ETH across "days" of price drift, value the book,
take a profit, review trade history, then arm and fire a price alert.

All money math is :class:`~decimal.Decimal` -- never ``float``.

Run it::

    pip install -e ".[dev]"
    python examples/demo.py
"""

from __future__ import annotations

from decimal import Decimal

import boto3
from moto import mock_aws

from hodlbook import (
    AlertEvaluator,
    Direction,
    MockPriceProvider,
    PriceCache,
    Repository,
    Side,
    TradingEngine,
    Valuator,
    build_table,
    create_table,
)

USER_ID = "alice"
PORTFOLIO_ID = "main"

BTC = "bitcoin"
ETH = "ethereum"
SOL = "solana"


def usd(amount: Decimal) -> str:
    """Format a Decimal as a USD string with thousands separators."""
    return f"${amount:,.2f}"


def rule(title: str) -> None:
    print(f"\n{'=' * 60}\n {title}\n{'=' * 60}")


def main() -> None:
    with mock_aws():
        # --- Wiring: everything runs against an in-memory moto DynamoDB. ---
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_table(client)
        table = build_table(client)
        repo = Repository(table)

        provider = MockPriceProvider(
            {
                BTC: Decimal("50000"),
                ETH: Decimal("3000"),
                SOL: Decimal("150"),
            }
        )
        # ttl_seconds=0 so the cache always re-reads the provider: as we advance
        # the provider's drift_step to simulate price movement, valuations and
        # alerts immediately see the new prices.
        cache = PriceCache(repo, provider, ttl_seconds=0)
        engine = TradingEngine(repo)
        valuator = Valuator(repo, cache)
        alerts = AlertEvaluator(repo, cache)

        # --- 1. Open a portfolio with starting cash. ---
        rule("1. Open portfolio")
        starting_cash = Decimal("100000")
        repo.create_portfolio(USER_ID, PORTFOLIO_ID, cash=starting_cash)
        print(f"Opened portfolio {USER_ID}/{PORTFOLIO_ID} with {usd(starting_cash)} cash.")

        # --- 2. Buy across "days": advance drift_step between buys. ---
        rule("2. Buy BTC and ETH over a few days")

        # Day 0: prices at base.
        provider.drift_step = 0
        day0 = cache.get_cached_prices([BTC, ETH])
        r = engine.buy(USER_ID, PORTFOLIO_ID, BTC, Decimal("1"), day0[BTC])
        print(f"Day 0: bought 1 {BTC} @ {usd(r.trade.price)}")
        r = engine.buy(USER_ID, PORTFOLIO_ID, ETH, Decimal("3"), day0[ETH])
        print(f"Day 0: bought 3 {ETH} @ {usd(r.trade.price)}")

        # Day 3: prices have drifted up ~3%; buy more BTC to show weighted avg cost.
        provider.drift_step = 3
        day3 = cache.get_cached_prices([BTC])
        r = engine.buy(USER_ID, PORTFOLIO_ID, BTC, Decimal("0.5"), day3[BTC])
        print(f"Day 3: bought 0.5 {BTC} @ {usd(r.trade.price)} (price drifted up)")

        # --- 3. Holdings + mark-to-market valuation. ---
        rule("3. Holdings & valuation (marked to market)")
        valuation = valuator.value(USER_ID, PORTFOLIO_ID)
        for h in valuation.holdings:
            print(
                f"  {h.symbol:<9} qty={h.quantity:>4} "
                f"avg_cost={usd(h.avg_cost):>12} "
                f"price={usd(h.price):>12} "
                f"mkt_val={usd(h.market_value):>14} "
                f"unrealized={usd(h.unrealized_pnl):>12}"
            )
        print(f"\n  Cash:              {usd(valuation.cash)}")
        print(f"  Holdings value:    {usd(valuation.holdings_value)}")
        print(f"  Total value:       {usd(valuation.total_value)}")
        print(f"  Unrealized P&L:    {usd(valuation.total_unrealized_pnl)}")
        # The weighted-average BTC cost basis sits between the day-0 and day-3 buys.
        btc = next(h for h in valuation.holdings if h.symbol == BTC)
        print(f"\n  BTC weighted avg cost basis: {usd(btc.avg_cost)} across 2 buys")

        # --- 4. Sell part of a position and realize a profit. ---
        rule("4. Take profit: sell part of BTC")
        provider.drift_step = 10  # ~10% above base -> a clear gain.
        sell_price = cache.get_cached_prices([BTC])[BTC]
        result = engine.sell(USER_ID, PORTFOLIO_ID, BTC, Decimal("1"), sell_price)
        print(f"Sold 1 {BTC} @ {usd(result.trade.price)}")
        print(f"Realized P&L on the sale: {usd(result.realized_pnl)}")

        # --- 5. Trade history (most-recent-first, via repository pagination). ---
        rule("5. Trade history")
        page = repo.list_trades(PORTFOLIO_ID)
        for t in page.items:
            sign = "+" if t.side is Side.SELL else "-"
            print(
                f"  {t.ts}  {t.side.value:<4} "
                f"{t.quantity:>4} {t.symbol:<9} @ {usd(t.price):>12}  "
                f"({sign}{usd(t.quantity * t.price)})"
            )
        print(f"\n  {len(page.items)} trades total.")

        # --- 6. Arm a price alert, push the price across it, evaluate. ---
        rule("6. Price alert")
        threshold = Decimal("60000")
        repo.create_alert(
            PORTFOLIO_ID,
            alert_id="btc-moon",
            symbol=BTC,
            direction=Direction.ABOVE,
            threshold=threshold,
        )
        print(f"Armed alert: {BTC} ABOVE {usd(threshold)}")

        provider.drift_step = 25  # 50000 * 1.25 = 62500 -> crosses 60000.
        crossed_price = cache.get_cached_prices([BTC])[BTC]
        print(f"{BTC} price moves to {usd(crossed_price)} ...")
        fired = alerts.evaluate([BTC])
        for f in fired:
            print(
                f"  FIRED: alert {f.alert.alert_id} "
                f"({f.alert.symbol} {f.alert.direction.value} {usd(f.alert.threshold)}) "
                f"at price {usd(f.price)}"
            )
        if not fired:
            print("  No alerts fired.")

        rule("Demo complete")
        final = valuator.value(USER_ID, PORTFOLIO_ID)
        print(f"Final cash: {usd(final.cash)}  |  total value: {usd(final.total_value)}")
        print("SUCCESS: hodlbook ran end-to-end on moto with no AWS credentials.")


if __name__ == "__main__":
    main()
