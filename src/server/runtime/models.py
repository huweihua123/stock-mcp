"""Runtime plugin models for stock-mcp.

These dataclasses are the stable contracts between:
- runtime substrate
- provider plugins
- capability plugins
- transports (HTTP / MCP)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import APIRouter
from fastmcp import FastMCP


RuntimeHook = Callable[["RuntimeContext"], Awaitable[bool | None]]
RouterFactory = Callable[["RuntimeContext"], APIRouter]
McpRegistrar = Callable[[FastMCP], None]


@dataclass(frozen=True)
class ProviderPlugin:
    """A runtime-loadable provider plugin."""

    name: str
    description: str
    contracts: frozenset[str]
    enabled_by_default: bool = True
    startup: RuntimeHook | None = None
    shutdown: RuntimeHook | None = None


@dataclass(frozen=True)
class CapabilityPlugin:
    """A runtime-loadable capability plugin."""

    name: str
    description: str
    enabled_by_default: bool = True
    required_contracts: frozenset[str] = frozenset()
    http_routers: tuple[RouterFactory, ...] = ()
    mcp_registrars: tuple[McpRegistrar, ...] = ()


@dataclass
class RuntimeContext:
    """Shared runtime context passed to registries, transports and plugins."""

    settings: Any
    auth_mode: str
    proxy_policy: Any
    container: Any
    provider_runtime: Any | None = None
    provider_facade: Any | None = None
    symbol_resolver: Any | None = None
    market_router: Any | None = None
    provider_health: Any | None = None
    provider_registry: Any | None = None
    capability_registry: Any | None = None
    lifecycle: Any | None = None
    state: dict[str, Any] = field(default_factory=dict)
