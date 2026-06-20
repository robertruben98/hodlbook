# Roadmap to hodlbook 1.0

`hodlbook` is a crypto paper-trading portfolio ledger API whose entire data layer
is built on **[pydynantic](https://github.com/robertruben98/pydynantic)**
(single-table DynamoDB on Pydantic v2). The point of the project is to be a
**real, fully functional application of pydynantic** — so every milestone leans
on a concrete library feature, and any gap the app exposes is fixed **upstream in
pydynantic** rather than worked around.

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
  do, open a PR on pydynantic; don't fork the behavior into the app.

## Domain model (single table)

One table `hodlbook` (PK/SK + `GSI1`, `GSI2`). Entities:

- **Portfolio** — `pk=USER#{user_id}`, `sk=PORTFOLIO#{portfolio_id}`; holds
  `cash` balance + a `version` for optimistic locking.
- **Holding** — a position in one asset: `pk=PORTFOLIO#{portfolio_id}`,
  `sk=HOLDING#{symbol}`; `quantity`, `avg_cost`.
- **Trade** — an executed buy/sell: `pk=PORTFOLIO#{portfolio_id}`,
  `sk=TRADE#{ts}#{trade_id}`; GSI1 `by_symbol` (`SYMBOL#{symbol}` / `TRADE#{ts}`).
- **PriceTick** — TTL-cached spot price: `pk=PRICE#{symbol}`, `sk=TICK`; `ttl_attr`.
- **Alert** — a price threshold: `pk=PORTFOLIO#{portfolio_id}`,
  `sk=ALERT#{alert_id}`; GSI2 `by_symbol` for the evaluator.

---

## M1 — Storage layer (pydynantic models)

The single-table schema + a typed repository, fully tested on `moto`.

- [ ] `Table` config + the five entities with key templates and GSIs.
- [ ] A `storage` module (table/client construction, dependency-injectable).
- [ ] Repository helpers: create/get portfolio, upsert holding, record trade,
      list trades (paginated), put/get price tick, CRUD alerts.
- [ ] `moto` test fixtures (mocked table) + unit tests for every helper.

## M2 — Trading engine

The core: atomic buy/sell with correct accounting.

- [ ] `buy(portfolio, symbol, qty, price)` / `sell(...)` as a single pydynantic
      `transaction`: debit/credit `cash`, upsert `Holding` (qty + weighted
      `avg_cost`), append a `Trade` — all-or-nothing.
- [ ] Optimistic locking on `Portfolio.cash`/`version`; lost races retry or fail
      cleanly with a domain error.
- [ ] Validation: insufficient cash (buy), insufficient quantity (sell),
      non-positive qty/price → typed domain errors.
- [ ] Realized P&L on sells; holding removed when quantity hits zero.
- [ ] Tests for happy paths, every validation error, and a concurrent-write race.

## M3 — Price feed & valuation

- [ ] A `PriceProvider` protocol with a deterministic `MockPriceProvider`
      (seeded) and an optional HTTP provider (e.g. CoinGecko) behind the same
      interface — injectable, so tests never hit the network.
- [ ] `PriceTick` write-through TTL cache: fetch → cache with `ttl_attr` → serve
      cached within TTL.
- [ ] Portfolio valuation: mark holdings to latest prices → total value,
      per-asset value, unrealized P&L.
- [ ] Tests with the mock provider (no network).

## M4 — REST API (FastAPI)

- [ ] App factory with the DynamoDB client injected via FastAPI dependencies.
- [ ] Endpoints: create/get portfolio; place order (buy/sell); list holdings;
      portfolio valuation; trade history (cursor-paginated); CRUD watchlist
      alerts; current prices.
- [ ] Map pydynantic errors → HTTP (e.g. `OptimisticLockError`→409,
      `ItemNotFoundError`→404, validation→422) via exception handlers.
- [ ] `httpx` API tests against the app wired to a `moto` table.

## M5 — Alerts & watchlists

- [ ] Create price-threshold alerts (`above`/`below`).
- [ ] GSI2 `by_symbol` evaluator: given fresh prices, find + fire matching alerts
      (mark triggered, idempotent).
- [ ] Tests for arming, firing, and not-double-firing alerts.

## M6 — Quality, DX & demo

- [ ] CI (GitHub Actions): ruff + mypy --strict + pytest/moto matrix (3.10–3.13),
      coverage gate (≥90%).
- [ ] A runnable demo script: seed a portfolio, simulate a few days of trading
      against the mock feed, print the valuation + history.
- [ ] `pre-commit` + Dependabot; usage docs in the README.
- [ ] Observability: wire pydynantic's `on_operation` hook to optional logging.

## M7 — Release 1.0.0

- [ ] Pin `pydynantic>=1.0`, finalize packaging.
- [ ] CHANGELOG + `Development Status :: 5 - Production/Stable`.
- [ ] Tag `1.0.0` and publish a GitHub Release.

---

## Definition of done for 1.0

1. `pip install -e ".[dev]"`, then `pytest` / `ruff` / `mypy --strict` all green
   on `moto`; coverage ≥ 90%.
2. The FastAPI app runs and serves the full flow: create portfolio → buy → sell →
   value → paginate history → set + fire an alert.
3. Every persistence path goes through pydynantic single-table (no raw boto3 in
   business logic); trades are atomic and balance-safe under concurrency.
4. The demo script runs end-to-end against `moto` with no AWS credentials.
5. Any pydynantic gap discovered was fixed upstream and the dependency pinned.

## Non-goals for 1.0

- Real funds, real exchange connectivity, or order-book matching (it's
  paper-trading at a quoted spot price).
- Auth/multi-tenant hardening beyond a `user_id` key (no real authn/z).
- A frontend UI (API + demo script only).
