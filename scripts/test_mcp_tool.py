#!/usr/bin/env python3
"""Simple MCP tool test for stock-mcp via Streamable HTTP."""
import asyncio
import json
import os
from typing import Any

from fastmcp.client import Client


def _disable_proxy_for_localhost() -> None:
    # Ensure local MCP calls do not go through proxy
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        if os.environ.get(key):
            os.environ[key] = ""


async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> None:
    async with Client("http://127.0.0.1:9898/mcp") as client:
        result = await client.call_tool(name, arguments or {})
        print(f"\n=== {name} ===")
        try:
            payload = result.model_dump() if hasattr(result, "model_dump") else result
            print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
        except Exception:
            print(result)


async def main() -> None:
    _disable_proxy_for_localhost()
    await call_tool("get_financial_reports", {"symbol": "SHSE:600519"})
    await call_tool("get_social_financing", {"months": 12})


if __name__ == "__main__":
    asyncio.run(main())
