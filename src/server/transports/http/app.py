"""Thin HTTP transport built from capability plugins."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute

from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.runtime import get_runtime_context
from src.server.runtime.auth import get_protected_dependencies
from src.server.runtime.health import build_health_router
from src.server.transports.mcp.server import create_mcp_server, get_enabled_tool_count
from src.server.utils.logger import logger


def create_http_app() -> FastAPI:
    runtime = get_runtime_context()
    mcp_server = create_mcp_server()
    mcp_app = mcp_server.http_app(path="/", transport="streamable-http")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        logger.info("🚀 Starting stock-mcp HTTP transport")
        await runtime.lifecycle.startup()
        async with mcp_app.router.lifespan_context(mcp_app):
            yield
        await runtime.lifecycle.shutdown()

    app = FastAPI(
        title="Stock MCP",
        description=(
            "Financial data service with a plugin-based runtime, thin HTTP transport, "
            "and MCP exposure for agent clients."
        ),
        version="2.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    protected_dependencies = get_protected_dependencies()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(SymbolResolutionError)
    async def symbol_resolution_exception_handler(_, exc: SymbolResolutionError):
        return JSONResponse(
            status_code=400,
            content={"error": exc.to_dict()},
        )

    app.include_router(build_health_router(runtime), tags=["Health"])
    for router in runtime.capability_registry.build_http_routers(runtime):
        app.include_router(router, dependencies=protected_dependencies)

    app.mount("/mcp", mcp_app)

    def _build_openapi_schema() -> dict:
        routes = [
            route
            for route in app.routes
            if not isinstance(route, APIRoute) or route.path != "/openapi.json"
        ]
        return get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=routes,
        )

    @app.get("/openapi.json", include_in_schema=False, dependencies=protected_dependencies)
    async def openapi_schema():
        return JSONResponse(_build_openapi_schema())

    @app.get("/docs", include_in_schema=False, dependencies=protected_dependencies, response_class=HTMLResponse)
    async def swagger_ui():
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")

    @app.get("/redoc", include_in_schema=False, dependencies=protected_dependencies, response_class=HTMLResponse)
    async def redoc_ui():
        return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")

    @app.get("/", tags=["Root"], dependencies=protected_dependencies)
    async def root():
        return {
            "service": "Stock MCP",
            "version": "2.0.0",
            "description": "Plugin-based financial data runtime with HTTP and MCP transports",
            "protocols": {
                "http": {
                    "base_url": "/api/v1",
                    "documentation": {
                        "swagger_ui": "/docs",
                        "redoc": "/redoc",
                        "openapi_json": "/openapi.json",
                    },
                },
                "mcp": {
                    "endpoint": "/mcp",
                    "protocol": "Streamable HTTP (JSON-RPC 2.0)",
                    "tools_count": get_enabled_tool_count(),
                },
            },
            "health_check": "/health",
            "capabilities": [plugin.name for plugin in runtime.capability_registry.list_enabled(runtime)],
        }

    return app
