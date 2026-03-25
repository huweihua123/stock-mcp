from __future__ import annotations

import os
import sys

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from fastmcp.server.auth import AccessToken

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.server.auth_support as auth_support


class _Verifier:
    def __init__(self, token: AccessToken | None):
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token == "good-token":
            return self._token
        return None


def _make_access_token() -> AccessToken:
    return AccessToken(
        token="good-token",
        client_id="deerflow-service",
        scopes=["mcp.read"],
        claims={"iss": "https://issuer.example.com/realms/valuecell"},
    )


def _build_app():
    app = FastAPI()

    @app.get("/protected")
    async def protected(token: AccessToken = Depends(auth_support.require_service_access)):
        return {"client_id": token.client_id, "scopes": token.scopes}

    return app


def test_get_auth_mode_defaults_to_none(monkeypatch):
    monkeypatch.delenv("STOCK_MCP_AUTH_MODE", raising=False)
    monkeypatch.delenv("FASTMCP_SERVER_AUTH", raising=False)
    monkeypatch.setattr(auth_support, "_dotenv_overrides", lambda: {})
    auth_support.reset_auth_cache()

    assert auth_support.get_auth_mode() == "none"
    assert auth_support.is_auth_enabled() is False


def test_get_auth_mode_infers_jwt_for_existing_deployments(monkeypatch):
    monkeypatch.delenv("STOCK_MCP_AUTH_MODE", raising=False)
    monkeypatch.setattr(
        auth_support,
        "_dotenv_overrides",
        lambda: {"FASTMCP_SERVER_AUTH_JWT_JWKS_URI": "https://issuer.example/certs"},
    )
    auth_support.reset_auth_cache()

    assert auth_support.get_auth_mode() == "jwt"


def test_require_service_access_accepts_anonymous_when_auth_disabled(monkeypatch):
    monkeypatch.setattr(auth_support, "get_auth_mode", lambda: "none")

    app = _build_app()
    with TestClient(app) as client:
        response = client.get("/protected")

    assert response.status_code == 200
    assert response.json() == {"client_id": "anonymous", "scopes": []}


def test_require_service_access_rejects_missing_bearer_in_token_mode(monkeypatch):
    monkeypatch.setattr(auth_support, "get_auth_mode", lambda: "token")
    monkeypatch.setattr(auth_support, "get_static_bearer_provider", lambda: auth_support.StaticBearerAuthProvider("secret-token"))

    app = _build_app()
    with TestClient(app) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert response.json()["detail"] == "Bearer token required."


def test_require_service_access_rejects_invalid_bearer_in_token_mode(monkeypatch):
    monkeypatch.setattr(auth_support, "get_auth_mode", lambda: "token")
    monkeypatch.setattr(auth_support, "get_static_bearer_provider", lambda: auth_support.StaticBearerAuthProvider("secret-token"))

    app = _build_app()
    with TestClient(app) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer bad-token"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired bearer token."


def test_require_service_access_accepts_valid_bearer_in_token_mode(monkeypatch):
    monkeypatch.setattr(auth_support, "get_auth_mode", lambda: "token")
    monkeypatch.setattr(auth_support, "get_static_bearer_provider", lambda: auth_support.StaticBearerAuthProvider("secret-token"))

    app = _build_app()
    with TestClient(app) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    assert response.json() == {"client_id": "static-token-client", "scopes": []}


def test_require_service_access_rejects_missing_bearer_in_jwt_mode(monkeypatch):
    monkeypatch.setattr(auth_support, "get_auth_mode", lambda: "jwt")
    monkeypatch.setattr(auth_support, "get_jwt_verifier", lambda: _Verifier(_make_access_token()))

    app = _build_app()
    with TestClient(app) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert response.json()["detail"] == "Bearer token required."


def test_require_service_access_rejects_invalid_bearer_in_jwt_mode(monkeypatch):
    monkeypatch.setattr(auth_support, "get_auth_mode", lambda: "jwt")
    monkeypatch.setattr(auth_support, "get_jwt_verifier", lambda: _Verifier(_make_access_token()))

    app = _build_app()
    with TestClient(app) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer bad-token"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired bearer token."


def test_require_service_access_accepts_valid_bearer_in_jwt_mode(monkeypatch):
    monkeypatch.setattr(auth_support, "get_auth_mode", lambda: "jwt")
    monkeypatch.setattr(auth_support, "get_jwt_verifier", lambda: _Verifier(_make_access_token()))

    app = _build_app()
    with TestClient(app) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer good-token"})

    assert response.status_code == 200
    assert response.json() == {"client_id": "deerflow-service", "scopes": ["mcp.read"]}


def test_get_mcp_auth_provider_returns_none_when_auth_disabled(monkeypatch):
    monkeypatch.setattr(auth_support, "get_auth_mode", lambda: "none")
    assert auth_support.get_mcp_auth_provider() is None


def test_get_mcp_auth_provider_uses_static_provider_in_token_mode(monkeypatch):
    provider = auth_support.StaticBearerAuthProvider("secret-token")
    monkeypatch.setattr(auth_support, "get_auth_mode", lambda: "token")
    monkeypatch.setattr(auth_support, "get_static_bearer_provider", lambda: provider)

    assert auth_support.get_mcp_auth_provider() is provider


def test_get_mcp_auth_provider_reuses_shared_verifier_in_jwt_mode(monkeypatch):
    verifier = _Verifier(_make_access_token())
    monkeypatch.setattr(auth_support, "get_auth_mode", lambda: "jwt")
    monkeypatch.setattr(auth_support, "get_jwt_verifier", lambda: verifier)

    assert auth_support.get_mcp_auth_provider() is verifier


def test_get_static_bearer_token_requires_configuration(monkeypatch):
    monkeypatch.delenv("STOCK_MCP_STATIC_BEARER_TOKEN", raising=False)
    monkeypatch.setattr(auth_support, "_dotenv_overrides", lambda: {})
    auth_support.reset_auth_cache()

    try:
        auth_support.get_static_bearer_token()
    except RuntimeError as exc:
        assert "STOCK_MCP_STATIC_BEARER_TOKEN" in str(exc)
    else:
        raise AssertionError("Expected missing static bearer token configuration to fail")
