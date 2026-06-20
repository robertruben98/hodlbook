# Changelog

All notable changes to hodlbook are documented in this file. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-06-20

A production-grade release: authentication, configuration, advanced orders,
analytics, observability, containerized deployment, a docs site, and hardened
CI — all still on pydynantic single-table DynamoDB (`pydynantic>=1.0`).

### Added
- **Configuration** — env-driven `Settings` (`pydantic-settings`, `HODLBOOK_*`)
  for table, region/endpoint, price provider, TTL, log level, default cash, and
  rate limit; `create_app(settings=...)`.
- **Authentication & multi-tenancy** — API keys (SHA-256 hashed, raw token shown
  once) via an `ApiKey` entity; a Bearer/`X-API-Key` dependency (401 on
  missing/invalid/revoked) and per-tenant authorization (403 cross-tenant). An
  `issue-api-key` CLI command mints keys.
- **Advanced orders** — limit and recurring/DCA orders (`Order` entity) with an
  `OrderExecutor` filling eligible open orders against the latest price via the
  atomic trading engine; placement/list/get/cancel endpoints.
- **Analytics & leaderboards** — portfolio-value `Snapshot`s, a returns series,
  and a `/leaderboard` ranked via a GSI (lexically-sortable zero-padded rank key).
- **Observability** — structured JSON logging with request IDs, Prometheus
  `/metrics` (HTTP + DynamoDB-operation metrics via pydynantic's `on_operation`
  hook), and `/healthz` / `/readyz` ops endpoints.
- **Packaging & CLI** — a slim non-root `Dockerfile`, `docker-compose.yml`
  (API + DynamoDB Local), and a `hodlbook` CLI (`create-table`, `issue-api-key`,
  `seed-demo`, `refresh-prices`).
- **Docs & CI** — a MkDocs (Material) docs site, `pip-audit` + `bandit` in CI,
  a 95% coverage gate, and a strict docs build.

### Changed
- **Rate limiting** — `/v1` routes are rate-limited per principal (429 on
  exceedance, configurable via `HODLBOOK_RATE_LIMIT_PER_MINUTE`).

### Breaking changes (migration 1.x → 2.0)
- **Authentication is now required.** All business endpoints reject
  unauthenticated requests with 401. Issue a key (`hodlbook issue-api-key
  --user-id <id>`) and send `Authorization: Bearer <token>`; a caller may only
  access portfolios under their own `user_id` (403 otherwise).
- **API is versioned under `/v1`.** All business routes moved from `/...` to
  `/v1/...`; old unversioned paths now return 404. `/healthz`, `/readyz`, and
  `/metrics` remain unversioned at the root and unauthenticated.

## [1.0.0] - 2026-06-20

First stable release — a fully functional crypto paper-trading portfolio ledger
API, built end-to-end on [pydynantic](https://pypi.org/project/pydynantic/)
single-table DynamoDB (no raw boto3 in business logic; `pydynantic>=1.0`).

### Added
- **Storage layer** — single `hodlbook` table (PK/SK + GSI1 + GSI2) modelling
  Portfolio, Holding, Trade, PriceTick, and Alert via pydynantic entities, with a
  `build_models(table)` factory keeping the injected boto3 client isolated, and a
  typed `Repository` over every access pattern.
- **Trading engine** — atomic `buy`/`sell` as a single pydynantic `transaction`
  (debit/credit cash, weighted-average holding upsert, immutable trade record),
  guarded by optimistic locking on the portfolio version with a bounded retry,
  realized P&L on sells, and holdings removed at zero quantity.
- **Price feed & valuation** — a `PriceProvider` protocol (deterministic
  `MockPriceProvider` + optional injected-client `HttpPriceProvider`), a
  write-through TTL price cache (`ttl_attr`, checked against an injected clock),
  and a `Valuator` producing market value + unrealized P&L.
- **REST API (FastAPI)** — `create_app(client, ...)` with dependency-injected
  services; endpoints for portfolios, orders, holdings, valuation, cursor-paginated
  trade history, alert CRUD, and prices; pydynantic/domain errors mapped to a
  uniform HTTP error envelope.
- **Alerts evaluator** — fires price-threshold alerts via the GSI2 `by_symbol`
  index, idempotent through a conditional `triggered` guard.
- **Observability** — optional `on_operation` logging hook (pydynantic's
  observability), zero-overhead by default, no logging dependency.
- **DX** — a runnable end-to-end demo on `moto` (`examples/demo.py`), CI matrix
  (3.10–3.13) with ruff + `mypy --strict` + a ≥90% coverage gate, pre-commit, and
  Dependabot.

[2.0.0]: https://github.com/robertruben98/hodlbook/releases/tag/2.0.0
[1.0.0]: https://github.com/robertruben98/hodlbook/releases/tag/1.0.0
