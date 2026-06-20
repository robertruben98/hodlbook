# hodlbook

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

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy
```

## License

MIT © Robert Ruben
