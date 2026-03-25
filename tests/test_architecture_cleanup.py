from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_legacy_center_classes_and_shims_removed():
    assert not Path(ROOT, "src/server/domain/adapter_manager.py").exists()
    assert not Path(ROOT, "src/server/domain/market_gateway.py").exists()
    assert not Path(ROOT, "src/server/mcp/registry.py").exists()
    assert not Path(ROOT, "src/server/core/health.py").exists()
    assert not Path(ROOT, "src/server/api/models/__init__.py").exists()


def test_main_path_no_longer_references_container_market_gateway_or_adapter_manager():
    target_roots = [
        Path(ROOT, "src/server/runtime"),
        Path(ROOT, "src/server/providers"),
        Path(ROOT, "src/server/capabilities"),
        Path(ROOT, "src/server/transports"),
    ]
    forbidden = (
        "container.market_gateway()",
        "container.adapter_manager()",
    )

    offenders: list[str] = []
    for root in target_roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path}: {marker}")

    assert offenders == []


def test_legacy_public_surfaces_removed():
    assert not Path(ROOT, "src/server/api/routes").exists()
    assert not Path(ROOT, "src/server/mcp/tools").exists()
    assert not Path(ROOT, "src/server/core/use_cases").exists()
