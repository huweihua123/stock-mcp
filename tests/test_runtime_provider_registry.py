from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.runtime.models import ProviderPlugin
from src.server.runtime.provider_registry import ProviderRegistry


def test_provider_registry_filters_disabled_plugins_from_contract_lookup():
    enabled = ProviderPlugin(
        name="enabled-provider",
        description="enabled",
        contracts=frozenset({"historical_price"}),
    )
    disabled = ProviderPlugin(
        name="disabled-provider",
        description="disabled",
        contracts=frozenset({"historical_price"}),
        enabled_by_default=False,
    )

    registry = ProviderRegistry([enabled, disabled])

    assert registry.get("enabled-provider") is enabled
    assert registry.get("disabled-provider") is disabled
    assert registry.list_enabled() == [enabled]
    assert registry.get_by_contract("historical_price") == [enabled]


def test_provider_registry_keeps_other_contracts_independent():
    price = ProviderPlugin(
        name="price-provider",
        description="price",
        contracts=frozenset({"historical_price"}),
    )
    filings = ProviderPlugin(
        name="filings-provider",
        description="filings",
        contracts=frozenset({"filings"}),
    )

    registry = ProviderRegistry([price, filings])

    assert registry.get_by_contract("historical_price") == [price]
    assert registry.get_by_contract("filings") == [filings]
    assert registry.get_by_contract("missing") == []
