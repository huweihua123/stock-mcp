"""Provider selection helpers."""

from __future__ import annotations


class ProviderSelector:
    def __init__(self, provider_registry):
        self._provider_registry = provider_registry

    def list_providers(self, contract: str) -> list[str]:
        return [plugin.name for plugin in self._provider_registry.get_by_contract(contract)]

    def has_provider_for(self, contract: str) -> bool:
        return bool(self._provider_registry.get_by_contract(contract))
