"""Tests for env-driven configuration (:mod:`hodlbook.settings`)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from hodlbook.api import create_app
from hodlbook.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Drop the cached singleton before each test so env edits take effect."""
    get_settings.cache_clear()


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any HODLBOOK_* vars so defaults are observable in isolation."""
    import os

    for key in list(os.environ):
        if key.startswith("HODLBOOK_"):
            monkeypatch.delenv(key, raising=False)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    settings = Settings()
    assert settings.table_name == "hodlbook"
    assert settings.aws_region == "us-east-1"
    assert settings.dynamodb_endpoint is None
    assert settings.price_provider == "mock"
    assert settings.price_ttl_seconds == 60
    assert settings.default_starting_cash == Decimal("100000")
    assert settings.log_level == "INFO"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("HODLBOOK_TABLE_NAME", "hodlbook-prod")
    monkeypatch.setenv("HODLBOOK_AWS_REGION", "eu-west-1")
    monkeypatch.setenv("HODLBOOK_DYNAMODB_ENDPOINT", "http://localhost:8000")
    monkeypatch.setenv("HODLBOOK_PRICE_PROVIDER", "http")
    monkeypatch.setenv("HODLBOOK_PRICE_TTL_SECONDS", "120")
    monkeypatch.setenv("HODLBOOK_DEFAULT_STARTING_CASH", "50000.50")
    monkeypatch.setenv("HODLBOOK_LOG_LEVEL", "DEBUG")

    settings = get_settings()

    assert settings.table_name == "hodlbook-prod"
    assert settings.aws_region == "eu-west-1"
    assert settings.dynamodb_endpoint == "http://localhost:8000"
    assert settings.price_provider == "http"
    assert settings.price_ttl_seconds == 120
    assert settings.default_starting_cash == Decimal("50000.50")
    assert settings.log_level == "DEBUG"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_create_app_honors_passed_settings() -> None:
    settings = Settings(price_ttl_seconds=999)
    app = create_app(client=None, settings=settings)

    assert app.state.settings is settings
    assert app.state.cache.ttl_seconds == 999


def test_create_app_defaults_to_get_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("HODLBOOK_PRICE_TTL_SECONDS", "42")
    get_settings.cache_clear()

    app = create_app(client=None)

    assert app.state.cache.ttl_seconds == 42
