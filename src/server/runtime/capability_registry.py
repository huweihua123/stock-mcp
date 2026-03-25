"""Runtime registry for capability plugins."""

from __future__ import annotations

from fastmcp import FastMCP

from src.server.runtime.models import CapabilityPlugin


class CapabilityRegistry:
    def __init__(self, plugins: list[CapabilityPlugin] | None = None):
        self._plugins: dict[str, CapabilityPlugin] = {}
        for plugin in plugins or []:
            self.register(plugin)

    def register(self, plugin: CapabilityPlugin) -> None:
        self._plugins[plugin.name] = plugin

    def get(self, name: str) -> CapabilityPlugin | None:
        return self._plugins.get(name)

    def list_all(self) -> list[CapabilityPlugin]:
        return list(self._plugins.values())

    def _is_enabled(self, plugin: CapabilityPlugin, runtime=None) -> bool:
        if not plugin.enabled_by_default:
            return False
        if runtime is None or not plugin.required_contracts:
            return True
        provider_registry = getattr(runtime, "provider_registry", None)
        if provider_registry is None:
            return True
        return all(provider_registry.get_by_contract(contract) for contract in plugin.required_contracts)

    def list_enabled(self, runtime=None) -> list[CapabilityPlugin]:
        return [plugin for plugin in self._plugins.values() if self._is_enabled(plugin, runtime)]

    def build_http_routers(self, runtime) -> list:
        routers = []
        for plugin in self.list_enabled(runtime):
            for factory in plugin.http_routers:
                routers.append(factory(runtime))
        return routers

    def register_mcp_tools(self, mcp: FastMCP, runtime) -> None:
        for plugin in self.list_enabled(runtime):
            for registrar in plugin.mcp_registrars:
                registrar(mcp)

    def get_tool_group_info(self, runtime=None) -> dict[str, dict[str, str | int | bool]]:
        info: dict[str, dict[str, str | int | bool]] = {}
        for plugin in self.list_all():
            count = 0
            enabled = self._is_enabled(plugin, runtime)
            if enabled and plugin.mcp_registrars:
                probe = FastMCP(name=f"count-probe-{plugin.name}", version="0.0.0")
                for registrar in plugin.mcp_registrars:
                    registrar(probe)
                tool_manager = getattr(probe, "_tool_manager", None)
                tools = getattr(tool_manager, "_tools", {}) if tool_manager else {}
                count = len(tools) if isinstance(tools, dict) else 0
            info[plugin.name] = {
                "count": count,
                "description": plugin.description,
                "enabled": enabled,
            }
        return info

    def get_enabled_tool_count(self, runtime=None) -> int:
        return sum(meta["count"] for meta in self.get_tool_group_info(runtime).values())
