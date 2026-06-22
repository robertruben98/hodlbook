# hodlbook

[![Docs](https://img.shields.io/badge/docs-online-blue)](https://robertruben98.github.io/hodlbook/)

**A crypto paper-trading portfolio ledger API — built on [pydynantic](https://pypi.org/project/pydynantic/) single-table DynamoDB.**

`hodlbook` lets users run a virtual crypto portfolio: hold a cash balance, place
buy/sell orders at live (or mocked) prices, and track holdings, valuation, and a
full trade history — with no real money. It exists to be a real, working
application of **pydynantic**: every access pattern is a single-table design,
trades are atomic DynamoDB transactions, balances use optimistic locking, prices
are TTL-cached, and history is cursor-paginated.

> Status: **alpha / under construction.** See [ROADMAP.md](ROADMAP.md) for the
> plan and progress to a 1.0 release.

## Why it exists

This is a deliberate dogfooding project for `pydynantic`. It exercises the whole
library surface against a realistic domain:

| Domain need | pydynantic feature |
|---|---|
| One table, many entities | single-table modelling + `__entity__` discrimination |
| "trades for a portfolio", "trades by symbol" | primary key + GSIs |
| Atomic buy/sell (cash ↓, holding ↑, trade recorded) | `transaction(...)` |
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

## Usage

hodlbook runs against any DynamoDB endpoint via an injected boto3 client
(DynamoDB Local / AWS in prod, `moto` in tests). The core flow:

```python
import boto3
from decimal import Decimal
from hodlbook import (
    build_table, create_table, Repository, TradingEngine,
    MockPriceProvider, PriceCache, Valuator, AlertEvaluator, Side, Direction,
)

client = boto3.client("dynamodb")          # injected — your creds/region/endpoint
create_table(client)                       # one-time (or use CDK/Terraform in prod)

repo   = Repository(build_table(client))
prices = PriceCache(repo, MockPriceProvider({"bitcoin": Decimal("50000")}))
engine = TradingEngine(repo)
valuator = Valuator(repo, prices)

repo.create_portfolio(user_id="u1", portfolio_id="main", cash=Decimal("100000"))

# Atomic buy: cash debited, holding upserted, trade recorded — all-or-nothing.
engine.buy("u1", "main", "bitcoin", Decimal("1"), Decimal("50000"))

result = engine.sell("u1", "main", "bitcoin", Decimal("0.5"), Decimal("60000"))
print(result.realized_pnl)                 # Decimal('5000')

snapshot = valuator.value("u1", "main")    # cash + holdings, marked to current prices
print(snapshot.total_value, snapshot.total_unrealized_pnl)

# Price alerts (fired by the evaluator against fresh prices).
repo.create_alert("main", "a1", "bitcoin", Direction.ABOVE, Decimal("65000"))
fired = AlertEvaluator(repo, prices).evaluate(["bitcoin"])
```

### HTTP API (FastAPI)

```python
import boto3
from hodlbook import create_app

app = create_app(boto3.client("dynamodb"))   # inject the client; ASGI app
# uvicorn:  uvicorn mymodule:app
# Endpoints: POST /portfolios, POST /portfolios/{user}/{pf}/orders,
#            GET .../holdings, .../valuation, .../trades?cursor=, alerts CRUD,
#            GET /prices/{symbol}
```

A complete, runnable end-to-end example (no AWS needed — uses `moto`) lives in
[`examples/demo.py`](examples/demo.py).

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy
```

## License

MIT © Robert Ruben
