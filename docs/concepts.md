# Concepts

hodlbook is a deliberate, end-to-end application of **single-table design** on
DynamoDB, expressed entirely through [pydynantic](https://pypi.org/project/pydynantic/).
This page explains the data model and the principles behind it.

## Guiding principles

- **Single-table first** — one DynamoDB table, many entities, access patterns
  designed up front and expressed as pydynantic keys/GSIs.
- **Money is never wrong** — balances and holdings change only inside atomic
  transactions; concurrent writes are guarded by optimistic locking. No
  double-spend, no partial trades.
- **Inject the client** — the boto3 client is injected everywhere (DynamoDB
  Local / AWS in prod, `moto` in tests). No hidden global state.
- **Typed end to end** — `mypy --strict` clean, `ruff` clean, tests on `moto`.
- **Fix the library upstream** — if hodlbook needs something pydynantic can't
  do, the fix lands in pydynantic; the behavior is never forked into the app.

## Single-table design

A single DynamoDB table (`hodlbook`) holds every entity, distinguished by an
`__entity__` discriminator. The table has a primary key (`PK`/`SK`) plus two
global secondary indexes, `GSI1` and `GSI2`. Each entity declares its own key
templates and which GSIs it projects onto, so the access patterns are designed
up front rather than discovered at query time.

The table name is exported as
[`TABLE_NAME`](api-reference.md). The table and its entity classes are built
from an injected client by [`build_table`](api-reference.md) /
[`build_models`](api-reference.md); [`create_table`](api-reference.md)
provisions it once.

## Domain model

One table `hodlbook` (PK/SK + `GSI1`, `GSI2`). Entities:

| Entity | PK | SK | Notes |
|---|---|---|---|
| **Portfolio** | `USER#{user_id}` | `PORTFOLIO#{portfolio_id}` | holds `cash` balance + a `version` for optimistic locking |
| **Holding** | `PORTFOLIO#{portfolio_id}` | `HOLDING#{symbol}` | a position in one asset: `quantity`, `avg_cost` |
| **Trade** | `PORTFOLIO#{portfolio_id}` | `TRADE#{ts}#{trade_id}` | an executed buy/sell; GSI1 `by_symbol` (`SYMBOL#{symbol}` / `TRADE#{ts}`) |
| **PriceTick** | `PRICE#{symbol}` | `TICK` | TTL-cached spot price; uses `ttl_attr` |
| **Alert** | `PORTFOLIO#{portfolio_id}` | `ALERT#{alert_id}` | a price threshold; GSI2 `by_symbol` for the evaluator |

These five entities are defined together inside `build_models`, so each injected
client gets its own isolated set of classes.

## Money is never wrong

Two pydynantic features keep balances correct under concurrency:

- **Atomic transactions** — a buy or sell mutates the portfolio's cash, the
  per-symbol holding, and appends an immutable trade record in a single DynamoDB
  transaction. Either every change lands or none does.
- **Optimistic locking** — each `Portfolio` carries a `version`. A trade reads
  the version, guards the transaction with a condition on it, and bumps it. A
  lost race cancels the transaction; the [`TradingEngine`](api-reference.md)
  retries up to [`MAX_RETRIES`](api-reference.md) times before raising
  [`TradeConflict`](api-reference.md).

All monetary math uses [`decimal.Decimal`](https://docs.python.org/3/library/decimal.html)
— `float` never touches money.

## Prices and TTL caching

A [`PriceProvider`](api-reference.md) is the source of truth for a symbol's
current price. Two implementations ship:
[`MockPriceProvider`](api-reference.md) (deterministic, for tests and offline
use) and [`HttpPriceProvider`](api-reference.md) (a thin CoinGecko client over
an injected `httpx.Client`).

[`PriceCache`](api-reference.md) sits in front of any provider, persisting
fetched prices as `PriceTick` rows (using pydynantic's `ttl_attr`) and serving
them until they expire — with an injected clock so freshness is testable.

## Errors

The domain error hierarchy is rooted at [`HodlbookError`](api-reference.md) and
is independent of pydynantic's exceptions. The engine catches pydynantic's
transaction/condition failures internally (to drive optimistic-lock retries) and
translates them into domain errors —
[`InsufficientFunds`](api-reference.md),
[`InsufficientHoldings`](api-reference.md),
[`InvalidOrder`](api-reference.md),
[`TradeConflict`](api-reference.md),
[`UnknownSymbol`](api-reference.md) — so storage-layer concerns never leak out.
