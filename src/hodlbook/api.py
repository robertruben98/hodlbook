"""FastAPI REST API for hodlbook.

:func:`create_app` wires the storage stack (table, repository, price cache,
trading engine, valuator) from an *injected* boto3 client -- this module never
constructs boto3 itself, so the same factory serves DynamoDB Local, AWS, and
``moto`` tests. The services are built once and parked on ``app.state``; the
``Depends`` accessors read them back with concrete return types so ``mypy
--strict`` stays happy without leaking ``app.state``'s ``Any`` into handlers.

Pydantic request/response schemas live here and never expose pydynantic
entities directly -- every entity is mapped to a schema at the boundary. Domain
and pydynantic exceptions are translated to a uniform ``{"error", "detail"}``
JSON body by centralized handlers, so individual endpoints stay free of
try/except.

All money is :class:`~decimal.Decimal`; floats never touch the wire.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydynantic import (
    ItemNotFoundError,
    OperationHook,
    OptimisticLockError,
    PydynanticError,
)

from .analytics import Analytics
from .errors import (
    AuthenticationError,
    AuthorizationError,
    HodlbookError,
    InsufficientFunds,
    InsufficientHoldings,
    InvalidOrder,
    OrderNotFound,
    TradeConflict,
    UnknownSymbol,
)
from .prices import MockPriceProvider, PriceCache, PriceProvider
from .repository import Repository, _hash_token
from .settings import Settings, get_settings
from .storage import Direction, OrderStatus, OrderType, Side, build_table
from .trading import TradingEngine
from .valuation import Valuator


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


# -- API schemas ------------------------------------------------------------
class PortfolioCreateRequest(BaseModel):
    user_id: str
    portfolio_id: str
    cash: Decimal = Decimal("0")


class PortfolioResponse(BaseModel):
    user_id: str
    portfolio_id: str
    cash: Decimal
    version: int


class OrderRequest(BaseModel):
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal | None = None


class TradeResponse(BaseModel):
    trade_id: str
    portfolio_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    ts: str


class OrderResponse(BaseModel):
    trade: TradeResponse
    realized_pnl: Decimal


class HoldingResponse(BaseModel):
    symbol: str
    quantity: Decimal
    avg_cost: Decimal


class HoldingsResponse(BaseModel):
    holdings: list[HoldingResponse]


class HoldingValuationResponse(BaseModel):
    symbol: str
    quantity: Decimal
    avg_cost: Decimal
    price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal


class ValuationResponse(BaseModel):
    cash: Decimal
    holdings: list[HoldingValuationResponse]
    holdings_value: Decimal
    total_value: Decimal
    total_unrealized_pnl: Decimal


class TradePageResponse(BaseModel):
    items: list[TradeResponse]
    cursor: str | None


class AlertCreateRequest(BaseModel):
    symbol: str
    direction: Direction
    threshold: Decimal


class AlertResponse(BaseModel):
    alert_id: str
    symbol: str
    direction: Direction
    threshold: Decimal
    triggered: bool


class LimitOrderCreateRequest(BaseModel):
    symbol: str
    side: Side
    quantity: Decimal
    limit_price: Decimal


class DcaOrderCreateRequest(BaseModel):
    symbol: str
    side: Side
    quantity: Decimal
    interval_seconds: int
    total_runs: int


class OrderEntityResponse(BaseModel):
    order_id: str
    portfolio_id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    status: OrderStatus
    interval_seconds: int | None
    next_run: datetime | None
    remaining_runs: int | None


class PriceResponse(BaseModel):
    symbol: str
    price: Decimal


class SnapshotResponse(BaseModel):
    portfolio_id: str
    taken_at: str
    total_value: Decimal
    cash: Decimal
    holdings_value: Decimal
    total_unrealized_pnl: Decimal


class SnapshotPageResponse(BaseModel):
    items: list[SnapshotResponse]
    cursor: str | None


class ReturnsResponse(BaseModel):
    series: SnapshotPageResponse
    return_pct: Decimal


class LeaderboardEntryResponse(BaseModel):
    portfolio_id: str
    total_value: Decimal
    rank: int


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntryResponse]


class ErrorResponse(BaseModel):
    error: str
    detail: str


# -- entity -> schema mappers ----------------------------------------------
def _portfolio_response(portfolio: Any) -> PortfolioResponse:
    return PortfolioResponse(
        user_id=portfolio.user_id,
        portfolio_id=portfolio.portfolio_id,
        cash=portfolio.cash,
        version=portfolio.version,
    )


def _trade_response(trade: Any) -> TradeResponse:
    return TradeResponse(
        trade_id=trade.trade_id,
        portfolio_id=trade.portfolio_id,
        symbol=trade.symbol,
        side=trade.side,
        quantity=trade.quantity,
        price=trade.price,
        ts=trade.ts,
    )


def _holding_response(holding: Any) -> HoldingResponse:
    return HoldingResponse(
        symbol=holding.symbol,
        quantity=holding.quantity,
        avg_cost=holding.avg_cost,
    )


def _alert_response(alert: Any) -> AlertResponse:
    return AlertResponse(
        alert_id=alert.alert_id,
        symbol=alert.symbol,
        direction=alert.direction,
        threshold=alert.threshold,
        triggered=alert.triggered,
    )


def _snapshot_response(snapshot: Any) -> SnapshotResponse:
    return SnapshotResponse(
        portfolio_id=snapshot.portfolio_id,
        taken_at=snapshot.taken_at,
        total_value=snapshot.total_value,
        cash=snapshot.cash,
        holdings_value=snapshot.holdings_value,
        total_unrealized_pnl=snapshot.total_unrealized_pnl,
    )


def _snapshot_page_response(page: Any) -> SnapshotPageResponse:
    return SnapshotPageResponse(
        items=[_snapshot_response(s) for s in page.items],
        cursor=page.cursor,
    )


def _order_response(order: Any) -> OrderEntityResponse:
    return OrderEntityResponse(
        order_id=order.order_id,
        portfolio_id=order.portfolio_id,
        symbol=order.symbol,
        side=order.side,
        order_type=order.order_type,
        quantity=order.quantity,
        limit_price=order.limit_price,
        status=order.status,
        interval_seconds=order.interval_seconds,
        next_run=order.next_run,
        remaining_runs=order.remaining_runs,
    )


# -- typed app.state accessors ---------------------------------------------
def get_repo(request: Request) -> Repository:
    repo: Repository = request.app.state.repo
    return repo


def get_engine(request: Request) -> TradingEngine:
    engine: TradingEngine = request.app.state.engine
    return engine


def get_cache(request: Request) -> PriceCache:
    cache: PriceCache = request.app.state.cache
    return cache


def get_valuator(request: Request) -> Valuator:
    valuator: Valuator = request.app.state.valuator
    return valuator


def get_analytics(request: Request) -> Analytics:
    analytics: Analytics = request.app.state.analytics
    return analytics


RepoDep = Annotated[Repository, Depends(get_repo)]
EngineDep = Annotated[TradingEngine, Depends(get_engine)]
CacheDep = Annotated[PriceCache, Depends(get_cache)]
ValuatorDep = Annotated[Valuator, Depends(get_valuator)]
AnalyticsDep = Annotated[Analytics, Depends(get_analytics)]


# -- authentication / authorization ----------------------------------------
def get_principal(request: Request, repo: RepoDep) -> str:
    """Resolve the authenticated principal (``user_id``) from the request.

    Reads a bearer token from ``Authorization: Bearer <token>`` (falling back to
    the ``X-API-Key`` header), hashes it, and looks the key up. A missing,
    unknown, or revoked key raises :class:`AuthenticationError` (-> 401).
    """
    token: str | None = None
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[len("bearer ") :].strip()
    if token is None:
        token = request.headers.get("X-API-Key")
    if not token:
        raise AuthenticationError("missing API key")
    key = repo.get_api_key_by_hash(_hash_token(token))
    if key is None or key.revoked:
        raise AuthenticationError("invalid API key")
    principal: str = key.user_id
    return principal


PrincipalDep = Annotated[str, Depends(get_principal)]


def require_tenant(user_id: str, principal: PrincipalDep) -> str:
    """Authorize a path ``user_id`` against the authenticated ``principal``.

    A caller may only touch resources under their own ``user_id``; any mismatch
    raises :class:`AuthorizationError` (-> 403). The path ``user_id`` is never
    trusted on its own.
    """
    if user_id != principal:
        raise AuthorizationError("cannot access another principal's resources")
    return user_id


# -- exception handlers -----------------------------------------------------
def _error_response(exc: Exception, code: int) -> JSONResponse:
    body = ErrorResponse(error=type(exc).__name__, detail=str(exc))
    return JSONResponse(status_code=code, content=body.model_dump())


def _register_exception_handlers(app: FastAPI) -> None:
    leaf: list[tuple[type[Exception], int]] = [
        (ItemNotFoundError, status.HTTP_404_NOT_FOUND),
        (UnknownSymbol, status.HTTP_404_NOT_FOUND),
        (OrderNotFound, status.HTTP_404_NOT_FOUND),
        (OptimisticLockError, status.HTTP_409_CONFLICT),
        (TradeConflict, status.HTTP_409_CONFLICT),
        (InsufficientFunds, status.HTTP_409_CONFLICT),
        (InsufficientHoldings, status.HTTP_409_CONFLICT),
        (InvalidOrder, status.HTTP_422_UNPROCESSABLE_CONTENT),
        # Auth errors are HodlbookError subclasses; register them as leaves so
        # they map to 401/403 ahead of the generic HodlbookError -> 400 handler.
        (AuthenticationError, status.HTTP_401_UNAUTHORIZED),
        (AuthorizationError, status.HTTP_403_FORBIDDEN),
    ]
    for exc_type, code in leaf:

        def make_handler(
            code: int,
        ) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
            async def handler(request: Request, exc: Exception) -> JSONResponse:
                return _error_response(exc, code)

            return handler

        app.add_exception_handler(exc_type, make_handler(code))

    async def validation_handler(request: Request, exc: Exception) -> JSONResponse:
        return _error_response(exc, status.HTTP_422_UNPROCESSABLE_CONTENT)

    app.add_exception_handler(RequestValidationError, validation_handler)

    async def hodlbook_handler(request: Request, exc: Exception) -> JSONResponse:
        return _error_response(exc, status.HTTP_400_BAD_REQUEST)

    app.add_exception_handler(HodlbookError, hodlbook_handler)

    async def pydynantic_handler(request: Request, exc: Exception) -> JSONResponse:
        return _error_response(exc, status.HTTP_500_INTERNAL_SERVER_ERROR)

    app.add_exception_handler(PydynanticError, pydynantic_handler)


def create_app(
    client: Any,
    *,
    provider: PriceProvider | None = None,
    clock: Callable[[], datetime] | None = None,
    on_operation: OperationHook | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Build the hodlbook FastAPI app from an injected boto3 ``client``.

    Services are constructed once here (never boto3 itself) and stored on
    ``app.state``; the ``Depends`` accessors read them back per request.

    Pass ``on_operation`` (e.g. ``observability.logging_hook()``) to enable
    optional tracing/cost-attribution logging around each DynamoDB call. The
    default of ``None`` leaves behavior unchanged.

    Pass ``settings`` to override the env-driven configuration (defaults to
    :func:`~hodlbook.settings.get_settings`); it drives values like the price
    cache TTL while leaving every other parameter backward-compatible.
    """
    the_settings = settings or get_settings()
    the_clock = clock or _default_clock
    table = build_table(client, on_operation=on_operation)
    repo = Repository(table)
    the_provider = provider or MockPriceProvider({})
    cache = PriceCache(
        repo,
        the_provider,
        clock=the_clock,
        ttl_seconds=the_settings.price_ttl_seconds,
    )
    engine = TradingEngine(repo, clock=the_clock)
    valuator = Valuator(repo, cache)
    analytics = Analytics(repo, valuator, clock=the_clock)

    app = FastAPI(title="hodlbook")
    app.state.settings = the_settings
    app.state.repo = repo
    app.state.engine = engine
    app.state.cache = cache
    app.state.valuator = valuator
    app.state.analytics = analytics

    _register_exception_handlers(app)

    @app.post(
        "/portfolios",
        response_model=PortfolioResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_portfolio(
        body: PortfolioCreateRequest, repo: RepoDep, principal: PrincipalDep
    ) -> PortfolioResponse:
        if body.user_id != principal:
            raise AuthorizationError("cannot create a portfolio for another principal")
        portfolio = repo.create_portfolio(body.user_id, body.portfolio_id, body.cash)
        return _portfolio_response(portfolio)

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}",
        response_model=PortfolioResponse,
        dependencies=[Depends(require_tenant)],
    )
    def get_portfolio(user_id: str, portfolio_id: str, repo: RepoDep) -> PortfolioResponse:
        portfolio = repo.get_portfolio(user_id, portfolio_id)
        if portfolio is None:
            raise ItemNotFoundError(f"portfolio {user_id}/{portfolio_id} not found")
        return _portfolio_response(portfolio)

    @app.post(
        "/portfolios/{user_id}/{portfolio_id}/orders",
        response_model=OrderResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_tenant)],
    )
    def create_order(
        user_id: str,
        portfolio_id: str,
        body: OrderRequest,
        repo: RepoDep,
        engine: EngineDep,
        cache: CacheDep,
    ) -> OrderResponse:
        if repo.get_portfolio(user_id, portfolio_id) is None:
            raise ItemNotFoundError(f"portfolio {user_id}/{portfolio_id} not found")
        price = body.price if body.price is not None else cache.get_cached_price(body.symbol)
        if body.side is Side.BUY:
            result = engine.buy(user_id, portfolio_id, body.symbol, body.quantity, price)
        else:
            result = engine.sell(user_id, portfolio_id, body.symbol, body.quantity, price)
        return OrderResponse(
            trade=_trade_response(result.trade),
            realized_pnl=result.realized_pnl,
        )

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/holdings",
        response_model=HoldingsResponse,
        dependencies=[Depends(require_tenant)],
    )
    def list_holdings(portfolio_id: str, repo: RepoDep) -> HoldingsResponse:
        holdings = repo.get_holdings(portfolio_id)
        return HoldingsResponse(holdings=[_holding_response(h) for h in holdings])

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/valuation",
        response_model=ValuationResponse,
        dependencies=[Depends(require_tenant)],
    )
    def get_valuation(user_id: str, portfolio_id: str, valuator: ValuatorDep) -> ValuationResponse:
        v = valuator.value(user_id, portfolio_id)
        return ValuationResponse(
            cash=v.cash,
            holdings=[
                HoldingValuationResponse(
                    symbol=h.symbol,
                    quantity=h.quantity,
                    avg_cost=h.avg_cost,
                    price=h.price,
                    market_value=h.market_value,
                    unrealized_pnl=h.unrealized_pnl,
                )
                for h in v.holdings
            ],
            holdings_value=v.holdings_value,
            total_value=v.total_value,
            total_unrealized_pnl=v.total_unrealized_pnl,
        )

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/trades",
        response_model=TradePageResponse,
        dependencies=[Depends(require_tenant)],
    )
    def list_trades(
        portfolio_id: str,
        repo: RepoDep,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> TradePageResponse:
        page = repo.list_trades(portfolio_id, cursor=cursor, limit=limit)
        return TradePageResponse(
            items=[_trade_response(t) for t in page.items],
            cursor=page.cursor,
        )

    @app.post(
        "/portfolios/{user_id}/{portfolio_id}/alerts",
        response_model=AlertResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_tenant)],
    )
    def create_alert(portfolio_id: str, body: AlertCreateRequest, repo: RepoDep) -> AlertResponse:
        alert = repo.create_alert(
            portfolio_id,
            uuid4().hex,
            body.symbol,
            body.direction,
            body.threshold,
        )
        return _alert_response(alert)

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/alerts",
        response_model=list[AlertResponse],
        dependencies=[Depends(require_tenant)],
    )
    def list_alerts(portfolio_id: str, repo: RepoDep) -> list[AlertResponse]:
        return [_alert_response(a) for a in repo.list_alerts(portfolio_id)]

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/alerts/{alert_id}",
        response_model=AlertResponse,
        dependencies=[Depends(require_tenant)],
    )
    def get_alert(portfolio_id: str, alert_id: str, repo: RepoDep) -> AlertResponse:
        alert = repo.get_alert(portfolio_id, alert_id)
        if alert is None:
            raise ItemNotFoundError(f"alert {alert_id} not found")
        return _alert_response(alert)

    @app.delete(
        "/portfolios/{user_id}/{portfolio_id}/alerts/{alert_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_tenant)],
    )
    def delete_alert(portfolio_id: str, alert_id: str, repo: RepoDep) -> Response:
        repo.delete_alert(portfolio_id, alert_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/portfolios/{user_id}/{portfolio_id}/orders/limit",
        response_model=OrderEntityResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_tenant)],
    )
    def create_limit_order(
        user_id: str,
        portfolio_id: str,
        body: LimitOrderCreateRequest,
        repo: RepoDep,
    ) -> OrderEntityResponse:
        if repo.get_portfolio(user_id, portfolio_id) is None:
            raise ItemNotFoundError(f"portfolio {user_id}/{portfolio_id} not found")
        order = repo.create_order(
            portfolio_id,
            uuid4().hex,
            user_id,
            body.symbol,
            body.side,
            OrderType.LIMIT,
            body.quantity,
            limit_price=body.limit_price,
        )
        return _order_response(order)

    @app.post(
        "/portfolios/{user_id}/{portfolio_id}/orders/dca",
        response_model=OrderEntityResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_tenant)],
    )
    def create_dca_order(
        user_id: str,
        portfolio_id: str,
        body: DcaOrderCreateRequest,
        repo: RepoDep,
    ) -> OrderEntityResponse:
        if repo.get_portfolio(user_id, portfolio_id) is None:
            raise ItemNotFoundError(f"portfolio {user_id}/{portfolio_id} not found")
        # Persisted OPEN with no cash movement: the first tick is due immediately
        # (next_run = now) and the executor drives subsequent fills.
        order = repo.create_order(
            portfolio_id,
            uuid4().hex,
            user_id,
            body.symbol,
            body.side,
            OrderType.DCA,
            body.quantity,
            interval_seconds=body.interval_seconds,
            next_run=the_clock(),
            remaining_runs=body.total_runs,
        )
        return _order_response(order)

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/orders",
        response_model=list[OrderEntityResponse],
        dependencies=[Depends(require_tenant)],
    )
    def list_orders(portfolio_id: str, repo: RepoDep) -> list[OrderEntityResponse]:
        return [_order_response(o) for o in repo.list_orders(portfolio_id)]

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/orders/{order_id}",
        response_model=OrderEntityResponse,
        dependencies=[Depends(require_tenant)],
    )
    def get_order(portfolio_id: str, order_id: str, repo: RepoDep) -> OrderEntityResponse:
        order = repo.get_order(portfolio_id, order_id)
        if order is None:
            raise OrderNotFound(f"order {order_id} not found")
        return _order_response(order)

    @app.delete(
        "/portfolios/{user_id}/{portfolio_id}/orders/{order_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_tenant)],
    )
    def cancel_order(portfolio_id: str, order_id: str, repo: RepoDep) -> Response:
        if repo.get_order(portfolio_id, order_id) is None:
            raise OrderNotFound(f"order {order_id} not found")
        repo.cancel_order(portfolio_id, order_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        "/prices/{symbol}",
        response_model=PriceResponse,
        dependencies=[Depends(get_principal)],
    )
    def get_price(symbol: str, cache: CacheDep) -> PriceResponse:
        price = cache.get_cached_price(symbol)
        return PriceResponse(symbol=symbol, price=price)

    @app.post(
        "/portfolios/{user_id}/{portfolio_id}/snapshots",
        response_model=SnapshotResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_tenant)],
    )
    def take_snapshot(
        user_id: str, portfolio_id: str, repo: RepoDep, analytics: AnalyticsDep
    ) -> SnapshotResponse:
        if repo.get_portfolio(user_id, portfolio_id) is None:
            raise ItemNotFoundError(f"portfolio {user_id}/{portfolio_id} not found")
        return _snapshot_response(analytics.take_snapshot(user_id, portfolio_id))

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/snapshots",
        response_model=SnapshotPageResponse,
        dependencies=[Depends(require_tenant)],
    )
    def list_snapshots(
        portfolio_id: str,
        analytics: AnalyticsDep,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> SnapshotPageResponse:
        page = analytics.series(portfolio_id, cursor=cursor, limit=limit)
        return _snapshot_page_response(page)

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/returns",
        response_model=ReturnsResponse,
        dependencies=[Depends(require_tenant)],
    )
    def get_returns(
        portfolio_id: str,
        analytics: AnalyticsDep,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> ReturnsResponse:
        page = analytics.series(portfolio_id, cursor=cursor, limit=limit)
        return ReturnsResponse(
            series=_snapshot_page_response(page),
            return_pct=analytics.returns(portfolio_id),
        )

    @app.get(
        "/leaderboard",
        response_model=LeaderboardResponse,
        # Authenticated but intentionally cross-tenant: the leaderboard ranks
        # every principal's portfolios, so it requires a valid principal
        # (Depends(get_principal)) but NOT require_tenant. To avoid leaking other
        # tenants' details, the response exposes only portfolio_id + total_value +
        # rank -- never user_id, holdings, or cash.
        dependencies=[Depends(get_principal)],
    )
    def get_leaderboard(analytics: AnalyticsDep, limit: int = 10) -> LeaderboardResponse:
        entries = analytics.leaderboard(limit)
        return LeaderboardResponse(
            entries=[
                LeaderboardEntryResponse(
                    portfolio_id=e.portfolio_id,
                    total_value=e.total_value,
                    rank=rank,
                )
                for rank, e in enumerate(entries, start=1)
            ]
        )

    return app
