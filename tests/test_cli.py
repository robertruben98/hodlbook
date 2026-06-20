"""Network-free tests for the ``hodlbook`` admin CLI.

The CLI normally builds a boto3 client from the environment; here we inject a
``moto``-mocked client via ``args.client`` (set with ``--`` not possible, so we
patch :func:`hodlbook.cli.build_client`) and assert each subcommand returns 0
and produces the expected side effects on the table.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import boto3
import pytest
from moto import mock_aws

from hodlbook import cli
from hodlbook.repository import Repository
from hodlbook.storage import TABLE_NAME, build_table, create_table


@pytest.fixture
def mocked_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A moto DynamoDB client that ``cli.build_client`` is patched to return."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        monkeypatch.setattr(cli, "build_client", lambda **_: client)
        yield client


def test_create_table_provisions_table(mocked_client: Any) -> None:
    rc = cli.main(["create-table", "--region", "us-east-1"])
    assert rc == 0
    names = mocked_client.list_tables()["TableNames"]
    assert TABLE_NAME in names


def test_seed_demo_creates_portfolio_and_trades(mocked_client: Any) -> None:
    create_table(mocked_client)

    rc = cli.main(["seed-demo", "--user-id", "alice", "--portfolio-id", "p1", "--cash", "100000"])
    assert rc == 0

    repo = Repository(build_table(mocked_client))
    portfolio = repo.get_portfolio("alice", "p1")
    assert portfolio is not None
    # Two buys debited cash from the 100000 starting balance.
    assert portfolio.cash < Decimal("100000")

    trades = repo.list_trades("p1").items
    assert len(trades) == 2
    symbols = {t.symbol for t in trades}
    assert symbols == {"bitcoin", "ethereum"}


def test_issue_api_key_is_noop(mocked_client: Any) -> None:
    assert cli.main(["issue-api-key"]) == 0


def test_refresh_prices_is_noop(mocked_client: Any) -> None:
    assert cli.main(["refresh-prices"]) == 0


def test_create_table_twice_is_idempotent(mocked_client: Any) -> None:
    assert cli.main(["create-table"]) == 0
    # Second call hits ResourceInUseException and is handled gracefully.
    assert cli.main(["create-table"]) == 0
