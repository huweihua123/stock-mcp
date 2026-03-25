from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_domain_services_only_keeps_heavy_internal_services():
    services_dir = Path(ROOT, "src/server/domain/services")
    assert sorted(path.name for path in services_dir.glob("*.py")) == [
        "filings_service.py",
        "fundamental_service.py",
        "technical_service.py",
    ]


def test_core_dependencies_no_longer_registers_lightweight_domain_services():
    text = Path(ROOT, "src/server/core/dependencies.py").read_text(encoding="utf-8")
    assert "NewsService" not in text
    assert "MoneyFlowService" not in text
    assert "news_service = providers.Factory(" not in text
    assert "money_flow_service = providers.Factory(" not in text


def test_capability_services_no_longer_depend_on_removed_domain_services():
    news_service = Path(
        ROOT, "src/server/capabilities/news/service.py"
    ).read_text(encoding="utf-8")
    money_flow_service = Path(
        ROOT, "src/server/capabilities/money_flow/service.py"
    ).read_text(encoding="utf-8")

    assert "container.news_service()" not in news_service
    assert "container.money_flow_service()" not in money_flow_service
