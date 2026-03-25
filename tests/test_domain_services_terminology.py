from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_domain_services_no_legacy_center_terms():
    services_dir = Path(ROOT, "src/server/domain/services")
    forbidden = (
        "adapter_manager",
        "AdapterManager",
        "MarketGateway",
        "market_gateway",
        "ticker-scoped synthesized methods",
        "通过 AdapterManager 获取数据",
        "适配器管理器",
    )

    offenders: list[str] = []
    for path in services_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.name}: {marker}")

    assert offenders == []


def test_readme_describes_domain_services_as_runtime_backing_layer():
    readme = Path(ROOT, "README.md").read_text(encoding="utf-8")
    assert "runtime/provider facade surface" in readme
