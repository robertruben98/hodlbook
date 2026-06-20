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

- [x] `Table` config + the five entities with key templates and GSIs.
- [x] A `storage` module (table/client construction, dependency-injectable).
- [x] Repository helpers: create/get portfolio, upsert holding, record trade,
      list trades (paginated), put/get price tick, CRUD alerts.
- [x] `moto` test fixtures (mocked table) + unit tests for every helper.

## M2 — Trading engine

The core: atomic buy/sell with correct accounting.

- [x] `buy(portfolio, symbol, qty, price)` / `sell(...)` as a single pydynantic
      `transaction`: debit/credit `cash`, upsert `Holding` (qty + weighted
      `avg_cost`), append a `Trade` — all-or-nothing.
- [x] Optimistic locking on `Portfolio.cash`/`version`; lost races retry or fail
      cleanly with a domain error.
- [x] Validation: insufficient cash (buy), insufficient quantity (sell),
      non-positive qty/price → typed domain errors.
- [x] Realized P&L on sells; holding removed when quantity hits zero.
- [x] Tests for happy paths, every validation error, and a concurrent-write race.

## M3 — Price feed & valuation

- [x] A `PriceProvider` protocol with a deterministic `MockPriceProvider`
      (seeded) and an optional HTTP provider (e.g. CoinGecko) behind the same
      interface — injectable, so tests never hit the network.
- [x] `PriceTick` write-through TTL cache: fetch → cache with `ttl_attr` → serve
      cached within TTL.
- [x] Portfolio valuation: mark holdings to latest prices → total value,
      per-asset value, unrealized P&L.
- [x] Tests with the mock provider (no network).

## M4 — REST API (FastAPI)

- [x] App factory with the DynamoDB client injected via FastAPI dependencies.
- [x] Endpoints: create/get portfolio; place order (buy/sell); list holdings;
      portfolio valuation; trade history (cursor-paginated); CRUD watchlist
      alerts; current prices.
- [x] Map pydynantic errors → HTTP (e.g. `OptimisticLockError`→409,
      `ItemNotFoundError`→404, validation→422) via exception handlers.
- [x] `httpx` API tests against the app wired to a `moto` table.

## M5 — Alerts & watchlists

- [x] Create price-threshold alerts (`above`/`below`).
- [x] GSI2 `by_symbol` evaluator: given fresh prices, find + fire matching alerts
      (mark triggered, idempotent).
- [x] Tests for arming, firing, and not-double-firing alerts.

## M6 — Quality, DX & demo

- [x] CI (GitHub Actions): ruff + mypy --strict + pytest/moto matrix (3.10–3.13),
      coverage gate (≥90%).
- [x] A runnable demo script: seed a portfolio, simulate a few days of trading
      against the mock feed, print the valuation + history.
- [x] `pre-commit` + Dependabot; usage docs in the README.
- [x] Observability: wire pydynantic's `on_operation` hook to optional logging.

## M7 — Release 1.0.0

- [x] Pin `pydynantic>=1.0`, finalize packaging.
- [x] CHANGELOG + `Development Status :: 5 - Production/Stable`.
- [x] Tag `1.0.0` and publish a GitHub Release.

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

---

# Part II — Road to 2.0.0

**1.0 shipped** (M1–M7: storage, trading engine, price feed, REST API, alerts,
quality/DX, release). 2.0 is about turning the working prototype into a
**production-grade, professional service**: real authentication, configuration,
live market data, richer order types, analytics, first-class observability,
containerized deployment, a docs site, and hardened CI/security — without ever
breaking the "money is never wrong" and "single-table on pydynantic" principles.

Each milestone below is delivered as one or more GitHub **issues** picked up by
the developer team, verified by the tester, and merged to `main` only when green
(ruff + `mypy --strict` + pytest/moto + coverage gate).

## M8 — Configuration & settings

- [x] Centralized settings via `pydantic-settings` (env-driven): table name,
      AWS region/endpoint, price-provider choice, TTL, log level, default
      starting cash. No hardcoded config in modules.
- [x] `create_app()` reads settings; a documented `.env.example`.
- [x] Tests for settings parsing + overrides.

## M9 — Authentication & multi-tenancy

- [x] API-key (or bearer-token) auth as a FastAPI dependency; unauthenticated
      requests → 401.
- [x] Per-principal authorization: a caller may only access portfolios under
      their own `user_id` (mismatch → 403). Remove the trust in path `user_id`.
- [x] An `ApiKey` entity (hashed) in the single table, with issue/revoke helpers.
- [x] Tests: 401 (no/invalid key), 403 (cross-tenant), happy path.

## M10 — Advanced order types

- [x] Limit orders and recurring/DCA orders persisted as an `Order` entity;
      an execution pass that fills eligible orders against the latest price tick
      (atomic via pydynantic `transaction`, balance-safe).
- [x] Order lifecycle (open → filled/cancelled), list/cancel endpoints.
- [x] Tests: limit fill when price crosses, DCA schedule, cancel, insufficient
      funds at fill time.

## M11 — Analytics & leaderboards

- [x] Periodic portfolio-value snapshots (entity) → performance over time and a
      returns series endpoint.
- [x] A leaderboard ranking portfolios by total value / return via a GSI.
- [x] Tests for snapshotting, returns math, and ranking.

## M12 — Observability & ops endpoints

- [x] Structured JSON logging + request IDs (middleware); the pydynantic
      `on_operation` hook wired to metrics.
- [x] Prometheus `/metrics` (request latency/count, DynamoDB op latency/count)
      and `/healthz` / `/readyz` endpoints.
- [x] Tests asserting metrics increment and health endpoints respond.

## M13 — Packaging, deployment & CLI

- [x] `Dockerfile` (slim, non-root) + `docker-compose.yml` (app + DynamoDB Local)
      that boots the full stack locally.
- [x] A `hodlbook` CLI (console-script) for admin tasks: create-table, issue
      API key, seed a demo portfolio, run the price-refresh pass.
- [x] Deployment docs (env vars, DynamoDB table provisioning).

## M14 — Docs site & API/CI hardening

- [x] MkDocs (Material) docs site: concepts, API guide, mkdocstrings reference,
      deployment guide; `mkdocs build --strict` in CI.
- [x] API versioning (`/v1` prefix), richer OpenAPI examples, and basic rate
      limiting.
- [x] Security/quality in CI: `pip-audit` + `bandit`; raise the coverage gate to
      ≥95%.

## M15 — Release 2.0.0

- [x] CHANGELOG `[2.0.0]`, version bump, keep `Development Status :: 5`.
- [x] Migration notes (1.x → 2.0 breaking changes, e.g. auth now required).
- [x] Tag `2.0.0` and publish a GitHub Release.

## Definition of done for 2.0

1. All gates green on `moto`; coverage ≥ 95%; `pip-audit`/`bandit` clean in CI.
2. Auth enforced end-to-end (no unauthenticated/cross-tenant access); settings
   are env-driven with no hardcoded config.
3. `docker-compose up` boots the API against DynamoDB Local; the CLI works.
4. Limit/DCA orders execute atomically and balance-safe; analytics + leaderboard
   serve real data.
5. `/metrics`, `/healthz`, `/readyz` live; structured logs with request IDs.
6. Docs site builds `--strict`; API is versioned under `/v1`.
7. Every persistence path still goes through pydynantic single-table; any library
   gap discovered is fixed upstream in pydynantic and the pin updated.
