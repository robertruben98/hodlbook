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
from pydynantic import ItemNotFoundError, OptimisticLockError, PydynanticError

from .errors import (
    HodlbookError,
    InsufficientFunds,
    InsufficientHoldings,
    InvalidOrder,
    TradeConflict,
    UnknownSymbol,
)
from .prices import MockPriceProvider, PriceCache, PriceProvider
from .repository import Repository
from .storage import Direction, Side, build_table
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


class PriceResponse(BaseModel):
    symbol: str
    price: Decimal


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


RepoDep = Annotated[Repository, Depends(get_repo)]
EngineDep = Annotated[TradingEngine, Depends(get_engine)]
CacheDep = Annotated[PriceCache, Depends(get_cache)]
ValuatorDep = Annotated[Valuator, Depends(get_valuator)]


# -- exception handlers -----------------------------------------------------
def _error_response(exc: Exception, code: int) -> JSONResponse:
    body = ErrorResponse(error=type(exc).__name__, detail=str(exc))
    return JSONResponse(status_code=code, content=body.model_dump())


def _register_exception_handlers(app: FastAPI) -> None:
    leaf: list[tuple[type[Exception], int]] = [
        (ItemNotFoundError, status.HTTP_404_NOT_FOUND),
        (UnknownSymbol, status.HTTP_404_NOT_FOUND),
        (OptimisticLockError, status.HTTP_409_CONFLICT),
        (TradeConflict, status.HTTP_409_CONFLICT),
        (InsufficientFunds, status.HTTP_409_CONFLICT),
        (InsufficientHoldings, status.HTTP_409_CONFLICT),
        (InvalidOrder, status.HTTP_422_UNPROCESSABLE_CONTENT),
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
) -> FastAPI:
    """Build the hodlbook FastAPI app from an injected boto3 ``client``.

    Services are constructed once here (never boto3 itself) and stored on
    ``app.state``; the ``Depends`` accessors read them back per request.
    """
    the_clock = clock or _default_clock
    table = build_table(client)
    repo = Repository(table)
    the_provider = provider or MockPriceProvider({})
    cache = PriceCache(repo, the_provider, clock=the_clock)
    engine = TradingEngine(repo, clock=the_clock)
    valuator = Valuator(repo, cache)

    app = FastAPI(title="hodlbook")
    app.state.repo = repo
    app.state.engine = engine
    app.state.cache = cache
    app.state.valuator = valuator

    _register_exception_handlers(app)

    @app.post(
        "/portfolios",
        response_model=PortfolioResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_portfolio(body: PortfolioCreateRequest, repo: RepoDep) -> PortfolioResponse:
        portfolio = repo.create_portfolio(body.user_id, body.portfolio_id, body.cash)
        return _portfolio_response(portfolio)

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}",
        response_model=PortfolioResponse,
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
    )
    def list_holdings(portfolio_id: str, repo: RepoDep) -> HoldingsResponse:
        holdings = repo.get_holdings(portfolio_id)
        return HoldingsResponse(holdings=[_holding_response(h) for h in holdings])

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/valuation",
        response_model=ValuationResponse,
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
    )
    def list_alerts(portfolio_id: str, repo: RepoDep) -> list[AlertResponse]:
        return [_alert_response(a) for a in repo.list_alerts(portfolio_id)]

    @app.get(
        "/portfolios/{user_id}/{portfolio_id}/alerts/{alert_id}",
        response_model=AlertResponse,
    )
    def get_alert(portfolio_id: str, alert_id: str, repo: RepoDep) -> AlertResponse:
        alert = repo.get_alert(portfolio_id, alert_id)
        if alert is None:
            raise ItemNotFoundError(f"alert {alert_id} not found")
        return _alert_response(alert)

    @app.delete(
        "/portfolios/{user_id}/{portfolio_id}/alerts/{alert_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_alert(portfolio_id: str, alert_id: str, repo: RepoDep) -> Response:
        repo.delete_alert(portfolio_id, alert_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/prices/{symbol}", response_model=PriceResponse)
    def get_price(symbol: str, cache: CacheDep) -> PriceResponse:
        price = cache.get_cached_price(symbol)
        return PriceResponse(symbol=symbol, price=price)

    return app
