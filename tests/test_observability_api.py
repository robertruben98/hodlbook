"""Tests for M12 ops endpoints, metrics, request-id, and M14 rate limiting."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from hodlbook.api import create_app
from hodlbook.prices import MockPriceProvider
from hodlbook.repository import Repository
from hodlbook.settings import Settings
from hodlbook.storage import build_table, create_table

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_client(client: Any, settings: Settings | None = None) -> TestClient:
    app = create_app(
        client,
        provider=MockPriceProvider({"bitcoin": Decimal("50000")}),
        clock=lambda: _NOW,
        settings=settings,
    )
    return TestClient(app)


# -- healthz / readyz -------------------------------------------------------
def test_healthz_is_200(api_client: TestClient) -> None:
    resp = api_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_is_200_with_table(api_client: TestClient) -> None:
    resp = api_client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_readyz_is_503_when_describe_table_raises(dynamodb_client: Any) -> None:
    client = _make_client(dynamodb_client)

    def boom(**_: Any) -> None:
        raise RuntimeError("dynamodb down")

    client.app.state.client.describe_table = boom  # type: ignore[attr-defined]
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "RuntimeError"
    assert "down" in body["detail"]


# -- metrics ----------------------------------------------------------------
def test_metrics_exposes_prometheus_text_and_http_counter(
    api_client: TestClient,
) -> None:
    # Hit an endpoint so the http counter has a sample to expose.
    api_client.get("/healthz")
    resp = api_client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "http_requests_total" in body
    assert 'route="/healthz"' in body


def test_metrics_disabled_returns_404() -> None:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_table(client)
        settings = Settings(metrics_enabled=False)
        tc = _make_client(client, settings)
        assert tc.get("/metrics").status_code == 404


def test_dynamodb_op_metrics_recorded_via_hook(
    api_client: TestClient, auth_headers: dict[str, str]
) -> None:
    api_client.post(
        "/v1/portfolios",
        json={"user_id": "u1", "portfolio_id": "p1", "cash": "100"},
        headers=auth_headers,
    )
    body = api_client.get("/metrics").text
    assert "dynamodb_operations_total" in body
    assert 'operation="put_item"' in body
    assert 'success="true"' in body


# -- request id -------------------------------------------------------------
def test_request_id_generated_and_echoed(api_client: TestClient) -> None:
    resp = api_client.get("/healthz")
    rid = resp.headers.get("X-Request-ID")
    assert rid is not None and len(rid) > 0


def test_request_id_honored_when_supplied(api_client: TestClient) -> None:
    resp = api_client.get("/healthz", headers={"X-Request-ID": "trace-123"})
    assert resp.headers["X-Request-ID"] == "trace-123"


# -- rate limiting ----------------------------------------------------------
@pytest.fixture
def rate_limited_client() -> Iterator[tuple[TestClient, Repository]]:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_table(client)
        repo = Repository(build_table(client))
        settings = Settings(rate_limit_per_minute=2)
        tc = _make_client(client, settings)
        with tc:
            yield tc, repo


def test_rate_limit_returns_429_after_quota(
    rate_limited_client: tuple[TestClient, Repository],
) -> None:
    tc, repo = rate_limited_client
    raw, _ = repo.issue_api_key("u1")
    headers = {"Authorization": f"Bearer {raw}"}

    # limit=2: first two requests pass, the third is rejected with 429.
    assert tc.get("/v1/prices/bitcoin", headers=headers).status_code == 200
    assert tc.get("/v1/prices/bitcoin", headers=headers).status_code == 200
    resp = tc.get("/v1/prices/bitcoin", headers=headers)
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"] == "RateLimitExceeded"
    assert "rate limit" in body["detail"]


def test_ops_endpoints_exempt_from_rate_limit(
    rate_limited_client: tuple[TestClient, Repository],
) -> None:
    tc, _ = rate_limited_client
    # Far more than the limit of 2; health stays 200 since it is unversioned.
    for _ in range(5):
        assert tc.get("/healthz").status_code == 200


def test_old_unversioned_path_is_404(api_client: TestClient) -> None:
    # Clean break: business routes live only under /v1 now.
    assert api_client.get("/prices/bitcoin").status_code == 404
