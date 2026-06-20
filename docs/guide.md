# Guide

This walks through the core hodlbook flow using the real public API: wire the
storage stack, open a portfolio, trade, value the book, page through history,
and arm price alerts. Every name below is exported from `hodlbook` (see the
[API Reference](api-reference.md)).

All money is [`decimal.Decimal`](https://docs.python.org/3/library/decimal.html)
— never `float`.

## 1. Wire the stack

The boto3 client is injected everywhere. In production point it at AWS or
DynamoDB Local; in tests wrap it in `moto`.

```python
import boto3
from decimal import Decimal
from hodlbook import (
    build_table, create_table, Repository, TradingEngine,
    MockPriceProvider, PriceCache, Valuator, AlertEvaluator, Direction,
)

client = boto3.client("dynamodb")   # injected
create_table(client)                # one-time provisioning

repo = Repository(build_table(client))
prices = PriceCache(repo, MockPriceProvider({"bitcoin": Decimal("50000")}))
engine = TradingEngine(repo)
valuator = Valuator(repo, prices)
alerts = AlertEvaluator(repo, prices)
```

[`Repository`](api-reference.md) is the typed storage layer;
[`TradingEngine`](api-reference.md), [`Valuator`](api-reference.md), and
[`AlertEvaluator`](api-reference.md) are the business-logic services built on
top of it.

## 2. Open a portfolio

```python
repo.create_portfolio("u1", "main", cash=Decimal("100000"))
```

`create_portfolio` fails if a portfolio already exists at that key.

## 3. Place orders

`buy` and `sell` are atomic: cash, the per-symbol holding, and an immutable
trade record all move together, guarded by optimistic locking.

```python
# Buy 1 BTC at 50,000 — cash debited, holding upserted, trade recorded.
result = engine.buy("u1", "main", "bitcoin", Decimal("1"), Decimal("50000"))

# Sell half at a higher price — realized P&L is computed on the way out.
result = engine.sell("u1", "main", "bitcoin", Decimal("0.5"), Decimal("60000"))
print(result.realized_pnl)   # Decimal('5000')
```

Both return a [`TradeResult`](api-reference.md) with the persisted `trade` and
`realized_pnl` (always `Decimal("0")` for buys). Invalid orders raise typed
errors — [`InvalidOrder`](api-reference.md) for non-positive quantity/price,
[`InsufficientFunds`](api-reference.md) on a buy you can't afford,
[`InsufficientHoldings`](api-reference.md) on a sell larger than the position,
and [`TradeConflict`](api-reference.md) if optimistic-lock retries are exhausted.

## 4. Value the portfolio

[`Valuator.value`](api-reference.md) marks every holding to its latest cached
price and rolls cash + holdings into a [`Valuation`](api-reference.md) snapshot.

```python
snapshot = valuator.value("u1", "main")
print(snapshot.cash)                   # uninvested cash
print(snapshot.holdings_value)         # market value of all positions
print(snapshot.total_value)            # cash + holdings_value
print(snapshot.total_unrealized_pnl)   # open P&L across holdings

for h in snapshot.holdings:            # each is a HoldingValuation
    print(h.symbol, h.quantity, h.price, h.market_value, h.unrealized_pnl)
```

## 5. Page through trade history

Trades are cursor-paginated. [`Repository.list_trades`](api-reference.md)
returns a page; pass the previous page's cursor to fetch the next.

```python
page = repo.list_trades("main", limit=50)
for trade in page.items:
    print(trade)

if page.cursor:                        # more pages remain
    nxt = repo.list_trades("main", cursor=page.cursor, limit=50)
```

## 6. Arm and fire price alerts

Create a threshold alert, then have the evaluator fire any that the current
prices have crossed.

```python
repo.create_alert("main", "a1", "bitcoin", Direction.ABOVE, Decimal("65000"))

fired = alerts.evaluate(["bitcoin"])   # list[FiredAlert]
for f in fired:
    print(f.alert, f.price)            # the alert and the price that crossed it
```

Firing is idempotent: a second pass over the same prices fires nothing.
[`Direction.ABOVE`](api-reference.md) fires when `price >= threshold`,
[`Direction.BELOW`](api-reference.md) when `price <= threshold`.

## HTTP API

The same flow is served over HTTP by a FastAPI app.
[`create_app`](api-reference.md) wires the whole stack from an injected client
and returns an ASGI app:

```python
import boto3
from hodlbook import create_app

app = create_app(boto3.client("dynamodb"))   # ASGI app
# uvicorn mymodule:app
```

Endpoints cover the flow above: create portfolios, place orders, list holdings,
portfolio valuation, cursor-paginated trade history, alert CRUD, and current
prices. Domain and pydynantic exceptions are translated to a uniform
`{"error", "detail"}` JSON body by centralized handlers.

## Observability

Wire pydynantic's `on_operation` hook to opt into tracing without forcing a
logging dependency. [`logging_hook`](api-reference.md) logs each DynamoDB
operation; [`collecting_hook`](api-reference.md) collects events for inspection
(handy in tests). Pass one to `build_table` / `create_app` as the table's
operation hook.
