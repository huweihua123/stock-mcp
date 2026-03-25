"""Runtime auth facade."""

from __future__ import annotations

from fastapi import Depends

from src.server.auth_support import (
    get_auth_mode,
    get_mcp_auth_provider,
    is_auth_enabled,
    require_service_access,
)


def get_protected_dependencies():
    return [Depends(require_service_access)] if is_auth_enabled() else []


__all__ = [
    "get_auth_mode",
    "get_mcp_auth_provider",
    "get_protected_dependencies",
    "is_auth_enabled",
    "require_service_access",
]
