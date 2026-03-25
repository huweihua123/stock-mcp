from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from fastapi import APIRouter
from fastmcp import FastMCP

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.runtime.capability_registry import CapabilityRegistry
from src.server.runtime.models import CapabilityPlugin, ProviderPlugin
from src.server.runtime.provider_registry import ProviderRegistry


def _router_factory(_runtime):
    router = APIRouter()

    @router.get("/dummy")
    async def dummy():
        return {"ok": True}

    return router


def _tool_registrar(name: str):
    def registrar(mcp: FastMCP) -> None:
        @mcp.tool(name=name)
        def _tool() -> str:
            return name

    return registrar


def _runtime_with_contracts(*contracts: str):
    plugins = [
        ProviderPlugin(
            name=f"provider-{index}",
            description=contract,
            contracts=frozenset({contract}),
        )
        for index, contract in enumerate(contracts, start=1)
    ]
    return SimpleNamespace(provider_registry=ProviderRegistry(plugins))


def test_capability_registry_hides_capabilities_without_required_contracts():
    registry = CapabilityRegistry(
        [
            CapabilityPlugin(
                name="market",
                description="market",
                required_contracts=frozenset({"historical_price"}),
                http_routers=(_router_factory,),
                mcp_registrars=(_tool_registrar("market_tool"),),
            ),
            CapabilityPlugin(
                name="news",
                description="news",
                required_contracts=frozenset({"news_search"}),
                http_routers=(_router_factory,),
                mcp_registrars=(_tool_registrar("news_tool"),),
            ),
        ]
    )

    runtime = _runtime_with_contracts("historical_price")

    enabled = registry.list_enabled(runtime)
    assert [plugin.name for plugin in enabled] == ["market"]
    assert len(registry.build_http_routers(runtime)) == 1

    mcp = FastMCP(name="test", version="0.0.0")
    registry.register_mcp_tools(mcp, runtime)
    assert list(getattr(getattr(mcp, "_tool_manager", None), "_tools", {}).keys()) == ["market_tool"]


def test_capability_registry_reports_tool_counts_with_runtime_filtering():
    registry = CapabilityRegistry(
        [
            CapabilityPlugin(
                name="market",
                description="market",
                required_contracts=frozenset({"historical_price"}),
                mcp_registrars=(_tool_registrar("market_tool"),),
            ),
            CapabilityPlugin(
                name="news",
                description="news",
                required_contracts=frozenset({"news_search"}),
                mcp_registrars=(_tool_registrar("news_tool"),),
            ),
        ]
    )

    runtime = _runtime_with_contracts("historical_price")
    info = registry.get_tool_group_info(runtime)

    assert info["market"]["enabled"] is True
    assert info["market"]["count"] == 1
    assert info["news"]["enabled"] is False
    assert info["news"]["count"] == 0
    assert registry.get_enabled_tool_count(runtime) == 1


def test_capability_registry_without_runtime_keeps_default_enablement():
    registry = CapabilityRegistry(
        [
            CapabilityPlugin(
                name="news",
                description="news",
                required_contracts=frozenset({"news_search"}),
            )
        ]
    )

    enabled = registry.list_enabled()
    assert [plugin.name for plugin in enabled] == ["news"]
