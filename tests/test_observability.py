"""Tests for the optional ``on_operation`` observability hook (ROADMAP M6)."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from pydynantic import OperationEvent

from hodlbook.observability import collecting_hook, logging_hook
from hodlbook.repository import Repository
from hodlbook.storage import TABLE_NAME, build_table


def test_collecting_hook_captures_put_and_get(dynamodb_client: Any) -> None:
    """A hook wired via build_table sees one event per DynamoDB call."""
    hook, events = collecting_hook()
    repo = Repository(build_table(dynamodb_client, on_operation=hook))

    repo.create_portfolio("u1", "p1", Decimal("100"))
    fetched = repo.get_portfolio("u1", "p1")

    assert fetched is not None
    operations = [e.operation for e in events]
    assert "put_item" in operations
    assert "get_item" in operations
    assert all(isinstance(e, OperationEvent) for e in events)
    assert all(e.table_name == TABLE_NAME for e in events)
    assert all(e.success is True for e in events)
    assert all(e.duration_ms >= 0 for e in events)


def test_no_hook_leaves_behavior_unchanged(dynamodb_client: Any) -> None:
    """With on_operation=None the default path is taken and round-trips work."""
    hook, events = collecting_hook()
    instrumented = Repository(build_table(dynamodb_client, on_operation=hook))
    plain = Repository(build_table(dynamodb_client))  # on_operation defaults to None

    plain.create_portfolio("u2", "p2", Decimal("250"))
    fetched = plain.get_portfolio("u2", "p2")

    # The plain repo behaves identically and fired no events on the other hook.
    assert fetched is not None
    assert fetched.cash == Decimal("250")
    assert events == []
    # Sanity: the instrumented repo would have collected events for the same ops.
    instrumented.create_portfolio("u3", "p3", Decimal("0"))
    assert any(e.operation == "put_item" for e in events)


def test_logging_hook_logs_at_debug(dynamodb_client: Any, caplog: Any) -> None:
    """logging_hook emits a DEBUG record per successful operation."""
    logger = logging.getLogger("hodlbook.test.obs")
    repo = Repository(build_table(dynamodb_client, on_operation=logging_hook(logger)))

    with caplog.at_level(logging.DEBUG, logger="hodlbook.test.obs"):
        repo.create_portfolio("u4", "p4", Decimal("10"))

    messages = [r.getMessage() for r in caplog.records]
    assert any("put_item" in m and TABLE_NAME in m and "ok=True" in m for m in messages)


def test_logging_hook_default_logger() -> None:
    """logging_hook with no argument returns a usable callback."""
    hook = logging_hook()
    event = OperationEvent(
        operation="get_item",
        table_name=TABLE_NAME,
        duration_ms=1.5,
        success=True,
        exception=None,
        consumed_capacity=None,
    )
    # Must not raise even with no handlers configured.
    hook(event)
