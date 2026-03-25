from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.app import app, create_app
from src.server.transports.http.app import create_http_app


def test_app_entrypoint_keeps_create_app_alias():
    assert create_app is create_http_app
    assert app.title == "Stock MCP"


def test_app_exposes_capability_route_groups():
    paths = {route.path for route in app.routes}
    assert "/api/v1/market/prices/batch" in paths
    assert "/api/v1/technical/indicators/calculate" in paths
    assert "/api/v1/fundamental/report" in paths
    assert "/api/v1/money-flow/stock/{symbol}" in paths
    assert "/api/v1/filings/sec/periodic" in paths
    assert "/api/v1/filings/chunks" in paths
    assert "/api/v1/news/search" in paths
    assert "/api/v1/code-export/tushare/csv" in paths
