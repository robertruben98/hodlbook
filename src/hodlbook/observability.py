"""Optional logging/tracing hooks for hodlbook's DynamoDB operations.

pydynantic exposes ``Table(..., on_operation=<callback>)`` which fires an
:class:`~pydynantic.OperationEvent` around every DynamoDB call. This module
builds small, dependency-free hooks on top of that callback so callers can opt
into tracing and cost attribution without hodlbook forcing a logging
dependency on anyone -- it uses only the stdlib :mod:`logging` module.

Beyond the DynamoDB hooks it also hosts the M12 observability primitives:
structured JSON logging (:func:`setup_logging`), a per-app Prometheus metrics
holder (:func:`build_metrics`), and a stdlib fixed-window
:class:`RateLimiter`. The Prometheus pieces use ``prometheus-client`` (already
a dependency); everything else is pure stdlib.
"""

from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
)
from pydynantic import OperationEvent, OperationHook

_DEFAULT_LOGGER = logging.getLogger("hodlbook.dynamodb")

#: Per-request correlation id, set by the API middleware and echoed into logs.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


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


# -- structured JSON logging ------------------------------------------------
class JsonFormatter(logging.Formatter):
    """Render each log record as a single line of JSON.

    The payload carries the timestamp, level, logger name, message, and -- when
    set by the API middleware -- the current ``request_id`` from
    :data:`request_id_var`. No third-party dependency is used.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        return json.dumps(payload)


def setup_logging(level: str) -> None:
    """Configure the root logger with a single JSON-emitting stream handler.

    Idempotent across calls: any handler previously installed by this function
    is removed first so repeated ``create_app`` calls (e.g. in tests) do not
    stack handlers. ``level`` is a stdlib level name such as ``"INFO"``.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_hodlbook_json", False):
            root.removeHandler(existing)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._hodlbook_json = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level.upper())


# -- Prometheus metrics -----------------------------------------------------
@dataclass
class Metrics:
    """A per-app holder for the Prometheus collectors and the DynamoDB hook."""

    registry: CollectorRegistry
    http_requests_total: Counter
    http_request_duration_seconds: Histogram
    dynamodb_operations_total: Counter
    dynamodb_operation_duration_seconds: Histogram
    metrics_hook: OperationHook = field(init=False)

    def __post_init__(self) -> None:
        def hook(event: OperationEvent) -> None:
            self.dynamodb_operations_total.labels(
                operation=event.operation,
                success=str(event.success).lower(),
            ).inc()
            self.dynamodb_operation_duration_seconds.labels(
                operation=event.operation,
            ).observe(event.duration_ms / 1000.0)

        self.metrics_hook = hook


def build_metrics(registry: CollectorRegistry) -> Metrics:
    """Build the hodlbook metric collectors against a per-app ``registry``.

    A dedicated :class:`~prometheus_client.CollectorRegistry` per app avoids the
    duplicate-registration error the global default registry raises when several
    apps are constructed in one process (e.g. across tests).
    """
    return Metrics(
        registry=registry,
        http_requests_total=Counter(
            "http_requests_total",
            "Total HTTP requests.",
            ["method", "route", "status"],
            registry=registry,
        ),
        http_request_duration_seconds=Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds.",
            ["method", "route"],
            registry=registry,
        ),
        dynamodb_operations_total=Counter(
            "dynamodb_operations_total",
            "Total DynamoDB operations.",
            ["operation", "success"],
            registry=registry,
        ),
        dynamodb_operation_duration_seconds=Histogram(
            "dynamodb_operation_duration_seconds",
            "DynamoDB operation latency in seconds.",
            ["operation"],
            registry=registry,
        ),
    )


# -- rate limiting ----------------------------------------------------------
class RateLimiter:
    """A fixed-window, in-memory rate limiter keyed by an arbitrary string.

    Each key maps to ``(window_start, count)``. When a request arrives the
    limiter rolls the window forward if a minute has elapsed, then either admits
    the request (incrementing the count) or rejects it once ``limit`` is reached
    within the window. The clock is injectable so tests stay deterministic.
    """

    def __init__(self, limit: int, *, clock: Callable[[], datetime]) -> None:
        self._limit = limit
        self._clock = clock
        self._windows: dict[str, tuple[datetime, int]] = {}

    def check(self, key: str) -> bool:
        """Admit (returns ``True``) or reject (returns ``False``) a request."""
        now = self._clock()
        window_start, count = self._windows.get(key, (now, 0))
        if (now - window_start).total_seconds() >= 60:
            window_start, count = now, 0
        if count >= self._limit:
            self._windows[key] = (window_start, count)
            return False
        self._windows[key] = (window_start, count + 1)
        return True
