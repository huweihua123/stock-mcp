# tests/test_mcp_sector_overview_tools.py
"""MCP tools tests for sector-overview related tool groups."""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.mcp.tools.chunking_tools import _chunk_to_text, _fallback_markdown_chunking


def run(coro):
    return asyncio.run(coro)


def _capture_tools(mcp_mock):
    captured = {}

    def fake_tool(**_deco_kwargs):
        def decorator(fn):
            captured[fn.__name__] = fn
            return fn

        return decorator

    mcp_mock.tool = fake_tool
    return captured


class _TextBlock:
    def __init__(self, text):
        self.text = text


class _DummyFiling:
    def __init__(self, accession_no="0000320193-24-000123", form="10-K", filing_date="2024-11-01"):
        self.accession_no = accession_no
        self.form = form
        self.filing_date = filing_date

    def obj(self):
        # Trigger markdown fallback branch
        return SimpleNamespace(doc=None)

    def markdown(self):
        return "\n\n".join([f"paragraph-{i}" for i in range(12)])


class _DummyChunkedDoc:
    def as_dataframe(self):
        return pd.DataFrame(
            [
                {
                    "Text": "上游设备订单增长。",
                    "Item": "Item 1A",
                    "Empty": False,
                    "Table": False,
                    "Chars": 10,
                    "Signature": False,
                },
                {
                    "Text": "中游制造成本下降。",
                    "Item": "Item 7",
                    "Empty": False,
                    "Table": False,
                    "Chars": 10,
                    "Signature": False,
                },
            ]
        )


class _DummyFilingWithDoc(_DummyFiling):
    def obj(self):
        return SimpleNamespace(doc=_DummyChunkedDoc())


class TestFilingsTools:
    def test_fetch_periodic_sec_filings_success(self):
        from src.server.mcp.tools.filings_tools import register_filings_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        records = [
            {"form": "10-K", "filing_date": "2025-02-20"},
            {"form": "10-Q", "filing_date": "2025-05-10"},
        ]

        with patch("src.server.mcp.tools.filings_tools.filings_use_cases") as uc:
            uc.fetch_periodic_sec_filings = AsyncMock(return_value=records)
            register_filings_tools(mcp)
            result = run(
                tools["fetch_periodic_sec_filings"](
                    ticker="AAPL",
                    forms=["10-K", "10-Q"],
                    year=2025,
                    limit=5,
                )
            )

        uc.fetch_periodic_sec_filings.assert_awaited_once_with(
            ticker="AAPL",
            forms=["10-K", "10-Q"],
            year=2025,
            quarter=None,
            limit=5,
        )
        assert "AAPL SEC定期报告: 2份" in result["summary"]
        artifact = result["artifact"]
        assert artifact["component_type"] == "table"
        assert len(artifact["content"]["items"]) == 2

    def test_fetch_event_sec_filings_symbol_error(self):
        from src.server.mcp.tools.filings_tools import register_filings_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        err = SymbolResolutionError(
            code="SYMBOL_NOT_FOUND",
            message="Unknown ticker",
            raw="BAD",
        )

        with patch("src.server.mcp.tools.filings_tools.filings_use_cases") as uc:
            uc.fetch_event_sec_filings = AsyncMock(side_effect=err)
            register_filings_tools(mcp)
            result = run(tools["fetch_event_sec_filings"](ticker="BAD"))

        assert "符号解析失败" in result["summary"]
        assert result["artifact"]["content"]["error"]["code"] == "SYMBOL_NOT_FOUND"

    def test_fetch_event_sec_filings_success(self):
        from src.server.mcp.tools.filings_tools import register_filings_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        records = [{"form": "8-K", "filing_date": "2025-06-01"}]

        with patch("src.server.mcp.tools.filings_tools.filings_use_cases") as uc:
            uc.fetch_event_sec_filings = AsyncMock(return_value=records)
            register_filings_tools(mcp)
            result = run(
                tools["fetch_event_sec_filings"](
                    ticker="AAPL",
                    forms=["8-K"],
                    limit=1,
                )
            )

        assert "AAPL SEC临时报告: 1份" in result["summary"]
        assert result["artifact"]["component_type"] == "table"
        assert len(result["artifact"]["content"]["items"]) == 1

    def test_fetch_ashare_filings_success(self):
        from src.server.mcp.tools.filings_tools import register_filings_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        records = [
            {"filing_type": "annual", "ann_date": "2025-03-20"},
            {"filing_type": "quarterly", "ann_date": "2025-10-30"},
        ]

        with patch("src.server.mcp.tools.filings_tools.filings_use_cases") as uc:
            uc.fetch_ashare_filings = AsyncMock(return_value=records)
            register_filings_tools(mcp)
            result = run(
                tools["fetch_ashare_filings"](
                    symbol="SSE:600519",
                    filing_types=["annual", "quarterly"],
                    limit=2,
                )
            )

        assert "SSE:600519 A股公告: 2份" in result["summary"]
        assert result["artifact"]["component_type"] == "table"
        assert len(result["artifact"]["content"]["items"]) == 2

    def test_process_document_success(self):
        from src.server.mcp.tools.filings_tools import register_filings_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        payload = {"doc_id": "d1", "status": "success", "storage_path": "minio://x"}

        with patch("src.server.mcp.tools.filings_tools.filings_use_cases") as uc:
            uc.process_document = AsyncMock(return_value=payload)
            register_filings_tools(mcp)
            result = run(
                tools["process_document"](
                    doc_id="d1",
                    url="https://sec.gov/doc.html",
                    doc_type="10-K",
                    ticker="AAPL",
                )
            )

        assert result == payload
        uc.process_document.assert_awaited_once()

    def test_get_filing_markdown_success(self):
        from src.server.mcp.tools.filings_tools import register_filings_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        payload = {
            "status": "success",
            "cached": True,
            "content": "# 10-K Filing\n\ncontent body",
            "doc_id": "0000320193-25-000079",
            "ticker": "AAPL",
        }

        with patch("src.server.mcp.tools.filings_tools.filings_use_cases") as uc:
            uc.get_filing_markdown = AsyncMock(return_value=payload)
            register_filings_tools(mcp)
            result = run(
                tools["get_filing_markdown"](
                    ticker="AAPL",
                    doc_id="0000320193-25-000079",
                )
            )

        assert "AAPL 文档Markdown获取完成" in result["summary"]
        assert result["artifact"]["component_type"] == "filing_document"
        assert result["artifact"]["content"]["cached"] is True

    def test_get_filing_markdown_symbol_error(self):
        from src.server.mcp.tools.filings_tools import register_filings_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        err = SymbolResolutionError(
            code="SYMBOL_NOT_FOUND",
            message="Unknown ticker",
            raw="BAD",
        )
        with patch("src.server.mcp.tools.filings_tools.filings_use_cases") as uc:
            uc.get_filing_markdown = AsyncMock(side_effect=err)
            register_filings_tools(mcp)
            result = run(
                tools["get_filing_markdown"](
                    ticker="BAD",
                    doc_id="0000320193-25-000079",
                )
            )
        assert "符号解析失败" in result["summary"]
        assert result["artifact"]["content"]["error"]["code"] == "SYMBOL_NOT_FOUND"

    def test_extract_filing_key_metrics_success(self):
        from src.server.mcp.tools.filings_tools import register_filings_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        markdown_payload = {
            "status": "success",
            "cached": False,
            "content": "\n".join(
                [
                    "# Item 7",
                    "Revenue increased to 12,345 and margin reached 18%.",
                    "Net income was 2,345 with EPS 3.2.",
                ]
            ),
        }
        with patch("src.server.mcp.tools.filings_tools.filings_use_cases") as uc:
            uc.get_filing_markdown = AsyncMock(return_value=markdown_payload)
            register_filings_tools(mcp)
            result = run(
                tools["extract_filing_key_metrics"](
                    ticker="AAPL",
                    doc_id="0000320193-25-000079",
                    metric_hints=["revenue", "net income", "eps"],
                    max_items=10,
                )
            )
        items = result["artifact"]["content"]["items"]
        assert len(items) >= 2
        assert any("12,345" in " ".join(i["numbers"]) for i in items)

    def test_extract_filing_section_facts_success(self):
        from src.server.mcp.tools.filings_tools import register_filings_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        markdown_payload = {
            "status": "success",
            "cached": True,
            "content": "\n".join(
                [
                    "# Item 1A Risk Factors",
                    "Supply chain disruptions remain a risk.",
                    "# Item 7 MD&A",
                    "Management expects demand recovery.",
                ]
            ),
        }
        with patch("src.server.mcp.tools.filings_tools.filings_use_cases") as uc:
            uc.get_filing_markdown = AsyncMock(return_value=markdown_payload)
            register_filings_tools(mcp)
            result = run(
                tools["extract_filing_section_facts"](
                    ticker="AAPL",
                    doc_id="0000320193-25-000079",
                    section_hints=["item 1a", "item 7"],
                    max_quotes_per_section=3,
                )
            )
        sections = result["artifact"]["content"]["sections"]
        assert len(sections) == 2
        assert sections[0]["facts"]

    def test_build_filing_citations_success(self):
        from src.server.mcp.tools.filings_tools import register_filings_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        markdown_payload = {
            "status": "success",
            "cached": False,
            "content": "\n".join(
                [
                    "# Item 8",
                    "Revenue was 10,000 and net income 1,200.",
                ]
            ),
        }
        with patch("src.server.mcp.tools.filings_tools.filings_use_cases") as uc:
            uc.get_filing_markdown = AsyncMock(return_value=markdown_payload)
            register_filings_tools(mcp)
            result = run(
                tools["build_filing_citations"](
                    ticker="AAPL",
                    doc_id="0000320193-25-000079",
                    metric_hints=["revenue", "net income"],
                    max_items=10,
                )
            )
        citations = result["artifact"]["content"]["citations"]
        assert len(citations) >= 1
        assert citations[0]["ref_id"].startswith("0000320193-25-000079#L")


class TestChunkingTools:
    def test_chunk_to_text_with_textblocks(self):
        text = _chunk_to_text([_TextBlock("hello"), _TextBlock("world")])
        assert text == "hello\nworld"

    def test_fallback_markdown_chunking_success(self):
        filing = _DummyFiling()
        result = run(_fallback_markdown_chunking(filing, "AAPL", filing.accession_no))
        assert result["status"] == "success"
        assert result["fallback"] is True
        assert result["chunks_count"] >= 1

    def test_get_document_chunks_filing_not_found(self):
        from src.server.mcp.tools.chunking_tools import register_chunking_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        missing_filing = _DummyFiling(accession_no="0000000000-00-000001")
        company = MagicMock()
        company.get_filings.return_value.latest.return_value = [missing_filing]

        with patch("src.server.utils.sec_utils.get_company", return_value=company):
            register_chunking_tools(mcp)
            result = run(
                tools["get_document_chunks"](
                    ticker="AAPL",
                    doc_id="0000320193-24-000123",
                )
            )

        assert result["status"] == "error"
        assert "Filing not found" in result["error"]

    def test_get_document_chunks_fallback_path(self):
        from src.server.mcp.tools.chunking_tools import register_chunking_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        target_filing = _DummyFiling(accession_no="0000320193-24-000123")
        company = MagicMock()
        company.get_filings.return_value.latest.return_value = [target_filing]

        with patch("src.server.utils.sec_utils.get_company", return_value=company):
            register_chunking_tools(mcp)
            result = run(
                tools["get_document_chunks"](
                    ticker="AAPL",
                    doc_id="0000320193-24-000123",
                )
            )

        assert result["status"] == "success"
        assert result["fallback"] is True
        assert result["chunks_count"] >= 1

    def test_get_document_chunks_dataframe_path(self):
        from src.server.mcp.tools.chunking_tools import register_chunking_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        target_filing = _DummyFilingWithDoc(accession_no="0000320193-24-000123")
        company = MagicMock()
        company.get_filings.return_value.latest.return_value = [target_filing]

        with patch("src.server.utils.sec_utils.get_company", return_value=company):
            register_chunking_tools(mcp)
            result = run(
                tools["get_document_chunks"](
                    ticker="AAPL",
                    doc_id="0000320193-24-000123",
                    items=["Item 1A", "Item 7"],
                )
            )

        assert result["status"] == "success"
        assert result.get("fallback", False) is False
        assert result["chunks_count"] == 2
        assert result["chunks"][0]["metadata"]["item"] == "Item 1A"


class TestSectorResearchTools:
    def test_resolve_sector_scope_cn(self):
        from src.server.mcp.tools.sector_research_tools import register_sector_research_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        register_sector_research_tools(mcp)

        result = run(
            tools["resolve_sector_scope"](
                sector_name="新能源车",
                market="auto",
                peer_count=99,
                horizon_days=10,
            )
        )

        artifact = result["artifact"]
        scope = artifact["content"]["scope"]
        assert scope["market"] == "CN"
        assert scope["peer_count"] == 30
        assert scope["horizon_days"] == 30
        assert "get_sector_trend" in artifact["content"]["recommended_tools"]

    def test_build_sector_universe_with_symbols(self):
        from src.server.mcp.tools.sector_research_tools import register_sector_research_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        register_sector_research_tools(mcp)

        with patch(
            "src.server.mcp.tools.sector_research_tools._resolve_symbols",
            AsyncMock(return_value=["SSE:600519", "SZSE:000858"]),
        ), patch(
            "src.server.mcp.tools.sector_research_tools.market_use_cases.get_asset_info",
            AsyncMock(side_effect=[{"name": "贵州茅台"}, {"name": "五粮液"}]),
        ):
            result = run(
                tools["build_sector_universe"](
                    sector_name="白酒",
                    symbols=["600519", "000858"],
                    max_companies=10,
                )
            )

        content = result["artifact"]["content"]
        assert content["source"] == "manual"
        assert len(content["universe"]) == 2
        assert content["universe"][0]["company_name"] == "贵州茅台"

    def test_build_sector_universe_empty_returns_no_data(self):
        from src.server.mcp.tools.sector_research_tools import register_sector_research_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        register_sector_research_tools(mcp)

        with patch(
            "src.server.mcp.tools.sector_research_tools._build_cn_universe_from_tushare",
            AsyncMock(
                return_value={
                    "universe": [],
                    "source": "manual",
                    "candidates": ["商品化工(A股)", "煤化工"],
                    "note": "sector is ambiguous or not found",
                }
            ),
        ):
            result = run(
                tools["build_sector_universe"](
                    sector_name="化工",
                    market="CN",
                    max_companies=10,
                )
            )

        assert result["result_status"] == "no_data"
        assert "ambiguous sector query" in result["no_data_reason"]
        assert result["scope"]["tool"] == "build_sector_universe"
        assert result["scope"]["candidate_count"] == 2
        assert result["artifact"]["content"]["universe"] == []

    def test_build_cn_universe_accepts_ti_code_query(self):
        from src.server.mcp.tools.sector_research_tools import _build_cn_universe_from_tushare

        ths_member_fn = object()
        client = SimpleNamespace(ths_member=ths_member_fn)
        tushare_adapter = MagicMock()
        tushare_adapter.tushare_conn.get_client.return_value = client
        tushare_adapter._resolve_sector_by_code = AsyncMock(
            return_value=("877042.TI", "半导体")
        )
        tushare_adapter._resolve_sector_index = AsyncMock(
            return_value=(None, None, None)
        )
        tushare_adapter._run = AsyncMock(
            return_value=pd.DataFrame(
                [
                    {"con_code": "600519.SH", "con_name": "贵州茅台"},
                    {"con_code": "000858.SZ", "con_name": "五粮液"},
                ]
            )
        )

        manager = MagicMock()
        manager.get_adapter_by_provider.return_value = tushare_adapter

        with patch(
            "src.server.mcp.tools.sector_research_tools.Container.adapter_manager",
            return_value=manager,
        ):
            result = run(_build_cn_universe_from_tushare("877042.TI", max_companies=5))

        assert result["source"] == "tushare"
        assert result["index_code"] == "877042.TI"
        assert len(result["universe"]) == 2
        assert result["universe"][0]["ticker"] == "SSE:600519"
        tushare_adapter._resolve_sector_by_code.assert_awaited_once()

    def test_build_peer_benchmark_table_empty_symbols(self):
        from src.server.mcp.tools.sector_research_tools import register_sector_research_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        register_sector_research_tools(mcp)

        result = run(tools["build_peer_benchmark_table"](sector_name="半导体", symbols=[]))
        assert "symbols 不能为空" in result["summary"]
        assert result["artifact"]["content"]["rows"] == []

    def test_classify_value_chain_cn(self):
        from src.server.mcp.tools.sector_research_tools import register_sector_research_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        register_sector_research_tools(mcp)

        with patch(
            "src.server.mcp.tools.sector_research_tools._resolve_symbols",
            AsyncMock(return_value=["SSE:600519", "SZSE:000333"]),
        ), patch(
            "src.server.mcp.tools.sector_research_tools.market_use_cases.get_asset_info",
            AsyncMock(side_effect=[{"name": "设备材料公司"}, {"name": "终端应用公司"}]),
        ), patch(
            "src.server.mcp.tools.sector_research_tools.fundamental_use_cases.get_mainbz_info",
            AsyncMock(
                side_effect=[
                    {"rows": [{"bz_item": "上游设备材料", "bz_sales": 100}]},
                    {"rows": [{"bz_item": "终端应用服务", "bz_sales": 80}]},
                ]
            ),
        ):
            result = run(
                tools["classify_value_chain"](
                    sector_name="半导体",
                    symbols=["600519", "000333"],
                    market="CN",
                )
            )

        content = result["artifact"]["content"]
        assert len(content["rows"]) == 2
        stages = {r["ticker"]: r["value_chain_stage"] for r in content["rows"]}
        assert stages["SSE:600519"] == "上游"
        assert stages["SZSE:000333"] == "下游"
        assert content["stage_counts"]["上游"] == 1
        assert content["stage_counts"]["下游"] == 1

    def test_build_sector_evidence_pack_cn(self):
        from src.server.mcp.tools.sector_research_tools import register_sector_research_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        register_sector_research_tools(mcp)

        cn_universe = {"universe": [{"ticker": "SSE:600519"}, {"ticker": "SZSE:000858"}]}
        peer_rows = [{"ticker": "SSE:600519", "market_cap": 1000000000}]

        with patch(
            "src.server.mcp.tools.sector_research_tools._build_cn_universe_from_tushare",
            AsyncMock(return_value=cn_universe),
        ), patch(
            "src.server.mcp.tools.sector_research_tools._build_peer_rows",
            AsyncMock(return_value=peer_rows),
        ), patch(
            "src.server.mcp.tools.sector_research_tools.money_flow_use_cases.get_sector_trend",
            AsyncMock(return_value={"total_pct_chg": 2.1}),
        ), patch(
            "src.server.mcp.tools.sector_research_tools.money_flow_use_cases.get_sector_valuation_metrics",
            AsyncMock(return_value={"summary": {"valuation_level": "偏高"}}),
        ), patch(
            "src.server.mcp.tools.sector_research_tools.filings_use_cases.fetch_ashare_filings",
            AsyncMock(return_value=[{"filing_date": "2025-03-20"}]),
        ):
            result = run(
                tools["build_sector_evidence_pack"](
                    sector_name="白酒",
                    market="CN",
                    days=200,
                    include_filings=True,
                )
            )

        content = result["artifact"]["content"]
        assert content["scope"]["market"] == "CN"
        assert len(content["universe"]) == 2
        assert len(content["peer_benchmark"]) == 1
        assert len(content["filings_digest"]) == 2

    def test_build_sector_structure_snapshot_cn_score(self):
        from src.server.mcp.tools.sector_research_tools import register_sector_research_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        register_sector_research_tools(mcp)

        with patch(
            "src.server.mcp.tools.sector_research_tools.money_flow_use_cases.get_sector_trend",
            AsyncMock(return_value={"total_pct_chg": 3.2}),
        ), patch(
            "src.server.mcp.tools.sector_research_tools.money_flow_use_cases.get_sector_money_flow_history",
            AsyncMock(return_value={"summary": {"trend": "主力资金持续流入"}}),
        ), patch(
            "src.server.mcp.tools.sector_research_tools.money_flow_use_cases.get_sector_valuation_metrics",
            AsyncMock(return_value={"summary": {"valuation_level": "低估"}}),
        ):
            result = run(
                tools["build_sector_structure_snapshot"](
                    sector_name="新能源",
                    market="CN",
                    days=90,
                )
            )

        snapshot = result["artifact"]["content"]["snapshot"]
        assert snapshot["structure_score"] == 3
        assert snapshot["flow_signal"] == "主力资金持续流入"
        assert snapshot["valuation_level"] == "低估"

    def test_quality_gate_sector_report_pass(self):
        from src.server.mcp.tools.sector_research_tools import register_sector_research_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        register_sector_research_tools(mcp)

        markdown = """
# 2026-03-14 半导体行业判断
## 执行摘要
关键数值: 12 18 20 120 98 15 20。
## 主线一
龙头A收入120，龙头B收入98，份额提升3%。
## 风险与失效条件
若价格下跌15%且订单减少20%，结论失效。
## 结论与下一步
继续跟踪产能与订单。下一步重点看Q2订单变化？
"""
        evidence_pack = {
            "peer_benchmark": [{"a": 1}, {"a": 2}, {"a": 3}],
            "filings_digest": [{"ticker": "A"}],
            "universe": ["A", "B"],
        }
        result = run(
            tools["quality_gate_sector_report"](
                report_markdown=markdown,
                evidence_pack=evidence_pack,
                min_numeric_facts=3,
                require_question=True,
            )
        )
        assert result["artifact"]["content"]["pass"] is True
        assert result["artifact"]["content"]["failed_checks"] == []

    def test_quality_gate_sector_report_fail(self):
        from src.server.mcp.tools.sector_research_tools import register_sector_research_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)
        register_sector_research_tools(mcp)

        result = run(
            tools["quality_gate_sector_report"](
                report_markdown="## 执行摘要\n只有一句。",
                evidence_pack={"peer_benchmark": [], "filings_digest": [], "universe": []},
                min_numeric_facts=6,
                require_question=True,
            )
        )
        checks = result["artifact"]["content"]
        assert checks["pass"] is False
        assert "required_sections" in checks["failed_checks"]
        assert "ending_question" in checks["failed_checks"]
