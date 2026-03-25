"""Built-in capability plugin registration."""

from __future__ import annotations

from src.server.capabilities.code_export.plugin import plugin as code_export_plugin
from src.server.capabilities.filings.plugin import plugin as filings_plugin
from src.server.capabilities.fundamental.plugin import plugin as fundamental_plugin
from src.server.capabilities.market.plugin import plugin as market_plugin
from src.server.capabilities.money_flow.plugin import plugin as money_flow_plugin
from src.server.capabilities.news.plugin import plugin as news_plugin
from src.server.capabilities.technical.plugin import plugin as technical_plugin
from src.server.runtime.models import CapabilityPlugin


BUILTIN_CAPABILITIES: list[CapabilityPlugin] = [
    market_plugin,
    technical_plugin,
    fundamental_plugin,
    money_flow_plugin,
    filings_plugin,
    news_plugin,
    code_export_plugin,
]


def get_builtin_capability_plugins() -> list[CapabilityPlugin]:
    return BUILTIN_CAPABILITIES
