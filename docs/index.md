# hodlbook

**A crypto paper-trading portfolio ledger API — built on
[pydynantic](https://pypi.org/project/pydynantic/) single-table DynamoDB.**

`hodlbook` lets users run a virtual crypto portfolio: hold a cash balance, place
buy/sell orders at live (or mocked) prices, and track holdings, valuation, and a
full trade history — with no real money. It exists to be a real, working
application of **pydynantic**: every access pattern is a single-table design,
trades are atomic DynamoDB transactions, balances use optimistic locking, prices
are TTL-cached, and history is cursor-paginated.

## Why it exists

This is a deliberate dogfooding project for `pydynantic`. It exercises the whole
library surface against a realistic domain:

| Domain need | pydynantic feature |
|---|---|
| One table, many entities | single-table modelling + `__entity__` discrimination |
| "trades for a portfolio", "trades by symbol" | primary key + GSIs |
| Atomic buy/sell (cash down, holding up, trade recorded) | `transaction(...)` |
| No double-spend on balance | optimistic locking (`version_attr`) |
| Price cache that expires | `ttl_attr` |
| Created/updated bookkeeping | `created_at_attr` / `updated_at_attr` |
| Trade history pages | cursor pagination (`.page(cursor=...)`) |
| Cost/latency tracing | observability hooks (`on_operation`) |

If the app surfaces a gap in `pydynantic`, the fix lands upstream in the library.

## Stack

- **Python 3.10+**, **FastAPI** for the HTTP API.
- **pydynantic** for the data layer over **DynamoDB** (the boto3 client is
  injected — DynamoDB Local or AWS in prod, `moto` in tests).
- **pytest + moto** for tests; **ruff** + **mypy --strict** for quality.

## Quick start

```python
import boto3
from decimal import Decimal
from hodlbook import (
    build_table, create_table, Repository, TradingEngine,
    MockPriceProvider, PriceCache, Valuator, AlertEvaluator, Side, Direction,
)

client = boto3.client("dynamodb")          # injected — your creds/region/endpoint
create_table(client)                       # one-time (or use CDK/Terraform in prod)

repo = Repository(build_table(client))
prices = PriceCache(repo, MockPriceProvider({"bitcoin": Decimal("50000")}))
engine = TradingEngine(repo)
valuator = Valuator(repo, prices)

repo.create_portfolio("u1", "main", cash=Decimal("100000"))

# Atomic buy: cash debited, holding upserted, trade recorded — all-or-nothing.
engine.buy("u1", "main", "bitcoin", Decimal("1"), Decimal("50000"))

result = engine.sell("u1", "main", "bitcoin", Decimal("0.5"), Decimal("60000"))
print(result.realized_pnl)                 # Decimal('5000')

snapshot = valuator.value("u1", "main")    # cash + holdings, marked to current prices
print(snapshot.total_value, snapshot.total_unrealized_pnl)
```

A complete, runnable end-to-end example (no AWS needed — uses `moto`) lives in
`examples/demo.py` in the repository.

## Where to go next

- **[Concepts](concepts.md)** — the single-table design and the domain model.
- **[Guide](guide.md)** — the core flow: portfolios, orders, valuation, alerts.
- **[API Reference](api-reference.md)** — every public symbol, generated from the
  source.
- **[Deployment](deployment.md)** — running hodlbook against real DynamoDB.

## License

MIT © Robert Ruben
