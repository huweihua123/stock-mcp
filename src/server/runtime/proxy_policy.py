"""Proxy policy for provider plugins."""

from __future__ import annotations

from dataclasses import dataclass

from src.server.utils.proxy_utils import build_proxy_url


FOREIGN_PROVIDER_NAMES = frozenset(
    {
        "yahoo",
        "finnhub",
        "futures",
        "alpha_vantage",
        "twelve_data",
        "fred",
        "ccxt",
        "crypto",
        "edgar",
    }
)

DOMESTIC_PROVIDER_NAMES = frozenset({"tushare", "akshare", "baostock"})


@dataclass(frozen=True)
class ProxyPolicy:
    enabled: bool
    host: str
    port: int

    @property
    def proxy_url(self) -> str | None:
        return build_proxy_url(self.enabled, self.host, self.port)

    def should_proxy(self, provider_name: str) -> bool:
        if provider_name in DOMESTIC_PROVIDER_NAMES:
            return False
        if provider_name in FOREIGN_PROVIDER_NAMES:
            return self.enabled
        return self.enabled


def build_proxy_policy(settings) -> ProxyPolicy:
    return ProxyPolicy(
        enabled=settings.proxy.enabled,
        host=settings.proxy.host,
        port=settings.proxy.port,
    )
