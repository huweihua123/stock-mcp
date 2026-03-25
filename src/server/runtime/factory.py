"""Runtime context factory."""

from __future__ import annotations

from functools import lru_cache

from src.server.auth_support import get_auth_mode
from src.server.capabilities.registry import get_builtin_capability_plugins
from src.server.core.dependencies import Container
from src.server.providers.registry import get_builtin_provider_plugins
from src.server.runtime.capability_registry import CapabilityRegistry
from src.server.runtime.lifecycle import RuntimeLifecycle
from src.server.runtime.models import RuntimeContext
from src.server.runtime.provider_registry import ProviderRegistry
from src.server.runtime.provider_selector import ProviderSelector
from src.server.runtime.proxy_policy import build_proxy_policy
from src.server.runtime.settings import get_settings


@lru_cache(maxsize=1)
def get_runtime_context() -> RuntimeContext:
    settings = get_settings()
    runtime = RuntimeContext(
        settings=settings,
        auth_mode=get_auth_mode(),
        proxy_policy=build_proxy_policy(settings),
        container=Container,
    )
    runtime.provider_runtime = Container.provider_runtime()
    runtime.symbol_resolver = Container.symbol_resolver()
    runtime.provider_health = Container.provider_health()
    runtime.market_router = Container.market_router()
    runtime.provider_facade = Container.provider_facade()
    runtime.provider_registry = ProviderRegistry(get_builtin_provider_plugins())
    runtime.capability_registry = CapabilityRegistry(get_builtin_capability_plugins())
    runtime.lifecycle = RuntimeLifecycle(runtime)
    runtime.state["provider_selector"] = ProviderSelector(runtime.provider_registry)
    return runtime


def reset_runtime_context() -> None:
    get_runtime_context.cache_clear()
