from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.config.settings import MCPConfig


def test_mcp_port_defaults_to_9898() -> None:
    assert MCPConfig.model_fields["port"].default == 9898


def test_mcp_host_defaults_to_localhost() -> None:
    assert MCPConfig.model_fields["host"].default == "127.0.0.1"
