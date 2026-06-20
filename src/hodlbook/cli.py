"""``hodlbook`` admin CLI (stdlib :mod:`argparse`, no extra dependency).

Provides operator subcommands for the containerized stack:

* ``create-table``  -- provision the single ``hodlbook`` DynamoDB table.
* ``issue-api-key`` -- mint an API key for a user; prints the raw token once.
* ``seed-demo``     -- create a demo portfolio plus a couple of trades.
* ``refresh-prices``-- placeholder for the price-refresh pass (no-op).

The boto3 client is built from the environment (region + optional endpoint),
mirroring how the API resolves DynamoDB Local vs. AWS. Everything is injectable
and the boto3 client builder is overridable, so the whole CLI is exercisable
under ``moto`` without touching the network.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from .repository import Repository
from .storage import build_table, create_table
from .trading import TradingEngine

# Default region used when neither --region nor the environment provides one.
DEFAULT_REGION = "us-east-1"


def build_client(*, region: str | None = None, endpoint_url: str | None = None) -> Any:
    """Build a boto3 DynamoDB client from explicit args falling back to env.

    Resolution order for each value is: explicit argument, then environment
    (``HODLBOOK_DYNAMODB_ENDPOINT`` / ``AWS_REGION`` / ``AWS_DEFAULT_REGION``),
    then a sensible default for region. Imported lazily so importing this
    module (e.g. in tests that inject their own client) never requires boto3.
    """
    import boto3

    resolved_region = (
        region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or DEFAULT_REGION
    )
    resolved_endpoint = endpoint_url or os.environ.get("HODLBOOK_DYNAMODB_ENDPOINT")
    kwargs: dict[str, Any] = {"region_name": resolved_region}
    if resolved_endpoint:
        kwargs["endpoint_url"] = resolved_endpoint
    return boto3.client("dynamodb", **kwargs)


def app_factory() -> Any:
    """Build the FastAPI app with a client resolved from the environment.

    :func:`hodlbook.api.create_app` is a factory that needs an injected boto3
    client, so it can't be served by ``uvicorn --factory`` directly. This
    no-arg wrapper bridges the gap, letting the container run::

        uvicorn --factory hodlbook.cli:app_factory
    """
    from .api import create_app

    return create_app(build_client())


def _add_client_args(parser: argparse.ArgumentParser) -> None:
    """Attach the shared --region / --endpoint-url flags to a subparser."""
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region (default: $AWS_REGION / $AWS_DEFAULT_REGION / us-east-1)",
    )
    parser.add_argument(
        "--endpoint-url",
        default=None,
        help="DynamoDB endpoint URL (default: $HODLBOOK_DYNAMODB_ENDPOINT)",
    )


def _client_from_args(args: argparse.Namespace) -> Any:
    """Return the injected client if present, else build one from args/env."""
    client = getattr(args, "client", None)
    if client is not None:
        return client
    return build_client(region=args.region, endpoint_url=args.endpoint_url)


def _cmd_create_table(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    try:
        create_table(client)
    except Exception as exc:  # pragma: no cover - defensive, e.g. already exists
        if "ResourceInUseException" in type(exc).__name__:
            print("table 'hodlbook' already exists; nothing to do")
            return 0
        raise
    print("created table 'hodlbook'")
    return 0


def _cmd_issue_api_key(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    repo = Repository(build_table(client))
    raw, api_key = repo.issue_api_key(args.user_id)
    print(f"issued API key {api_key.key_id} for user {args.user_id}")
    print(f"  token: {raw}")
    print("  store this now -- it is not recoverable (only its hash is stored)")
    return 0


def _cmd_seed_demo(args: argparse.Namespace) -> int:
    client = _client_from_args(args)
    repo = Repository(build_table(client))
    engine = TradingEngine(repo)

    repo.create_portfolio(args.user_id, args.portfolio_id, Decimal(args.cash))
    engine.buy(args.user_id, args.portfolio_id, "bitcoin", Decimal("0.5"), Decimal("50000"))
    engine.buy(args.user_id, args.portfolio_id, "ethereum", Decimal("2"), Decimal("3000"))
    print(
        f"seeded demo portfolio {args.user_id}/{args.portfolio_id} "
        f"with 2 trades (bitcoin, ethereum)"
    )
    return 0


def _cmd_refresh_prices(args: argparse.Namespace) -> int:
    print(
        "refresh-prices: not yet implemented -- the price-refresh pass is a "
        "no-op stub for now; the API refreshes prices lazily on read."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(prog="hodlbook", description="hodlbook admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-table", help="provision the hodlbook table")
    _add_client_args(p_create)
    p_create.set_defaults(func=_cmd_create_table)

    p_key = sub.add_parser("issue-api-key", help="issue an API key for a user")
    _add_client_args(p_key)
    p_key.add_argument("--user-id", required=True, help="user the key authenticates as")
    p_key.set_defaults(func=_cmd_issue_api_key)

    p_seed = sub.add_parser("seed-demo", help="seed a demo portfolio + trades")
    _add_client_args(p_seed)
    p_seed.add_argument("--user-id", default="demo", help="demo user id")
    p_seed.add_argument("--portfolio-id", default="main", help="demo portfolio id")
    p_seed.add_argument("--cash", default="100000", help="starting cash balance")
    p_seed.set_defaults(func=_cmd_seed_demo)

    p_refresh = sub.add_parser("refresh-prices", help="run price refresh (placeholder)")
    _add_client_args(p_refresh)
    p_refresh.set_defaults(func=_cmd_refresh_prices)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse ``argv`` and dispatch to the chosen subcommand."""
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace], int] = args.func
    return func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
