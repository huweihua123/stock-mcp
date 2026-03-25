"""Thin MCP transport built from capability plugins."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any

from fastmcp import FastMCP

from src.server.runtime import get_runtime_context
from src.server.runtime.auth import get_auth_mode, get_mcp_auth_provider
from src.server.mcp.envelope import (
    normalize_tool_exception as _normalize_tool_exception_impl,
    normalize_tool_result as _normalize_tool_result_impl,
)
from src.server.utils.logger import logger


def _normalize_tool_result(tool_name: str, result: Any) -> Any:
    return _normalize_tool_result_impl(tool_name, result)


def _normalize_tool_exception(
    tool_name: str, error: Exception, *, timeout_seconds: float | None = None
) -> Any:
    return _normalize_tool_exception_impl(
        tool_name, error, timeout_seconds=timeout_seconds
    )


def _install_tool_guard(mcp: FastMCP, tool_timeout_seconds: float) -> None:
    original_tool = mcp.tool

    def guarded_tool(*tool_args, **tool_kwargs):
        base_decorator = original_tool(*tool_args, **tool_kwargs)

        def decorator(func):
            @wraps(func)
            async def wrapped(*args, **kwargs):
                try:
                    result = await asyncio.wait_for(
                        func(*args, **kwargs), timeout=tool_timeout_seconds
                    )
                    return _normalize_tool_result(func.__name__, result)
                except asyncio.TimeoutError:
                    logger.error(
                        "Tool timed out",
                        tool_name=func.__name__,
                        timeout_seconds=tool_timeout_seconds,
                    )
                    return _normalize_tool_exception(
                        func.__name__,
                        asyncio.TimeoutError(),
                        timeout_seconds=tool_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Tool execution failed", tool_name=func.__name__)
                    return _normalize_tool_exception(func.__name__, exc)

            return base_decorator(wrapped)

        return decorator

    mcp.tool = guarded_tool


@asynccontextmanager
async def mcp_lifespan(mcp: FastMCP):
    del mcp
    runtime = get_runtime_context()
    await runtime.lifecycle.startup()
    try:
        yield
    finally:
        await runtime.lifecycle.shutdown()


def create_mcp_server() -> FastMCP:
    runtime = get_runtime_context()
    auth_provider = get_mcp_auth_provider()
    auth_mode = get_auth_mode()
    logger.info("MCP auth mode: %s", auth_mode)

    mcp = FastMCP(
        name="stock-tool-mcp",
        version="2.0.0",
        lifespan=mcp_lifespan,
        auth=auth_provider,
    )
    _install_tool_guard(mcp, runtime.settings.timeout.mcp_tool_seconds)
    runtime.capability_registry.register_mcp_tools(mcp, runtime)
    logger.info("✅ MCP server created from capability registry")
    return mcp


def create_filtered_mcp_server(
    include_tags: set[str] | None = None,
    exclude_tags: set[str] | None = None,
    name: str | None = None,
) -> FastMCP:
    runtime = get_runtime_context()
    auth_provider = get_mcp_auth_provider()
    enabled = []
    for plugin in runtime.capability_registry.list_enabled(runtime):
        if include_tags and plugin.name not in include_tags:
            continue
        if exclude_tags and plugin.name in exclude_tags:
            continue
        enabled.append(plugin)

    mcp = FastMCP(
        name=name or "stock-tool-mcp-filtered",
        version="2.0.0",
        lifespan=mcp_lifespan,
        auth=auth_provider,
    )
    _install_tool_guard(mcp, runtime.settings.timeout.mcp_tool_seconds)
    for plugin in enabled:
        for registrar in plugin.mcp_registrars:
            registrar(mcp)
    return mcp


def get_tool_group_info() -> dict[str, dict[str, str | int | bool]]:
    runtime = get_runtime_context()
    return runtime.capability_registry.get_tool_group_info(runtime)


def get_enabled_tool_count() -> int:
    runtime = get_runtime_context()
    return runtime.capability_registry.get_enabled_tool_count(runtime)


def get_server_info() -> dict:
    return {
        "name": "Stock Tool MCP",
        "version": "2.0.0",
        "total_tools": get_enabled_tool_count(),
        "tags": get_tool_group_info(),
        "endpoint": "http://localhost:9898/mcp",
    }


def get_all_tags() -> set[str]:
    return set(get_tool_group_info().keys())


def get_tools_by_tag(tag: str) -> list[str]:
    runtime = get_runtime_context()
    plugin = runtime.capability_registry.get(tag)
    if plugin is None or plugin not in runtime.capability_registry.list_enabled(runtime):
        return []
    probe = FastMCP(name=f"tool-list-{tag}", version="0.0.0")
    for registrar in plugin.mcp_registrars:
        registrar(probe)
    tool_manager = getattr(probe, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", {}) if tool_manager else {}
    if isinstance(tools, dict):
        return list(tools.keys())
    return []
