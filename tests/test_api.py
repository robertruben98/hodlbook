"""End-to-end tests for the hodlbook FastAPI REST API.

Drives the full lifecycle through a ``TestClient`` over an app built from the
same mocked DynamoDB client as the ``repo`` fixture: create a portfolio, place
buy orders, inspect holdings and valuation, page through trades via the
returned cursor, then sell for realized P&L. Also covers cached-price orders,
the uniform error envelope across the failure modes, alert CRUD, and the price
endpoint.

Every request carries the ``auth_headers`` bearer token for principal ``u1``;
all portfolios live under that principal so authorization passes.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _assert_error_shape(body: dict[str, object], error: str) -> None:
    assert set(body) == {"error", "detail"}
    assert body["error"] == error
    assert isinstance(body["detail"], str)


def test_full_flow(api_client: TestClient, auth_headers: dict[str, str]) -> None:
    # Create a portfolio.
    resp = api_client.post(
        "/portfolios",
        json={"user_id": "u1", "portfolio_id": "p1", "cash": "1000000"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["cash"] == "1000000"
    assert body["version"] == 1

    # Buy 2 BTC @ 50000 (explicit price).
    resp = api_client.post(
        "/portfolios/u1/p1/orders",
        json={"symbol": "bitcoin", "side": "BUY", "quantity": "2", "price": "50000"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["trade"]["side"] == "BUY"
    assert order["trade"]["quantity"] == "2"
    assert order["realized_pnl"] == "0"

    # Holdings reflect the buy.
    resp = api_client.get("/portfolios/u1/p1/holdings", headers=auth_headers)
    assert resp.status_code == 200
    holdings = resp.json()["holdings"]
    assert len(holdings) == 1
    assert holdings[0]["symbol"] == "bitcoin"
    assert holdings[0]["quantity"] == "2"
    assert holdings[0]["avg_cost"] == "50000"

    # Valuation: cached BTC price is 50000, so no unrealized P&L.
    resp = api_client.get("/portfolios/u1/p1/valuation", headers=auth_headers)
    assert resp.status_code == 200
    val = resp.json()
    assert val["cash"] == "900000"  # 1000000 - 100000
    assert val["holdings_value"] == "100000"
    assert val["total_value"] == "1000000"
    assert val["total_unrealized_pnl"] == "0"

    # Sell 1 BTC @ 60000 -> realized P&L (60000 - 50000) * 1 = 10000.
    resp = api_client.post(
        "/portfolios/u1/p1/orders",
        json={"symbol": "bitcoin", "side": "SELL", "quantity": "1", "price": "60000"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["realized_pnl"] == "10000"


def test_order_omitted_price_uses_cache(
    api_client: TestClient, auth_headers: dict[str, str]
) -> None:
    api_client.post(
        "/portfolios",
        json={"user_id": "u1", "portfolio_id": "p", "cash": "1000000"},
        headers=auth_headers,
    )
    # No price field -> falls back to the cached MockPriceProvider price (50000).
    resp = api_client.post(
        "/portfolios/u1/p/orders",
        json={"symbol": "bitcoin", "side": "BUY", "quantity": "1"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["trade"]["price"] == "50000"


def test_trades_pagination(api_client: TestClient, auth_headers: dict[str, str]) -> None:
    api_client.post(
        "/portfolios",
        json={"user_id": "u1", "portfolio_id": "p", "cash": "1000000"},
        headers=auth_headers,
    )
    for _ in range(4):
        api_client.post(
            "/portfolios/u1/p/orders",
            json={"symbol": "bitcoin", "side": "BUY", "quantity": "1", "price": "100"},
            headers=auth_headers,
        )

    # First page of 2.
    resp = api_client.get("/portfolios/u1/p/trades", params={"limit": 2}, headers=auth_headers)
    assert resp.status_code == 200
    page1 = resp.json()
    assert len(page1["items"]) == 2
    assert page1["cursor"] is not None

    # Second page via the returned cursor.
    resp = api_client.get(
        "/portfolios/u1/p/trades",
        params={"limit": 2, "cursor": page1["cursor"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    page2 = resp.json()
    assert len(page2["items"]) == 2

    ids1 = {t["trade_id"] for t in page1["items"]}
    ids2 = {t["trade_id"] for t in page2["items"]}
    assert ids1.isdisjoint(ids2)
    assert len(ids1 | ids2) == 4


def test_get_portfolio_404(api_client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = api_client.get("/portfolios/u1/nope", headers=auth_headers)
    assert resp.status_code == 404
    _assert_error_shape(resp.json(), "ItemNotFoundError")


def test_order_insufficient_funds_409(api_client: TestClient, auth_headers: dict[str, str]) -> None:
    api_client.post(
        "/portfolios",
        json={"user_id": "u1", "portfolio_id": "p", "cash": "100"},
        headers=auth_headers,
    )
    resp = api_client.post(
        "/portfolios/u1/p/orders",
        json={"symbol": "bitcoin", "side": "BUY", "quantity": "1", "price": "50000"},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    _assert_error_shape(resp.json(), "InsufficientFunds")


def test_order_unknown_portfolio_404(api_client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = api_client.post(
        "/portfolios/u1/ghost/orders",
        json={"symbol": "bitcoin", "side": "BUY", "quantity": "1", "price": "100"},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    _assert_error_shape(resp.json(), "ItemNotFoundError")


def test_order_malformed_body_422(api_client: TestClient, auth_headers: dict[str, str]) -> None:
    api_client.post(
        "/portfolios",
        json={"user_id": "u1", "portfolio_id": "p", "cash": "100"},
        headers=auth_headers,
    )
    # Missing required 'quantity'.
    resp = api_client.post(
        "/portfolios/u1/p/orders",
        json={"symbol": "bitcoin", "side": "BUY"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    _assert_error_shape(resp.json(), "RequestValidationError")


def test_price_unknown_symbol_404(api_client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = api_client.get("/prices/dogecoin", headers=auth_headers)
    assert resp.status_code == 404
    _assert_error_shape(resp.json(), "UnknownSymbol")


def test_price_known_symbol(api_client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = api_client.get("/prices/ethereum", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "ethereum"
    assert body["price"] == "3000"


def test_alerts_crud(api_client: TestClient, auth_headers: dict[str, str]) -> None:
    api_client.post(
        "/portfolios",
        json={"user_id": "u1", "portfolio_id": "p", "cash": "100"},
        headers=auth_headers,
    )

    # Create.
    resp = api_client.post(
        "/portfolios/u1/p/alerts",
        json={"symbol": "bitcoin", "direction": "ABOVE", "threshold": "70000"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    alert = resp.json()
    alert_id = alert["alert_id"]
    assert alert["triggered"] is False
    assert alert["threshold"] == "70000"

    # List.
    resp = api_client.get("/portfolios/u1/p/alerts", headers=auth_headers)
    assert resp.status_code == 200
    assert [a["alert_id"] for a in resp.json()] == [alert_id]

    # Get.
    resp = api_client.get(f"/portfolios/u1/p/alerts/{alert_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["alert_id"] == alert_id

    # Delete -> 204.
    resp = api_client.delete(f"/portfolios/u1/p/alerts/{alert_id}", headers=auth_headers)
    assert resp.status_code == 204

    # Get the deleted alert -> 404.
    resp = api_client.get(f"/portfolios/u1/p/alerts/{alert_id}", headers=auth_headers)
    assert resp.status_code == 404
    _assert_error_shape(resp.json(), "ItemNotFoundError")
