# Changelog

All notable changes to hodlbook are documented in this file. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.0.0]: https://github.com/robertruben98/hodlbook/releases/tag/1.0.0
