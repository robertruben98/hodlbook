"""Centralized, env-driven configuration for hodlbook.

All runtime configuration lives here as a single :class:`Settings` model
(backed by ``pydantic-settings``) rather than being scattered as hardcoded
constants across modules. Every field is overridable via a ``HODLBOOK_``-
prefixed environment variable (or a ``.env`` file at the working directory),
with sensible defaults so the app boots with zero configuration.

Use :func:`get_settings` for the process-wide, lazily-built singleton; it is
``lru_cache``-d so env parsing happens once. Tests that mutate the environment
should call ``get_settings.cache_clear()`` to force a re-read.

Money fields are :class:`~decimal.Decimal`; floats never touch money math.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration, populated from ``HODLBOOK_*`` env vars / ``.env``.

    Fields:
        table_name: DynamoDB single-table name.
        aws_region: AWS region for the DynamoDB client.
        dynamodb_endpoint: Optional endpoint override (e.g. DynamoDB Local).
        price_provider: Price source -- ``"mock"`` or ``"http"``.
        price_ttl_seconds: Freshness window for cached price ticks.
        default_starting_cash: Default cash balance for new portfolios.
        log_level: Root log level (e.g. ``"INFO"``, ``"DEBUG"``).
        rate_limit_per_minute: Max requests per principal per fixed minute window.
        metrics_enabled: Whether to record/serve Prometheus metrics.
    """

    model_config = SettingsConfigDict(env_prefix="HODLBOOK_", env_file=".env")

    table_name: str = "hodlbook"
    aws_region: str = "us-east-1"
    dynamodb_endpoint: str | None = None
    price_provider: str = "mock"
    price_ttl_seconds: int = 60
    default_starting_cash: Decimal = Decimal("100000")
    log_level: str = "INFO"
    rate_limit_per_minute: int = 120
    metrics_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings`, parsed once and cached.

    Call ``get_settings.cache_clear()`` after mutating the environment (e.g. in
    tests) to force a fresh read.
    """
    return Settings()
