"""Storage-layer construction tests: table shape and entity key templates."""

from __future__ import annotations

from decimal import Decimal

import boto3
from moto import mock_aws

from hodlbook.repository import Repository
from hodlbook.storage import (
    TABLE_NAME,
    Direction,
    Models,
    Side,
    build_table,
    create_table,
)


def test_create_table_shape() -> None:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_table(client)
        desc = client.describe_table(TableName=TABLE_NAME)["Table"]
        assert desc["BillingModeSummary"]["BillingMode"] == "PAY_PER_REQUEST"
        gsis = {g["IndexName"] for g in desc["GlobalSecondaryIndexes"]}
        assert gsis == {"GSI1", "GSI2"}
        for g in desc["GlobalSecondaryIndexes"]:
            assert g["Projection"]["ProjectionType"] == "ALL"


def test_build_table_indexes() -> None:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        table = build_table(client)
        assert table.name == TABLE_NAME
        assert table.pk == "PK"
        assert table.sk == "SK"
        assert set(table.indexes) == {"GSI1", "GSI2"}


def test_build_models_returns_five_entities(repo: Repository) -> None:
    models = repo.models
    assert isinstance(models, Models)
    assert models.Portfolio.__entity_name__ == "portfolio"
    assert models.Holding.__entity_name__ == "holding"
    assert models.Trade.__entity_name__ == "trade"
    assert models.PriceTick.__entity_name__ == "price_tick"
    assert models.Alert.__entity_name__ == "alert"


def test_models_frozen(repo: Repository) -> None:
    import dataclasses

    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        repo.models.Portfolio = repo.models.Holding  # type: ignore[misc]


def test_portfolio_key_template(models: Models) -> None:
    p = models.Portfolio(user_id="u1", portfolio_id="p1")
    item = p.to_dynamo()
    assert item["PK"]["S"] == "USER#u1"
    assert item["SK"]["S"] == "PORTFOLIO#p1"


def test_trade_gsi1_key_template(models: Models) -> None:
    t = models.Trade(
        portfolio_id="p1",
        trade_id="t1",
        symbol="BTC",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        ts="2024-01-01T00:00:00Z",
    )
    item = t.to_dynamo()
    assert item["PK"]["S"] == "PORTFOLIO#p1"
    assert item["SK"]["S"] == "TRADE#2024-01-01T00:00:00Z#t1"
    assert item["GSI1PK"]["S"] == "SYMBOL#BTC"
    assert item["GSI1SK"]["S"] == "TRADE#2024-01-01T00:00:00Z"


def test_alert_gsi2_key_template(models: Models) -> None:
    a = models.Alert(
        portfolio_id="p1",
        alert_id="a1",
        symbol="ETH",
        direction=Direction.ABOVE,
        threshold=Decimal("3000"),
    )
    item = a.to_dynamo()
    assert item["PK"]["S"] == "PORTFOLIO#p1"
    assert item["SK"]["S"] == "ALERT#a1"
    assert item["GSI2PK"]["S"] == "SYMBOL#ETH"
    assert item["GSI2SK"]["S"] == "ALERT#a1"


def test_price_tick_key_template(models: Models) -> None:
    from datetime import datetime, timezone

    tick = models.PriceTick(
        symbol="BTC", price=Decimal("50000"), as_of=datetime(2024, 1, 1, tzinfo=timezone.utc)
    )
    item = tick.to_dynamo()
    assert item["PK"]["S"] == "PRICE#BTC"
    assert item["SK"]["S"] == "TICK"
