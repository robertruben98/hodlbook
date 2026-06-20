"""Shared pytest fixtures: a mocked DynamoDB table, models, a repository, and API."""

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
from hodlbook.storage import Models, build_table, create_table


@pytest.fixture
def dynamodb_client() -> Iterator[Any]:
    """A fresh mocked DynamoDB client with the ``hodlbook`` table created."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_table(client)
        yield client


@pytest.fixture
def repo(dynamodb_client: Any) -> Repository:
    """A Repository wired to the same mocked table the API uses."""
    return Repository(build_table(dynamodb_client))


@pytest.fixture
def models(repo: Repository) -> Models:
    """The entity classes bound to the same table the ``repo`` fixture uses."""
    return repo.models


@pytest.fixture
def api_client(dynamodb_client: Any) -> Iterator[TestClient]:
    """A TestClient over an app built from the same mocked client as ``repo``."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    app = create_app(
        dynamodb_client,
        provider=MockPriceProvider({"bitcoin": Decimal("50000"), "ethereum": Decimal("3000")}),
        clock=lambda: now,
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_headers(repo: Repository) -> dict[str, str]:
    """A valid bearer-token header for principal ``u1``.

    Issues the key through the same ``repo`` (and thus the same mocked table)
    the ``api_client`` reads from, so the API can authenticate the token.
    """
    raw, _ = repo.issue_api_key("u1")
    return {"Authorization": f"Bearer {raw}"}
