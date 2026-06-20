"""Optional logging/tracing hooks for hodlbook's DynamoDB operations.

pydynantic exposes ``Table(..., on_operation=<callback>)`` which fires an
:class:`~pydynantic.OperationEvent` around every DynamoDB call. This module
builds small, dependency-free hooks on top of that callback so callers can opt
into tracing and cost attribution without hodlbook forcing a logging
dependency on anyone -- it uses only the stdlib :mod:`logging` module.
"""

from __future__ import annotations

import logging

from pydynantic import OperationEvent, OperationHook

_DEFAULT_LOGGER = logging.getLogger("hodlbook.dynamodb")


def logging_hook(logger: logging.Logger | None = None) -> OperationHook:
    """Build an :data:`~pydynantic.OperationHook` that logs each operation.

    Each :class:`~pydynantic.OperationEvent` is logged at ``DEBUG`` on success
    and at ``INFO`` on failure (the exception name is included). Pass a custom
    ``logger`` to route the records; defaults to ``logging.getLogger(
    "hodlbook.dynamodb")``.
    """
    log = logger if logger is not None else _DEFAULT_LOGGER

    def hook(event: OperationEvent) -> None:
        if event.success:
            log.debug(
                "%s table=%s %.1fms ok=%s",
                event.operation,
                event.table_name,
                event.duration_ms,
                event.success,
            )
        else:
            exc_name = type(event.exception).__name__ if event.exception else "unknown"
            log.info(
                "%s table=%s %.1fms ok=%s exc=%s",
                event.operation,
                event.table_name,
                event.duration_ms,
                event.success,
                exc_name,
            )

    return hook


def collecting_hook() -> tuple[OperationHook, list[OperationEvent]]:
    """Build a hook that appends every event to a list, returned alongside it.

    Useful for tests and ad-hoc metrics collection::

        hook, events = collecting_hook()
        table = build_table(client, on_operation=hook)
        ...  # events now holds one OperationEvent per DynamoDB call
    """
    events: list[OperationEvent] = []

    def hook(event: OperationEvent) -> None:
        events.append(event)

    return hook, events
