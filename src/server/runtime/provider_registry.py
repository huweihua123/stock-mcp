"""Runtime registry for provider plugins."""

from __future__ import annotations

from collections import defaultdict

from src.server.runtime.models import ProviderPlugin


class ProviderRegistry:
    def __init__(self, plugins: list[ProviderPlugin] | None = None):
        self._plugins: dict[str, ProviderPlugin] = {}
        self._by_contract: dict[str, list[ProviderPlugin]] = defaultdict(list)
        for plugin in plugins or []:
            self.register(plugin)

    def register(self, plugin: ProviderPlugin) -> None:
        self._plugins[plugin.name] = plugin
        for contract in plugin.contracts:
            self._by_contract[contract].append(plugin)

    def get(self, name: str) -> ProviderPlugin | None:
        return self._plugins.get(name)

    def list_all(self) -> list[ProviderPlugin]:
        return list(self._plugins.values())

    def list_enabled(self) -> list[ProviderPlugin]:
        return [plugin for plugin in self._plugins.values() if plugin.enabled_by_default]

    def get_by_contract(self, contract: str) -> list[ProviderPlugin]:
        return [
            plugin for plugin in self._by_contract.get(contract, []) if plugin.enabled_by_default
        ]
