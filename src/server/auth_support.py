"""Shared authentication helpers for stock-mcp HTTP and MCP surfaces."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from dotenv import dotenv_values
from fastmcp.server.auth import AccessToken, AuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier

_JWT_PROVIDER_PATH = "fastmcp.server.auth.providers.jwt.JWTVerifier"
_http_bearer = HTTPBearer(auto_error=False)
_AUTH_MODE_NONE = "none"
_AUTH_MODE_TOKEN = "token"
_AUTH_MODE_JWT = "jwt"
AuthMode = Literal["none", "token", "jwt"]


@lru_cache(maxsize=1)
def _dotenv_overrides() -> dict[str, str]:
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]
    for path in candidates:
        if path.exists():
            return {str(k): str(v) for k, v in dotenv_values(path).items() if k and v is not None}
    return {}


def _get_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is not None:
        return value
    return _dotenv_overrides().get(name)


def _jwt_config_present() -> bool:
    return bool(
        _get_env("FASTMCP_SERVER_AUTH_JWT_JWKS_URI")
        or _get_env("FASTMCP_SERVER_AUTH_JWT_PUBLIC_KEY")
    )


def _token_config_present() -> bool:
    return bool(_get_env("STOCK_MCP_STATIC_BEARER_TOKEN"))


def get_auth_mode() -> AuthMode:
    configured_mode = str(_get_env("STOCK_MCP_AUTH_MODE") or "").strip().lower()
    if configured_mode:
        if configured_mode not in {_AUTH_MODE_NONE, _AUTH_MODE_TOKEN, _AUTH_MODE_JWT}:
            raise RuntimeError(
                "Invalid STOCK_MCP_AUTH_MODE. Expected one of: none, token, jwt."
            )
        return configured_mode  # type: ignore[return-value]

    # Backward compatibility for existing deployments that already configured JWT auth.
    configured_provider = str(_get_env("FASTMCP_SERVER_AUTH") or "").strip()
    if configured_provider or _jwt_config_present():
        return _AUTH_MODE_JWT
    if _token_config_present():
        return _AUTH_MODE_TOKEN
    return _AUTH_MODE_NONE


def is_auth_enabled() -> bool:
    return get_auth_mode() != _AUTH_MODE_NONE


def _validate_auth_provider() -> None:
    configured_provider = str(_get_env("FASTMCP_SERVER_AUTH") or "").strip()
    if get_auth_mode() != _AUTH_MODE_JWT:
        return
    if configured_provider and configured_provider != _JWT_PROVIDER_PATH:
        raise RuntimeError(
            "stock-mcp only supports FastMCP JWTVerifier for service authentication. "
            f"Received FASTMCP_SERVER_AUTH={configured_provider!r}."
        )


@lru_cache(maxsize=1)
def get_jwt_verifier() -> JWTVerifier:
    _validate_auth_provider()
    return JWTVerifier()


class StaticBearerAuthProvider(AuthProvider):
    """Minimal static bearer auth provider for local/self-hosted deployments."""

    def __init__(self, token: str):
        super().__init__()
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token != self._token:
            return None
        return AccessToken(
            token=token,
            client_id="static-token-client",
            scopes=[],
            claims={"auth_mode": _AUTH_MODE_TOKEN},
        )


@lru_cache(maxsize=1)
def get_static_bearer_token() -> str:
    token = str(_get_env("STOCK_MCP_STATIC_BEARER_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "STOCK_MCP_STATIC_BEARER_TOKEN must be configured when STOCK_MCP_AUTH_MODE=token."
        )
    return token


@lru_cache(maxsize=1)
def get_static_bearer_provider() -> StaticBearerAuthProvider:
    return StaticBearerAuthProvider(get_static_bearer_token())


def get_mcp_auth_provider() -> AuthProvider | None:
    mode = get_auth_mode()
    if mode == _AUTH_MODE_NONE:
        return None
    if mode == _AUTH_MODE_TOKEN:
        return get_static_bearer_provider()
    return get_jwt_verifier()


async def require_service_access(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
) -> AccessToken:
    mode = get_auth_mode()
    if mode == _AUTH_MODE_NONE:
        return AccessToken(
            token="anonymous",
            client_id="anonymous",
            scopes=[],
            claims={"auth_mode": _AUTH_MODE_NONE},
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        verifier = (
            get_static_bearer_provider()
            if mode == _AUTH_MODE_TOKEN
            else get_jwt_verifier()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Authentication is misconfigured: {exc}",
        ) from exc

    token = await verifier.verify_token(credentials.credentials)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


def reset_auth_cache() -> None:
    get_jwt_verifier.cache_clear()
    get_static_bearer_provider.cache_clear()
    get_static_bearer_token.cache_clear()
    cache_clear = getattr(_dotenv_overrides, "cache_clear", None)
    if callable(cache_clear):
        cache_clear()
