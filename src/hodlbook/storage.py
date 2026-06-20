"""Single-table storage layer for hodlbook.

Defines the ``hodlbook`` DynamoDB table shape and the five entity classes that
live in it. The boto3 client is injected everywhere (DynamoDB Local / AWS in
prod, ``moto`` in tests) -- no hidden global state. Entities are defined inside
:func:`build_models` so each injected client gets its own isolated set of
classes, mirroring pydynantic's own conftest factory pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydynantic import (
    Entity,
    OperationHook,
    Table,
    created_at_attr,
    key,
    ttl_attr,
    updated_at_attr,
    version_attr,
)

TABLE_NAME = "hodlbook"


class Side(str, Enum):
    """Direction of a trade."""

    BUY = "BUY"
    SELL = "SELL"


class Direction(str, Enum):
    """Whether a price alert fires above or below its threshold."""

    ABOVE = "ABOVE"
    BELOW = "BELOW"


def build_table(client: Any, *, on_operation: OperationHook | None = None) -> Table:
    """Build the ``hodlbook`` :class:`~pydynantic.Table` bound to ``client``.

    Pass ``on_operation`` to opt into pydynantic's observability hook -- it
    fires once per DynamoDB call for tracing and cost attribution. The default
    of ``None`` leaves behavior unchanged.
    """
    return Table(
        name=TABLE_NAME,
        pk="PK",
        sk="SK",
        indexes={
            "GSI1": {"pk": "GSI1PK", "sk": "GSI1SK"},
            "GSI2": {"pk": "GSI2PK", "sk": "GSI2SK"},
        },
        client=client,
        on_operation=on_operation,
    )


def create_table(client: Any) -> None:
    """Issue a boto3 ``create_table`` for the ``hodlbook`` single table.

    PK/SK plus two GSIs (GSI1, GSI2), all attributes ``S``, projection ALL,
    on-demand billing. Mirrors pydynantic's ``tests/conftest.py``.
    """
    client.create_table(
        TableName=TABLE_NAME,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "GSI2",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    )


@dataclass(frozen=True)
class Models:
    """Container of the five entity classes bound to one table."""

    Portfolio: type[Entity]
    Holding: type[Entity]
    Trade: type[Entity]
    PriceTick: type[Entity]
    Alert: type[Entity]
    ApiKey: type[Entity]


def build_models(table: Table) -> Models:
    """Define the five entity classes bound to ``table`` and return :class:`Models`.

    Defining the classes here (rather than at module scope) keeps the injected
    client isolated per table -- the same factory approach pydynantic uses in its
    own test suite.
    """

    class Portfolio(Entity, table=table, name="portfolio"):
        user_id: str
        portfolio_id: str
        cash: Decimal = Decimal("0")
        version: int = version_attr()
        created_at: datetime | None = created_at_attr()
        updated_at: datetime | None = updated_at_attr()

        class Meta:
            primary = key(pk="USER#{user_id}", sk="PORTFOLIO#{portfolio_id}")

    class Holding(Entity, table=table, name="holding"):
        portfolio_id: str
        symbol: str
        quantity: Decimal
        avg_cost: Decimal

        class Meta:
            primary = key(pk="PORTFOLIO#{portfolio_id}", sk="HOLDING#{symbol}")

    class Trade(Entity, table=table, name="trade"):
        portfolio_id: str
        trade_id: str
        symbol: str
        side: Side
        quantity: Decimal
        price: Decimal
        ts: str

        class Meta:
            primary = key(pk="PORTFOLIO#{portfolio_id}", sk="TRADE#{ts}#{trade_id}")
            by_symbol = key(index="GSI1", pk="SYMBOL#{symbol}", sk="TRADE#{ts}")

    class PriceTick(Entity, table=table, name="price_tick"):
        symbol: str
        price: Decimal
        as_of: datetime
        expires_at: datetime | None = ttl_attr()

        class Meta:
            primary = key(pk="PRICE#{symbol}", sk="TICK")

    class Alert(Entity, table=table, name="alert"):
        portfolio_id: str
        alert_id: str
        symbol: str
        direction: Direction
        threshold: Decimal
        triggered: bool = False

        class Meta:
            primary = key(pk="PORTFOLIO#{portfolio_id}", sk="ALERT#{alert_id}")
            by_symbol = key(index="GSI2", pk="SYMBOL#{symbol}", sk="ALERT#{alert_id}")

    class ApiKey(Entity, table=table, name="api_key"):
        key_id: str
        user_id: str
        key_hash: str
        revoked: bool = False
        created_at: datetime | None = created_at_attr()

        class Meta:
            primary = key(pk="APIKEY#{key_hash}", sk="APIKEY")
            by_user = key(index="GSI1", pk="USER#{user_id}", sk="APIKEY#{key_id}")

    return Models(
        Portfolio=Portfolio,
        Holding=Holding,
        Trade=Trade,
        PriceTick=PriceTick,
        Alert=Alert,
        ApiKey=ApiKey,
    )
