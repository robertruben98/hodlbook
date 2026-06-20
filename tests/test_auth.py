"""Auth & multi-tenancy tests for the hodlbook API and repository.

Covers the 401 paths (missing header, bad token, revoked key), the 403
cross-tenant path, the authenticated happy path, and the repository-level
issue/get/revoke/list helpers backing it all.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hodlbook.repository import Repository, _hash_token


def _assert_error_shape(body: dict[str, object], error: str) -> None:
    assert set(body) == {"error", "detail"}
    assert body["error"] == error
    assert isinstance(body["detail"], str)


# -- API: authentication (401) ----------------------------------------------
def test_no_header_is_401(api_client: TestClient) -> None:
    resp = api_client.get("/v1/prices/bitcoin")
    assert resp.status_code == 401
    _assert_error_shape(resp.json(), "AuthenticationError")


def test_bad_token_is_401(api_client: TestClient) -> None:
    resp = api_client.get("/v1/prices/bitcoin", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401
    _assert_error_shape(resp.json(), "AuthenticationError")


def test_revoked_key_is_401(api_client: TestClient, repo: Repository) -> None:
    raw, api_key = repo.issue_api_key("u1")
    repo.revoke_api_key("u1", api_key.key_id)
    resp = api_client.get("/v1/prices/bitcoin", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 401
    _assert_error_shape(resp.json(), "AuthenticationError")


# -- API: authorization (403) -----------------------------------------------
def test_cross_tenant_is_403(
    api_client: TestClient, auth_headers: dict[str, str], repo: Repository
) -> None:
    # auth_headers authenticates principal "u1"; hitting u2's portfolio -> 403.
    resp = api_client.get("/v1/portfolios/u2/p1", headers=auth_headers)
    assert resp.status_code == 403
    _assert_error_shape(resp.json(), "AuthorizationError")


def test_create_portfolio_for_other_principal_is_403(
    api_client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = api_client.post(
        "/v1/portfolios",
        json={"user_id": "u2", "portfolio_id": "p1", "cash": "1"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    _assert_error_shape(resp.json(), "AuthorizationError")


# -- API: happy path ---------------------------------------------------------
def test_x_api_key_header_authenticates(api_client: TestClient, repo: Repository) -> None:
    raw, _ = repo.issue_api_key("u1")
    resp = api_client.get("/v1/prices/bitcoin", headers={"X-API-Key": raw})
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "bitcoin"


def test_authenticated_owner_can_create_and_read(
    api_client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = api_client.post(
        "/v1/portfolios",
        json={"user_id": "u1", "portfolio_id": "p1", "cash": "500"},
        headers=auth_headers,
    )
    assert resp.status_code == 201

    resp = api_client.get("/v1/portfolios/u1/p1", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["portfolio_id"] == "p1"


# -- repository-level helpers ------------------------------------------------
def test_issue_returns_raw_and_persists_only_hash(repo: Repository) -> None:
    raw, api_key = repo.issue_api_key("u1")
    assert raw  # non-empty plaintext token returned once
    assert api_key.user_id == "u1"
    assert api_key.revoked is False
    assert api_key.key_hash == _hash_token(raw)
    assert api_key.key_hash != raw  # only the hash is stored


def test_get_api_key_by_hash_roundtrip(repo: Repository) -> None:
    raw, api_key = repo.issue_api_key("u1")
    fetched = repo.get_api_key_by_hash(_hash_token(raw))
    assert fetched is not None
    assert fetched.key_id == api_key.key_id
    assert repo.get_api_key_by_hash(_hash_token("unknown")) is None


def test_revoke_sets_revoked_flag(repo: Repository) -> None:
    raw, api_key = repo.issue_api_key("u1")
    repo.revoke_api_key("u1", api_key.key_id)
    fetched = repo.get_api_key_by_hash(_hash_token(raw))
    assert fetched is not None
    assert fetched.revoked is True


def test_revoke_unknown_key_is_noop(repo: Repository) -> None:
    repo.issue_api_key("u1")
    # No key_id "missing" exists; should not raise.
    repo.revoke_api_key("u1", "missing")


def test_list_api_keys_by_user(repo: Repository) -> None:
    _, k1 = repo.issue_api_key("u1")
    _, k2 = repo.issue_api_key("u1")
    _, other = repo.issue_api_key("u2")

    keys = repo.list_api_keys("u1")
    ids = {k.key_id for k in keys}
    assert ids == {k1.key_id, k2.key_id}
    assert other.key_id not in ids
    assert repo.list_api_keys("nobody") == []
