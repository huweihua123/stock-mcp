"""Proxy helpers for per-adapter routing.

Keep domestic providers direct by default and only enable proxying for
providers that explicitly opt in.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional


_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def build_proxy_url(enabled: bool, host: str, port: int) -> Optional[str]:
    if not enabled or not host or not port:
        return None
    return f"http://{host}:{port}"


def disable_global_proxy_env() -> None:
    """Remove process-level proxy env so domestic providers stay direct."""
    for key in _PROXY_ENV_KEYS:
        os.environ.pop(key, None)


@contextmanager
def temporary_proxy_env(proxy_url: Optional[str]) -> Iterator[None]:
    """Temporarily expose proxy env for libraries that only honor env vars."""
    if not proxy_url:
        yield
        return

    old_values = {key: os.environ.get(key) for key in _PROXY_ENV_KEYS}
    try:
        for key in _PROXY_ENV_KEYS:
            os.environ[key] = proxy_url
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
