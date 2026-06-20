"""Shared pytest fixtures: a mocked DynamoDB table, models, and a repository."""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from hodlbook.repository import Repository
from hodlbook.storage import Models, build_table, create_table


@pytest.fixture
def repo() -> Iterator[Repository]:
    """A Repository wired to a fresh mocked ``hodlbook`` table, per test."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_table(client)
        table = build_table(client)
        yield Repository(table)


@pytest.fixture
def models(repo: Repository) -> Models:
    """The entity classes bound to the same table the ``repo`` fixture uses."""
    return repo.models
