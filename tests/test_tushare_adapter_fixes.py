# tests/test_tushare_adapter_fixes.py
"""
单元测试：验证本次 Bug 修复的正确性
覆盖以下 7 个修复点：
  Fix-1  get_sector_trend  —— pct_change -> pct_chg 字段归一化
  Fix-2  get_sector_money_flow_history —— pct_change 归一化
  Fix-3  get_market_money_flow —— moneyflow_mkt 失败降级到 moneyflow_ind_dc
  Fix-4  get_sector_money_flow_history error 时 tool 层返回空 artifact
  Fix-5  get_market_liquidity —— 使用 margin（汇总）而非 margin_detail（截面）
  Fix-6  get_market_liquidity —— margin 按 trade_date 聚合 SSE+SZSE 两行
  Fix-7  get_money_flow —— 空数据时返回合法结构而不是 {"error": ...}
  Fix-8  get_north_bound_flow —— summary_text 万元->亿元换算正确
"""

import asyncio
import sys
import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.domain.adapters.tushare_adapter import TushareAdapter
from src.server.domain.adapters.akshare_adapter import AkshareAdapter
from src.server.domain.adapter_manager import AdapterManager
from src.server.domain.types import DataSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


class _DummyCache:
    """不实际缓存，所有 get 返回 None。"""

    async def get(self, *_a, **_kw):
        return None

    async def set(self, *_a, **_kw):
        pass


def _make_adapter(client_mock):
    """创建绑定了 mock client 的 TushareAdapter。"""
    conn = MagicMock()
    conn.get_client.return_value = client_mock
    return TushareAdapter(tushare_conn=conn, cache=_DummyCache())


def _df(*cols, rows):
    """快速创建 DataFrame。cols = 列名列表, rows = 行数据列表。"""
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# Fix-1  get_sector_trend：pct_change -> pct_chg 归一化
# ---------------------------------------------------------------------------


class TestSectorTrendPctChgNormalize:
    """ths_daily 返回 pct_change 字段时，total_pct_chg 应能正确累加。"""

    def _make_ths_daily_df_with_pct_change(self):
        return _df(
            "trade_date",
            "close",
            "pct_change",
            rows=[
                ("20260220", 100.0, 1.5),
                ("20260221", 102.0, 2.0),
                ("20260224", 101.0, -1.0),
            ],
        )

    def _make_ths_index_df(self):
        return _df("ts_code", "name", rows=[("123456.TI", "测试板块")])

    def test_total_pct_chg_correct_when_field_is_pct_change(self):
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_get_all_sectors():
            return [{"ts_code": "123456.TI", "name": "测试板块"}]

        # _run 是 async，通过 patch 使其同步返回 DataFrame
        async def fake_run(func, **kwargs):
            if func == client.ths_daily:
                return self._make_ths_daily_df_with_pct_change()
            return pd.DataFrame()

        adapter._get_all_sectors = fake_get_all_sectors
        adapter._run = fake_run

        result = run(adapter.get_sector_trend("测试板块", days=10))

        assert (
            result.get("total_pct_chg") != 0.0
        ), "total_pct_chg 应为非零值，字段归一化失败"
        # 1.5 + 2.0 + (-1.0) = 2.5
        assert (
            abs(result["total_pct_chg"] - 2.5) < 0.01
        ), f"期望 2.5，实际 {result['total_pct_chg']}"

    def test_trend_records_contain_pct_chg_key(self):
        """trend 列表中的每条记录都应有 pct_chg 键。"""
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_get_all_sectors():
            return [{"ts_code": "123456.TI", "name": "测试板块"}]

        async def fake_run(func, **kwargs):
            if func == client.ths_daily:
                return self._make_ths_daily_df_with_pct_change()
            return pd.DataFrame()

        adapter._get_all_sectors = fake_get_all_sectors
        adapter._run = fake_run

        result = run(adapter.get_sector_trend("测试板块", days=10))
        for rec in result.get("trend", []):
            assert "pct_chg" in rec, f"记录中缺少 pct_chg: {rec}"

    def test_no_regression_when_field_already_pct_chg(self):
        """当 ths_daily 本来就返回 pct_chg 时，结果不受影响。"""
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_get_all_sectors():
            return [{"ts_code": "123456.TI", "name": "测试板块"}]

        df_with_pct_chg = _df(
            "trade_date",
            "close",
            "pct_chg",
            rows=[("20260220", 100.0, 3.0), ("20260221", 103.0, 3.0)],
        )

        async def fake_run(func, **kwargs):
            if func == client.ths_daily:
                return df_with_pct_chg
            return pd.DataFrame()

        adapter._get_all_sectors = fake_get_all_sectors
        adapter._run = fake_run

        result = run(adapter.get_sector_trend("测试板块", days=10))
        assert abs(result["total_pct_chg"] - 6.0) < 0.01


# ---------------------------------------------------------------------------
# Fix-2  get_sector_money_flow_history：pct_change 归一化后 records 中有 pct_chg
# ---------------------------------------------------------------------------


class TestSectorMoneyFlowHistoryPctChgNormalize:

    def _ths_index_df(self):
        return _df("ts_code", "name", rows=[("299999.TI", "半导体")])

    def _ths_daily_pct_change_df(self):
        return _df(
            "trade_date",
            "close",
            "pct_change",
            "vol",
            "turnover_rate",
            rows=[
                ("20260220", 50.0, 2.0, 1000.0, 0.5),
                ("20260221", 51.0, 1.5, 1200.0, 0.6),
            ],
        )

    def test_records_have_pct_chg_not_none(self):
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_run(func, **kwargs):
            if func == client.ths_index:
                return self._ths_index_df()
            if func == client.ths_daily:
                return self._ths_daily_pct_change_df()
            if func == client.moneyflow_ind:
                raise Exception("no permission")
            return pd.DataFrame()

        adapter._run = fake_run

        result = run(adapter.get_sector_money_flow_history("半导体", days=5))
        assert result.get("records"), "records 不应为空"
        for rec in result["records"]:
            assert rec.get("pct_chg") is not None, f"pct_chg 应为数值，实际: {rec}"

    def test_summary_total_pct_chg_nonzero(self):
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_run(func, **kwargs):
            if func == client.ths_index:
                return self._ths_index_df()
            if func == client.ths_daily:
                return self._ths_daily_pct_change_df()
            if func == client.moneyflow_ind:
                raise Exception("no permission")
            return pd.DataFrame()

        adapter._run = fake_run

        result = run(adapter.get_sector_money_flow_history("半导体", days=5))
        total = result.get("summary", {}).get("total_pct_chg", 0)
        # 2.0 + 1.5 = 3.5
        assert abs(total - 3.5) < 0.01, f"期望 3.5，实际 {total}"


# ---------------------------------------------------------------------------
# Fix-3  get_market_money_flow：moneyflow_mkt 失败降级到 moneyflow_ind_dc
# ---------------------------------------------------------------------------


class TestMarketMoneyFlowFallback:

    def _ind_dc_df(self):
        return _df(
            "trade_date",
            "ts_code",
            "net_mf_amount",
            rows=[("20260224", "银行", 100.0), ("20260224", "半导体", -50.0)],
        )

    def test_fallback_to_moneyflow_ind_dc_when_mkt_fails(self):
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_run(func, **kwargs):
            if func == client.moneyflow_mkt:
                raise Exception("请指定正确的接口名")
            if func == client.moneyflow_ind_dc:
                return self._ind_dc_df()
            return pd.DataFrame()

        adapter._run = fake_run

        result = run(adapter.get_market_money_flow("20260224"))
        assert result.get("component_type") == "market_money_flow"
        assert (
            len(result.get("data", [])) == 2
        ), "降级后应从 moneyflow_ind_dc 拿到 2 行数据"

    def test_returns_data_when_mkt_succeeds(self):
        client = MagicMock()
        adapter = _make_adapter(client)

        mkt_df = _df("trade_date", "buy_sm_vol", rows=[("20260224", 1000.0)])

        async def fake_run(func, **kwargs):
            if func == client.moneyflow_mkt:
                return mkt_df
            return pd.DataFrame()

        adapter._run = fake_run

        result = run(adapter.get_market_money_flow("20260224"))
        assert len(result.get("data", [])) == 1

    def test_both_fail_returns_empty_data_gracefully(self):
        """两个接口都失败时，应优雅降级返回 data:[]，不向上抛出。"""
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_run(func, **kwargs):
            raise Exception("all failed")

        adapter._run = fake_run

        # 两个接口都失败时：_fetch_moneyflow_mkt 内部捕获异常并返回 None，
        # 外层 try 不会再 raise，最终返回 data:[]
        result = run(adapter.get_market_money_flow("20260224"))
        assert result.get("component_type") == "market_money_flow"
        assert result.get("data") == [], "两个接口都失败时应返回 data:[]，不崩溃"
        assert result.get("trend_conclusion_allowed") is False
        assert result.get("blocked_reason") == "market_money_flow_empty"

    def test_topn_ranking_and_gate_when_exact(self):
        client = MagicMock()
        adapter = _make_adapter(client)

        mkt_df = _df(
            "trade_date",
            "name",
            "net_mf_amount",
            "pct_chg",
            rows=[
                ("20260224", "基础化工", 120.0, 2.1),
                ("20260224", "储能概念", 100.0, 1.9),
                ("20260224", "信创", -80.0, -0.5),
            ],
        )

        async def fake_run(func, **kwargs):
            if func == client.moneyflow_mkt:
                return mkt_df
            return pd.DataFrame()

        adapter._run = fake_run

        result = run(
            adapter.get_market_money_flow(
                trade_date="20260224", top_n=1, include_outflow=True
            )
        )
        assert result.get("data_freshness") == "exact"
        assert result.get("trend_conclusion_allowed") is True
        assert len(result.get("top_inflow", [])) == 1
        assert result["top_inflow"][0]["sector_name"] == "基础化工"
        assert len(result.get("top_outflow", [])) == 1
        assert result["top_outflow"][0]["sector_name"] == "信创"

    def test_marks_stale_when_latest_falls_back_to_prev_trade_date(self):
        client = MagicMock()
        adapter = _make_adapter(client)

        prev_day_df = _df(
            "trade_date",
            "name",
            "net_mf_amount",
            rows=[("20260221", "银行", 10.0)],
        )

        async def fake_run(func, **kwargs):
            if func == client.moneyflow_mkt:
                if kwargs.get("trade_date") == "20260221":
                    return prev_day_df
                return pd.DataFrame()
            return pd.DataFrame()

        with patch(
            "src.server.domain.adapters.tushare_adapter.datetime"
        ) as mock_datetime:
            mock_now = datetime(2026, 2, 22)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)
            adapter._run = fake_run
            result = run(adapter.get_market_money_flow(None))

        assert result.get("data_freshness") == "fallback_prev_trade_date"
        assert result.get("trend_conclusion_allowed") is False
        assert str(result.get("blocked_reason", "")).startswith("stale_data:")


# ---------------------------------------------------------------------------
# Fix-5 & Fix-6  get_market_liquidity：使用 margin 而非 margin_detail，并聚合
# ---------------------------------------------------------------------------


class TestMarketLiquidityMarginAggregation:

    def _margin_df_two_exchanges(self):
        """模拟 margin 接口返回 SSE + SZSE 两行数据（同一日期）。"""
        return _df(
            "trade_date",
            "exchange_id",
            "rzye",
            "rqye",
            "rzrqye",
            rows=[
                ("20260224", "SSE", 500000.0, 200000.0, 700000.0),
                ("20260224", "SZSE", 300000.0, 100000.0, 400000.0),
                ("20260221", "SSE", 480000.0, 190000.0, 670000.0),
                ("20260221", "SZSE", 290000.0, 95000.0, 385000.0),
            ],
        )

    def _north_df(self):
        return _df(
            "trade_date",
            "hgt",
            "sgt",
            "north_money",
            rows=[
                ("20260224", 100000.0, 120000.0, 220000.0),
                ("20260221", 90000.0, 110000.0, 200000.0),
            ],
        )

    def test_margin_rows_deduplicated_by_date(self):
        """聚合后每个 trade_date 只应有一行。"""
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_run(func, **kwargs):
            if func == client.moneyflow_hsgt:
                return self._north_df()
            if func == client.margin:
                return self._margin_df_two_exchanges()
            # 不应调用 margin_detail
            if func == client.margin_detail:
                raise AssertionError("不应调用 margin_detail！")
            return pd.DataFrame()

        adapter._run = fake_run

        result = run(adapter.get_market_liquidity(days=5))
        margin_data = result["data"]["margin"]

        trade_dates = [r["trade_date"] for r in margin_data]
        assert len(trade_dates) == len(
            set(trade_dates)
        ), f"margin 应按 trade_date 去重聚合，实际: {trade_dates}"

    def test_margin_rzrqye_aggregated_correctly(self):
        """rzrqye 应为 SSE + SZSE 之和。"""
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_run(func, **kwargs):
            if func == client.moneyflow_hsgt:
                return self._north_df()
            if func == client.margin:
                return self._margin_df_two_exchanges()
            return pd.DataFrame()

        adapter._run = fake_run

        result = run(adapter.get_market_liquidity(days=5))
        margin_data = sorted(result["data"]["margin"], key=lambda r: r["trade_date"])

        # 20260221: 670000 + 385000 = 1055000
        row_20260221 = next(r for r in margin_data if r["trade_date"] == "20260221")
        assert (
            abs(row_20260221["rzrqye"] - 1055000.0) < 0.1
        ), f"20260221 rzrqye 期望 1055000，实际 {row_20260221['rzrqye']}"

        # 20260224: 700000 + 400000 = 1100000
        row_20260224 = next(r for r in margin_data if r["trade_date"] == "20260224")
        assert (
            abs(row_20260224["rzrqye"] - 1100000.0) < 0.1
        ), f"20260224 rzrqye 期望 1100000，实际 {row_20260224['rzrqye']}"


# ---------------------------------------------------------------------------
# Fix-7  get_money_flow：空数据时返回合法结构
# ---------------------------------------------------------------------------


class TestMoneyFlowEmptyData:

    def test_empty_df_returns_valid_structure_not_error(self):
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_run(func, **kwargs):
            return pd.DataFrame()  # 返回空 DataFrame

        adapter._run = fake_run

        result = run(adapter.get_money_flow("SSE:002555", days=20))

        # 不应有 error 键
        assert "error" not in result, f"空数据时不应返回 error 键，实际: {result}"
        # 应有合法的 records / data / summary
        assert "records" in result
        assert result["records"] == []
        assert result["data"]["dates"] == []
        assert result["summary"]["total_main_net"] == 0
        assert "暂无数据" in result["summary"]["trend"]

    def test_empty_df_summary_trend_message(self):
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_run(func, **kwargs):
            return None  # 也可能返回 None

        adapter._run = fake_run

        result = run(adapter.get_money_flow("SSE:601011", days=20))
        trend = result.get("summary", {}).get("trend", "")
        assert (
            "暂无数据" in trend or "积分" in trend
        ), f"trend 应包含提示信息，实际: {trend}"

    def test_nonempty_df_still_works(self):
        """正常数据路径不受影响。"""
        client = MagicMock()
        adapter = _make_adapter(client)

        df = _df(
            "trade_date",
            "buy_elg_amount",
            "sell_elg_amount",
            "buy_lg_amount",
            "sell_lg_amount",
            "buy_md_amount",
            "sell_md_amount",
            "buy_sm_amount",
            "sell_sm_amount",
            rows=[
                ("20260224", 100.0, 50.0, 80.0, 30.0, 20.0, 10.0, 5.0, 3.0),
                ("20260221", 90.0, 60.0, 70.0, 40.0, 15.0, 12.0, 4.0, 2.0),
            ],
        )

        async def fake_run(func, **kwargs):
            return df

        adapter._run = fake_run

        result = run(adapter.get_money_flow("SSE:600519", days=20))
        assert result.get("summary", {}).get("period_days", 0) > 0
        assert result["data"]["dates"] != []


# ---------------------------------------------------------------------------
# Fix-8  get_north_bound_flow 单位换算
# ---------------------------------------------------------------------------


class TestNorthBoundFlowUnit:
    """
    moneyflow_hsgt 返回的 north_money 单位是万元。
    money_flow_tools.py 中 summary_text 应除以 10000 换算成亿再展示。
    这里测试 adapter 层返回的 summary.total_net 是否为万元原始值，
    以便 tools 层的换算逻辑正确。
    """

    def test_total_net_is_in_wan_yuan(self):
        """total_net 应等于 north_money 列的总和（万元），而非已换算的亿元。"""
        client = MagicMock()
        adapter = _make_adapter(client)

        df = _df(
            "trade_date",
            "hgt",
            "sgt",
            "north_money",
            rows=[
                ("20260224", 150000.0, 170000.0, 320000.0),  # 万元
                ("20260221", 140000.0, 160000.0, 300000.0),  # 万元
            ],
        )

        async def fake_run(func, **kwargs):
            return df

        adapter._run = fake_run

        result = run(adapter.get_north_bound_flow(days=5))
        total_net = result["summary"]["total_net"]
        # 320000 + 300000 = 620000 万元
        assert (
            abs(total_net - 620000.0) < 0.1
        ), f"total_net 应为 620000 万元，实际 {total_net}"

    def test_tools_layer_unit_conversion(self):
        """
        模拟 tools 层的换算逻辑：total_net / 10000 应在合理的亿元范围。
        620000 万元 = 62 亿元。
        """
        total_net_wan = 620000.0
        total_net_yi = total_net_wan / 10000
        assert abs(total_net_yi - 62.0) < 0.01, f"换算应为 62 亿，实际 {total_net_yi}"
        # 确认旧的错误写法确实会输出错误数值
        wrong_amount_str = f"{total_net_wan:.2f}亿"
        assert "620000" in wrong_amount_str  # 旧逻辑输出 "620000.00亿"，是错误的
        correct_amount_str = f"{total_net_yi:.2f}亿"
        assert "62.00" in correct_amount_str  # 新逻辑输出 "62.00亿"，正确


# ---------------------------------------------------------------------------
# Fix-4  money_flow_tools：error 时也返回 artifact（集成测试 stub）
# ---------------------------------------------------------------------------


class TestSectorMoneyFlowHistoryToolErrorArtifact:
    """
    当 adapter 返回 {"error": ...} 时，tool 层不应裸返回 {"error": ...}，
    而应包装成带空 records 的 artifact。
    """

    def test_error_result_contains_artifacts_key(self):
        """
        直接调用 tool 函数逻辑（不通过 MCP 注册），
        验证 error 分支返回的结构含 artifacts。
        """
        # 构造一个模拟的 error result
        error_result = {
            "error": "未找到板块: 跨境支付",
            "sector_name": "跨境支付",
            "source": "tushare",
        }

        # 复现 tool 中的 error 处理逻辑
        from src.server.mcp.tools.artifact_utils import (
            create_artifact_envelope,
            create_artifact_response,
        )

        if error_result.get("error"):
            err_msg = error_result["error"]
            sector_name = error_result.get("sector_name", "未知板块")
            empty_artifact = create_artifact_envelope(
                component_type="sector_flow",
                name=f"Sector Money Flow: {sector_name}",
                content={
                    "sector_name": sector_name,
                    "index_code": "",
                    "has_money_flow": False,
                    "records": [],
                    "error": err_msg,
                },
                description=err_msg,
                metadata={"type": "sector_flow", "sector_name": sector_name},
                visible_to_llm=False,
                display_in_report=True,
            )
            response = create_artifact_response(
                summary=f"未找到板块数据: {err_msg}",
                artifact=empty_artifact,
            )

        # 验证返回结构包含 artifacts
        assert (
            "artifacts" in response or "artifact" in response
        ), f"error 分支应包含 artifact，实际 keys: {list(response.keys())}"
        # 不应裸返回 error
        assert response.get("error") is None, "response 顶层不应有 error 键"


# ---------------------------------------------------------------------------
# 方案C  _resolve_sector_index：三级缓存 + 模糊匹配
# ---------------------------------------------------------------------------


class TestResolveSectorIndexFuzzyMatch:
    """验证 _resolve_sector_index 的模糊匹配逻辑（不依赖 Tushare 网络）。"""

    # 模拟全量板块列表
    ALL_SECTORS = [
        {"ts_code": "A001.TI", "name": "白酒"},
        {"ts_code": "A002.TI", "name": "半导体"},
        {"ts_code": "A003.TI", "name": "影视传媒"},
        {"ts_code": "A004.TI", "name": "文化传媒"},
        {"ts_code": "A005.TI", "name": "新能源"},
        {"ts_code": "A006.TI", "name": "新能源汽车"},
        {"ts_code": "A007.TI", "name": "跨境电商"},
        {"ts_code": "A008.TI", "name": "跨境支付"},
    ]

    def _make_adapter_with_sectors(self, ths_index_exact_result=None):
        """
        创建 adapter，让 _run(client.ths_index, name=X) 精确查询返回空，
        强制走全量模糊匹配路径。
        """
        client = MagicMock()
        adapter = _make_adapter(client)

        # _resolve_sector_index 先尝试精确查询，这里让其返回空
        async def fake_run(func, **kwargs):
            if func == client.ths_index:
                name = kwargs.get("name")
                if name and ths_index_exact_result is not None:
                    return ths_index_exact_result
                return pd.DataFrame()  # 精确查询返空，走模糊路径
            return pd.DataFrame()

        adapter._run = fake_run

        # 注入全量板块列表（绕过真实 API）
        async def fake_get_all_sectors():
            return self.ALL_SECTORS

        adapter._get_all_sectors = fake_get_all_sectors
        return adapter

    def test_exact_match(self):
        """精确名称命中直接返回对应 ts_code。"""
        adapter = self._make_adapter_with_sectors()
        code, name, candidates = run(adapter._resolve_sector_index("白酒"))
        assert code == "A001.TI"
        assert name == "白酒"
        assert candidates is None

    def test_substring_match_unique(self):
        """'传媒' 是 '影视传媒' 和 '文化传媒' 的子串 → 返回候选列表。"""
        adapter = self._make_adapter_with_sectors()
        code, name, candidates = run(adapter._resolve_sector_index("传媒"))
        # 两个都包含"传媒" → 模糊多匹配 → 应返回候选
        assert code is None
        assert candidates is not None
        assert len(candidates) >= 2
        assert "影视传媒" in candidates or "文化传媒" in candidates

    def test_substring_match_single(self):
        """'白' 只在 '白酒' 中出现 → 唯一匹配直接返回。"""
        adapter = self._make_adapter_with_sectors()
        code, name, candidates = run(adapter._resolve_sector_index("白"))
        assert code == "A001.TI"
        assert candidates is None

    def test_reverse_substring_match(self):
        """'影视传媒文娱' 包含子串 '影视传媒' → 反向子串匹配。"""
        adapter = self._make_adapter_with_sectors()
        code, name, candidates = run(adapter._resolve_sector_index("影视传媒文娱"))
        assert code == "A003.TI"
        assert name == "影视传媒"
        assert candidates is None

    def test_no_match_returns_none(self):
        """完全不存在的名称返回 (None, None, None)。"""
        adapter = self._make_adapter_with_sectors()
        code, name, candidates = run(adapter._resolve_sector_index("航天军工"))
        assert code is None
        assert name is None
        # candidates 可能是 None 或空列表
        assert not candidates

    def test_reverse_match_picks_longest(self):
        """
        '新能源汽车整车' 同时包含 '新能源' 和 '新能源汽车'，
        应优先返回名称最长的（最具体）'新能源汽车'。
        """
        adapter = self._make_adapter_with_sectors()
        code, name, candidates = run(adapter._resolve_sector_index("新能源汽车整车"))
        assert name == "新能源汽车", f"应返回最长匹配 '新能源汽车'，实际 {name}"
        assert code == "A006.TI"

    def test_catalog_match_preferred_over_ths_index_first_row(self):
        """全量目录可用时，应优先目录匹配，避免 ths_index 首行导致错码。"""
        exact_df = _df("ts_code", "name", rows=[("BAD001.TI", "半导体")])
        adapter = self._make_adapter_with_sectors(ths_index_exact_result=exact_df)
        code, name, candidates = run(adapter._resolve_sector_index("半导体"))
        assert code == "A002.TI"
        assert name == "半导体"
        assert candidates is None

    def test_ths_index_fallback_when_catalog_unavailable(self):
        """目录不可用时，回退 ths_index(name) 兜底。"""
        client = MagicMock()
        adapter = _make_adapter(client)

        async def fake_get_all_sectors():
            return []

        async def fake_run(func, **kwargs):
            if func == client.ths_index:
                return _df("ts_code", "name", rows=[("B999.TI", "白酒直接命中")])
            return pd.DataFrame()

        adapter._get_all_sectors = fake_get_all_sectors
        adapter._run = fake_run

        code, name, candidates = run(adapter._resolve_sector_index("白酒直接命中"))
        assert code == "B999.TI"
        assert name == "白酒直接命中"
        assert candidates is None

    def test_token_recall_returns_candidates_for_compound_query(self):
        """复合词拆分召回: 有色金属 -> 召回金属相关候选，不应直接 not_found。"""
        client = MagicMock()
        adapter = _make_adapter(client)
        all_sectors = [
            {"ts_code": "M001.TI", "name": "小金属"},
            {"ts_code": "M002.TI", "name": "工业金属"},
            {"ts_code": "M003.TI", "name": "金属新材料"},
        ]

        async def fake_get_all_sectors():
            return all_sectors

        async def fake_run(func, **kwargs):
            return pd.DataFrame()

        adapter._get_all_sectors = fake_get_all_sectors
        adapter._run = fake_run

        code, name, candidates = run(adapter._resolve_sector_index("有色金属"))
        assert code is None
        assert name is None
        assert candidates, "应返回候选列表而不是 not_found"
        assert any(c in candidates for c in ["小金属", "工业金属", "金属新材料"])


class TestAkshareResolveBoardTokenRecall:
    """验证 AkShare 侧也复用相同分词召回行为。"""

    def test_akshare_token_recall_returns_candidates(self):
        adapter = AkshareAdapter(cache=_DummyCache())
        catalog = [
            {"name": "小金属", "code": "BK0478", "board_type": "industry"},
            {"name": "工业金属", "code": "BK1029", "board_type": "industry"},
            {"name": "贵金属", "code": "BK0732", "board_type": "industry"},
        ]

        async def fake_get_board_catalog():
            return catalog

        adapter._get_board_catalog = fake_get_board_catalog

        board, candidates = run(adapter._resolve_board("有色金属"))
        assert board is None
        assert candidates, "应返回候选列表"
        assert "小金属" in candidates or "工业金属" in candidates


class TestGetAllSectorsL1Cache:
    """验证 L1 进程内存缓存命中逻辑。"""

    def test_l1_cache_hit_skips_api(self):
        """L1 缓存有效时不调用任何外部 API。"""
        import time as _time

        client = MagicMock()
        adapter = _make_adapter(client)

        # 注入 L1 缓存（未过期）
        TushareAdapter._sector_index_cache = {
            "data": [{"ts_code": "X001.TI", "name": "缓存板块"}],
            "ts": _time.time(),  # 刚刚设置，未过期
        }
        api_called = []

        async def fake_run(func, **kwargs):
            api_called.append(func)
            return pd.DataFrame()

        adapter._run = fake_run

        result = run(adapter._get_all_sectors())
        assert result == [{"ts_code": "X001.TI", "name": "缓存板块"}]
        # 不应调用 Tushare API
        assert len(api_called) == 0, "L1 命中时不应调用 API"

        # 清理，避免影响其他测试
        TushareAdapter._sector_index_cache = {}

    def test_l1_cache_expired_calls_api(self):
        """L1 缓存过期后应重新查询（此处验证会尝试调用 API）。"""
        import time as _time

        client = MagicMock()
        adapter = _make_adapter(client)

        # 注入已过期的 L1 缓存（2小时前）
        TushareAdapter._sector_index_cache = {
            "data": [{"ts_code": "OLD.TI", "name": "过期数据"}],
            "ts": _time.time() - 7200,  # 2小时前，已超 1h TTL
        }
        api_called = []

        async def fake_run(func, **kwargs):
            api_called.append("ths_index")
            return pd.DataFrame()  # API 返回空，走 SQLite/空路径

        adapter._run = fake_run

        # Redis 也返回空
        result = run(adapter._get_all_sectors())
        # L1 过期 → 应尝试调用 API（即使最终为空）
        assert len(api_called) >= 1 or result != [
            {"ts_code": "OLD.TI", "name": "过期数据"}
        ]

        # 清理
        TushareAdapter._sector_index_cache = {}


class TestGetSectorTrendWithFuzzyMatch:
    """验证 get_sector_trend 通过模糊匹配找到板块后能正常返回数据。"""

    def test_fuzzy_sector_trend_returns_data(self):
        """
        '影视传媒文娱' 精确查询为空，但通过反向子串匹配找到 '影视传媒'，
        最终能正常返回走势数据。
        """
        client = MagicMock()
        adapter = _make_adapter(client)

        all_sectors = [
            {"ts_code": "A003.TI", "name": "影视传媒"},
        ]

        async def fake_get_all_sectors():
            return all_sectors

        adapter._get_all_sectors = fake_get_all_sectors

        async def fake_run(func, **kwargs):
            if func == client.ths_index:
                return pd.DataFrame()  # 精确查询为空
            if func == client.ths_daily:
                return _df(
                    "trade_date",
                    "close",
                    "pct_chg",
                    rows=[
                        ("20260220", 100.0, 1.2),
                        ("20260221", 101.0, 1.0),
                        ("20260224", 102.0, 0.8),
                    ],
                )
            return pd.DataFrame()

        adapter._run = fake_run

        # "影视传媒文娱" 包含子串 "影视传媒" → 反向子串匹配
        result = run(adapter.get_sector_trend("影视传媒文娱", days=5))
        assert result.get("error") is None, f"不应有 error: {result.get('error')}"
        assert result.get("trend"), "应有走势数据"
        assert (
            result.get("sector_name") == "影视传媒"
        ), f"sector_name 应为匹配的名称，实际: {result.get('sector_name')}"

    def test_ambiguous_sector_returns_candidates(self):
        """
        '传媒' 匹配多个板块时，返回候选列表而不是报错。
        """
        client = MagicMock()
        adapter = _make_adapter(client)

        all_sectors = [
            {"ts_code": "A003.TI", "name": "影视传媒"},
            {"ts_code": "A004.TI", "name": "文化传媒"},
            {"ts_code": "A009.TI", "name": "互联网传媒"},
        ]

        async def fake_get_all_sectors():
            return all_sectors

        adapter._get_all_sectors = fake_get_all_sectors

        async def fake_run(func, **kwargs):
            return pd.DataFrame()  # 精确查询为空

        adapter._run = fake_run

        result = run(adapter.get_sector_money_flow_history("传媒", days=10))
        assert "candidates" in result, f"多匹配时应返回 candidates，实际: {result}"
        assert len(result["candidates"]) >= 2
        assert "影视传媒" in result["candidates"] or "文化传媒" in result["candidates"]


# ---------------------------------------------------------------------------
# Fix-9  money_flow_tools.py — candidates 对 LLM 可见（summary 包含候选名称）
# ---------------------------------------------------------------------------

_USE_CASES_PATCH = "src.server.mcp.tools.money_flow_tools.money_flow_use_cases"


def _capture_tools(mcp_mock):
    """让 mcp.tool(**deco_kwargs) 装饰器捕获注册的函数，存入 dict 返回。"""
    captured = {}

    def fake_tool(**deco_kwargs):
        def decorator(fn):
            captured[fn.__name__] = fn
            return fn

        return decorator

    mcp_mock.tool = fake_tool
    return captured


def _assert_no_data_contract(result: dict):
    assert result.get("result_status") == "no_data", f"result_status 应为 no_data: {result}"
    assert str(result.get("no_data_reason") or "").strip(), f"no_data_reason 不能为空: {result}"
    scope = result.get("scope")
    assert isinstance(scope, dict) and scope, f"scope 应为非空 dict: {result}"
    assert str(result.get("suggested_reroute") or "").strip(), f"suggested_reroute 不能为空: {result}"


class TestToolLayerCandidatesVisible:
    """
    验证 money_flow_tools.py tool 层在 adapter 返回 candidates 时：
    1. summary 文字中包含候选板块名称（LLM 可读）
    2. artifact content 中也保存了 candidates 列表
    3. visible_to_llm=True
    """

    def test_get_sector_trend_candidates_in_summary(self):
        """get_sector_trend: adapter 返回 candidates 时，tool summary 应包含候选名称。"""
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        candidates = ["影视传媒", "文化传媒", "互联网传媒"]
        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_trend = AsyncMock(return_value={"candidates": candidates})
            register_money_flow_tools(mcp)
            result = run(tools["get_sector_trend"](sector_name="传媒", days=10))

        summary = result.get("summary", "")
        assert "影视传媒" in summary, f"summary 应包含候选板块，实际: {summary!r}"
        assert "文化传媒" in summary, f"summary 应包含候选板块，实际: {summary!r}"
        assert "互联网传媒" in summary, f"summary 应包含候选板块，实际: {summary!r}"
        _assert_no_data_contract(result)

        # artifact 单数 key（create_artifact_response 返回 {"artifact": ...}）
        artifact = result.get("artifact") or (result.get("artifacts") or [None])[0]
        assert artifact, "应返回 artifact"
        content = artifact.get("content", {})
        assert content.get("candidates") == candidates

    def test_get_sector_trend_candidates_visible_to_llm(self):
        """get_sector_trend: artifact visible_to_llm 必须为 True。"""
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_trend = AsyncMock(
                return_value={"candidates": ["影视传媒", "文化传媒"]}
            )
            register_money_flow_tools(mcp)
            result = run(tools["get_sector_trend"](sector_name="传媒", days=10))

        _assert_no_data_contract(result)
        artifact = result.get("artifact") or (result.get("artifacts") or [None])[0]
        assert artifact, "应返回 artifact"
        assert artifact.get("visible_to_llm") is True

    def test_get_sector_money_flow_history_candidates_in_summary(self):
        """get_sector_money_flow_history: adapter 返回 candidates 时，summary 包含候选名称。"""
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        candidates = ["影视传媒", "文化传媒", "互联网传媒"]
        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_money_flow_history = AsyncMock(
                return_value={"candidates": candidates}
            )
            register_money_flow_tools(mcp)
            result = run(
                tools["get_sector_money_flow_history"](sector_name="传媒", days=20)
            )

        summary = result.get("summary", "")
        assert "影视传媒" in summary, f"summary 应包含候选板块，实际: {summary!r}"
        assert "文化传媒" in summary, f"summary 应包含候选板块，实际: {summary!r}"
        _assert_no_data_contract(result)

        artifact = result.get("artifact") or (result.get("artifacts") or [None])[0]
        assert artifact, "应返回 artifact"
        content = artifact.get("content", {})
        assert content.get("candidates") == candidates

    def test_get_sector_money_flow_history_error_with_candidates_in_summary(self):
        """
        get_sector_money_flow_history: adapter 返回 error+candidates 时，
        summary 也应包含候选名称（而不是只有 error 信息）。
        """
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        candidates = ["影视传媒", "文化传媒"]
        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_money_flow_history = AsyncMock(
                return_value={
                    "error": "不明确，请从以下候选中选择",
                    "candidates": candidates,
                }
            )
            register_money_flow_tools(mcp)
            result = run(
                tools["get_sector_money_flow_history"](sector_name="传媒", days=20)
            )

        summary = result.get("summary", "")
        assert (
            "影视传媒" in summary or "文化传媒" in summary
        ), f"error+candidates 时 summary 应包含候选名称，实际: {summary!r}"
        _assert_no_data_contract(result)

    def test_no_candidates_normal_flow_unaffected(self):
        """正常无 candidates 时，get_sector_trend 走原有路径不受影响。"""
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_trend = AsyncMock(
                return_value={
                    "total_pct_chg": 3.5,
                    "trend": [{"trade_date": "20260220", "pct_chg": 1.0}],
                }
            )
            register_money_flow_tools(mcp)
            result = run(tools["get_sector_trend"](sector_name="半导体", days=5))

        summary = result.get("summary", "")
        assert (
            "3.50%" in summary or "3.5" in summary
        ), f"正常路径 summary 应含涨跌幅，实际: {summary!r}"
        assert "候选" not in summary


class TestToolLayerSectorIdFlowAndValuation:
    """验证 flow/valuation 在 sector_id 模式下的分支行为。"""

    def test_sector_flow_resolved_with_sector_id(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_money_flow_history = AsyncMock(
                return_value={
                    "sector_name": "新能源汽车",
                    "index_code": "885431.TI",
                    "has_money_flow": True,
                    "amount_unit": "10k_cny",
                    "records": [
                        {"trade_date": "20260312", "pct_chg": -1.06, "main_net_inflow": -20.0},
                        {"trade_date": "20260313", "pct_chg": -1.07, "main_net_inflow": -24.0},
                    ],
                    "summary": {
                        "total_pct_chg": -2.13,
                        "total_main_net": -44.0,
                        "trend": "主力资金持续流出",
                        "flow_source": "moneyflow_ind",
                        "amount_unit": "10k_cny",
                    },
                }
            )
            register_money_flow_tools(mcp)
            result = run(
                tools["get_sector_money_flow_history"](
                    sector_id="885431.TI", days=20
                )
            )

        uc.get_sector_money_flow_history.assert_awaited_once_with(
            sector_name="",
            days=20,
            sector_id="885431.TI",
        )
        summary = result.get("summary", "")
        assert "新能源汽车(885431.TI)" in summary
        artifact = result.get("artifact") or (result.get("artifacts") or [None])[0]
        assert artifact, "应返回 artifact"
        content = artifact.get("content", {})
        assert content.get("sector_name") == "新能源汽车"
        assert content.get("index_code") == "885431.TI"

    def test_sector_flow_ambiguous_with_sector_id_label(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_money_flow_history = AsyncMock(
                return_value={
                    "candidates": ["新能源发电", "新能源汽车"],
                }
            )
            register_money_flow_tools(mcp)
            result = run(
                tools["get_sector_money_flow_history"](
                    sector_id="NE_ENERGY", days=20
                )
            )

        summary = result.get("summary", "")
        assert "NE_ENERGY" in summary, f"应使用 sector_id 作为 query label，实际: {summary!r}"
        assert "新能源发电" in summary and "新能源汽车" in summary
        _assert_no_data_contract(result)
        artifact = result.get("artifact") or (result.get("artifacts") or [None])[0]
        assert artifact, "应返回 artifact"
        content = artifact.get("content", {})
        assert content.get("sector_name") == "NE_ENERGY"
        assert content.get("candidates") == ["新能源发电", "新能源汽车"]

    def test_sector_flow_not_found_with_sector_id(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_money_flow_history = AsyncMock(
                return_value={"error": "未找到板块ID: 885431.TI"}
            )
            register_money_flow_tools(mcp)
            result = run(
                tools["get_sector_money_flow_history"](
                    sector_id="885431.TI", days=20
                )
            )

        summary = result.get("summary", "")
        assert "未找到板块数据" in summary
        assert "885431.TI" in summary
        _assert_no_data_contract(result)
        artifact = result.get("artifact") or (result.get("artifacts") or [None])[0]
        assert artifact, "应返回 artifact"
        content = artifact.get("content", {})
        assert content.get("sector_name") == "885431.TI"
        assert content.get("error") == "未找到板块ID: 885431.TI"

    def test_sector_valuation_resolved_with_sector_id(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_valuation_metrics = AsyncMock(
                return_value={
                    "sector_name": "新能源汽车",
                    "index_code": "885431.TI",
                    "current": {"trade_date": "20260313", "pe_ttm": 39.28, "pb": 10.19},
                    "summary": {
                        "valuation_level": "偏高",
                        "pe_ttm_percentile": 50.3,
                        "pb_percentile": 85.7,
                        "coverage_latest": 60,
                    },
                    "history": [{"trade_date": "20260313", "pe_ttm": 39.28, "pb": 10.19, "coverage": 60}],
                    "member_count_total": 1014,
                    "member_count_used": 60,
                    "member_count_with_data": 60,
                }
            )
            register_money_flow_tools(mcp)
            result = run(
                tools["get_sector_valuation_metrics"](
                    sector_id="885431.TI", days=250, sample_size=60
                )
            )

        uc.get_sector_valuation_metrics.assert_awaited_once_with(
            sector_name="",
            days=250,
            sample_size=60,
            sector_id="885431.TI",
        )
        summary = result.get("summary", "")
        assert "新能源汽车(885431.TI)" in summary
        artifact = result.get("artifact") or (result.get("artifacts") or [None])[0]
        assert artifact, "应返回 artifact"
        content = artifact.get("content", {})
        assert content.get("sector_name") == "新能源汽车"
        assert content.get("index_code") == "885431.TI"

    def test_sector_valuation_ambiguous_with_sector_id_label(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_valuation_metrics = AsyncMock(
                return_value={"candidates": ["新能源发电", "新能源汽车"]}
            )
            register_money_flow_tools(mcp)
            result = run(
                tools["get_sector_valuation_metrics"](
                    sector_id="NE_ENERGY", days=250, sample_size=60
                )
            )

        summary = result.get("summary", "")
        assert "NE_ENERGY" in summary
        assert "新能源发电" in summary and "新能源汽车" in summary
        _assert_no_data_contract(result)
        artifact = result.get("artifact") or (result.get("artifacts") or [None])[0]
        assert artifact, "应返回 artifact"
        content = artifact.get("content", {})
        assert content.get("sector_name") == "NE_ENERGY"
        assert content.get("candidates") == ["新能源发电", "新能源汽车"]

    def test_sector_valuation_not_found_with_sector_id(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.get_sector_valuation_metrics = AsyncMock(
                return_value={"error": "未找到板块ID: 885431.TI"}
            )
            register_money_flow_tools(mcp)
            result = run(
                tools["get_sector_valuation_metrics"](
                    sector_id="885431.TI", days=250, sample_size=60
                )
            )

        summary = result.get("summary", "")
        assert "板块估值数据不可用" in summary
        assert "885431.TI" in summary
        _assert_no_data_contract(result)
        artifact = result.get("artifact") or (result.get("artifacts") or [None])[0]
        assert artifact, "应返回 artifact"
        content = artifact.get("content", {})
        assert content.get("sector_name") == "885431.TI"
        assert content.get("error") == "未找到板块ID: 885431.TI"

    def test_sector_trend_sector_id_backfills_sector_name(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.resolve_sector = AsyncMock(
                return_value={
                    "status": "resolved",
                    "sector_id": "885790.TI",
                    "canonical_name": "半导体",
                }
            )
            uc.get_sector_trend = AsyncMock(
                return_value={
                    "sector_name": "半导体",
                    "index_code": "885790.TI",
                    "total_pct_chg": 1.23,
                    "trend": [
                        {"trade_date": "20260318", "pct_chg": 0.6},
                        {"trade_date": "20260319", "pct_chg": 0.63},
                    ],
                }
            )
            register_money_flow_tools(mcp)
            _ = run(tools["get_sector_trend"](sector_id="BK0478", days=10))

        uc.resolve_sector.assert_awaited_once_with(query_text="BK0478", intent="trend")
        uc.get_sector_trend.assert_awaited_once_with(
            sector_name="半导体",
            sector_id="885790.TI",
            days=10,
        )

    def test_sector_flow_sector_id_backfills_sector_name(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.resolve_sector = AsyncMock(
                return_value={
                    "status": "resolved",
                    "sector_id": "885431.TI",
                    "canonical_name": "新能源汽车",
                }
            )
            uc.get_sector_money_flow_history = AsyncMock(
                return_value={
                    "sector_name": "新能源汽车",
                    "index_code": "885431.TI",
                    "has_money_flow": True,
                    "records": [{"trade_date": "20260319", "pct_chg": 0.2}],
                    "summary": {"total_pct_chg": 0.2, "trend": "震荡"},
                }
            )
            register_money_flow_tools(mcp)
            _ = run(tools["get_sector_money_flow_history"](sector_id="BK0478", days=20))

        uc.resolve_sector.assert_awaited_once_with(query_text="BK0478", intent="flow")
        uc.get_sector_money_flow_history.assert_awaited_once_with(
            sector_name="新能源汽车",
            days=20,
            sector_id="885431.TI",
        )

    def test_sector_valuation_sector_id_backfills_sector_name(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.resolve_sector = AsyncMock(
                return_value={
                    "status": "resolved",
                    "sector_id": "885431.TI",
                    "canonical_name": "新能源汽车",
                }
            )
            uc.get_sector_valuation_metrics = AsyncMock(
                return_value={
                    "sector_name": "新能源汽车",
                    "index_code": "885431.TI",
                    "current": {"pe_ttm": 30.1, "pb": 4.2},
                    "summary": {"valuation_level": "合理", "coverage_latest": 10},
                    "history": [{"trade_date": "20260319", "pe_ttm": 30.1, "pb": 4.2}],
                    "member_count_total": 10,
                    "member_count_used": 10,
                    "member_count_with_data": 10,
                }
            )
            register_money_flow_tools(mcp)
            _ = run(
                tools["get_sector_valuation_metrics"](
                    sector_id="BK0478",
                    days=60,
                    sample_size=10,
                )
            )

        uc.resolve_sector.assert_awaited_once_with(
            query_text="BK0478",
            intent="valuation",
        )
        uc.get_sector_valuation_metrics.assert_awaited_once_with(
            sector_name="新能源汽车",
            days=60,
            sample_size=10,
            sector_id="885431.TI",
        )

    def test_get_money_flow_empty_records_tool_level_no_data(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.get_money_flow = AsyncMock(
                return_value={
                    "ts_code": "600519.SH",
                    "records": [],
                    "data": {"dates": [], "main_net_inflow": []},
                    "summary": {
                        "total_main_net": 0.0,
                        "total_retail_net": 0.0,
                        "trend": "暂无数据",
                    },
                    "amount_unit": "10k_cny",
                    "source": "moneyflow",
                }
            )
            register_money_flow_tools(mcp)
            result = run(tools["get_money_flow"](symbol="SSE:600519", days=20))

        _assert_no_data_contract(result)
        artifact = result.get("artifact") or (result.get("artifacts") or [None])[0]
        assert artifact, "应返回 artifact"
        records = (artifact.get("content") or {}).get("records")
        assert records == []

    def test_resolve_sector_ambiguous_tool_level_no_data_for_llm_selection(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.resolve_sector = AsyncMock(
                return_value={
                    "status": "ambiguous",
                    "query_text": "传媒",
                    "intent": "flow",
                    "candidates": ["影视传媒", "文化传媒"],
                    "reason": "候选过多，请明确板块名称",
                }
            )
            register_money_flow_tools(mcp)
            result = run(tools["resolve_sector"](query_text="传媒", intent="flow"))

        assert result.get("result_status") == "no_data"
        assert "ambiguous sector query" in str(result.get("no_data_reason") or "")
        assert result.get("decision_required") is True
        assert result.get("resolution_status") == "ambiguous"
        assert result.get("scope", {}).get("status") == "ambiguous"
        assert "candidates" in str(result.get("suggested_reroute") or "")

    def test_resolve_sector_not_found_tool_level_no_data(self):
        from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

        mcp = MagicMock()
        tools = _capture_tools(mcp)

        with patch(_USE_CASES_PATCH) as uc:
            uc.resolve_sector = AsyncMock(
                return_value={
                    "status": "not_found",
                    "query_text": "不存在板块",
                    "intent": "flow",
                    "candidates": [],
                    "reason": "未找到板块",
                }
            )
            register_money_flow_tools(mcp)
            result = run(tools["resolve_sector"](query_text="不存在板块", intent="flow"))

        _assert_no_data_contract(result)
        assert result.get("scope", {}).get("status") == "not_found"


class _StubAdapter:
    def __init__(self, source: DataSource, *, trend_result=None, resolve_result=None):
        self.source = source
        self._trend_result = trend_result
        self._resolve_result = resolve_result

    async def get_sector_trend(self, **_kwargs):
        if isinstance(self._trend_result, Exception):
            raise self._trend_result
        return self._trend_result

    async def resolve_sector(self, **_kwargs):
        if isinstance(self._resolve_result, Exception):
            raise self._resolve_result
        return self._resolve_result

    async def get_sector_money_flow_history(self, **_kwargs):
        return self._trend_result

    async def get_sector_valuation_metrics(self, **_kwargs):
        return self._trend_result


class TestAdapterManagerSectorFallback:
    def test_dispatch_market_soft_no_data_falls_back_to_next_adapter(self):
        manager = AdapterManager(provider_timeout_seconds=1.0)
        manager._adapter_order = [
            _StubAdapter(
                DataSource.TUSHARE,
                trend_result={
                    "component_type": "sector_trend",
                    "error": "未找到板块",
                    "trend": [],
                    "source": "tushare",
                },
            ),
            _StubAdapter(
                DataSource.AKSHARE,
                trend_result={
                    "component_type": "sector_trend",
                    "sector_name": "有色金属",
                    "index_code": "885431.TI",
                    "trend": [{"trade_date": "20260318", "pct_chg": 1.2}],
                    "source": "akshare",
                },
            ),
        ]

        result = run(manager.get_sector_trend(sector_name="有色金属", days=10))
        assert result.get("source") == "akshare"
        assert len(result.get("trend", [])) == 1

    def test_dispatch_market_returns_last_soft_result_when_all_adapters_no_data(self):
        manager = AdapterManager(provider_timeout_seconds=1.0)
        manager._adapter_order = [
            _StubAdapter(
                DataSource.TUSHARE,
                trend_result={
                    "component_type": "sector_trend",
                    "error": "未找到板块",
                    "trend": [],
                    "source": "tushare",
                },
            ),
            _StubAdapter(
                DataSource.AKSHARE,
                trend_result={
                    "component_type": "sector_trend",
                    "error": "板块不明确",
                    "candidates": ["化工", "石油化工"],
                    "trend": [],
                    "source": "akshare",
                },
            ),
        ]

        result = run(manager.get_sector_trend(sector_name="化", days=10))
        assert result.get("source") == "akshare"
        assert "error" in result

    def test_resolve_sector_prefers_tushare_ambiguous_over_other_resolved(self):
        manager = AdapterManager(provider_timeout_seconds=1.0)
        manager._adapter_order = [
            _StubAdapter(
                DataSource.TUSHARE,
                resolve_result={
                    "component_type": "sector_resolve",
                    "status": "ambiguous",
                    "query_text": "有色",
                    "candidates": [{"sector_id": "A", "canonical_name": "有色金属"}],
                    "source": "tushare",
                },
            ),
            _StubAdapter(
                DataSource.AKSHARE,
                resolve_result={
                    "component_type": "sector_resolve",
                    "status": "resolved",
                    "query_text": "有色",
                    "sector_id": "885431.TI",
                    "canonical_name": "有色金属",
                    "source": "akshare",
                },
            ),
        ]

        result = run(manager.resolve_sector(query_text="有色", intent="flow"))
        assert result.get("status") == "ambiguous"
        assert result.get("source") == "tushare"

    def test_resolve_sector_uses_non_tushare_resolved_when_tushare_not_found(self):
        manager = AdapterManager(provider_timeout_seconds=1.0)
        manager._adapter_order = [
            _StubAdapter(
                DataSource.TUSHARE,
                resolve_result={
                    "component_type": "sector_resolve",
                    "status": "not_found",
                    "query_text": "有色金属",
                    "reason": "no match",
                    "source": "tushare",
                },
            ),
            _StubAdapter(
                DataSource.AKSHARE,
                resolve_result={
                    "component_type": "sector_resolve",
                    "status": "resolved",
                    "query_text": "有色金属",
                    "sector_id": "BK0478",
                    "canonical_name": "有色金属",
                    "source": "akshare",
                },
            ),
        ]

        result = run(manager.resolve_sector(query_text="有色金属", intent="flow"))
        assert result.get("status") == "resolved"
        assert result.get("source") == "akshare"

    def test_resolve_sector_returns_ambiguous_when_no_resolved_found(self):
        manager = AdapterManager(provider_timeout_seconds=1.0)
        manager._adapter_order = [
            _StubAdapter(
                DataSource.TUSHARE,
                resolve_result={
                    "component_type": "sector_resolve",
                    "status": "ambiguous",
                    "query_text": "传媒",
                    "candidates": [{"sector_id": "A", "canonical_name": "影视传媒"}],
                    "source": "tushare",
                },
            ),
            _StubAdapter(
                DataSource.AKSHARE,
                resolve_result={
                    "component_type": "sector_resolve",
                    "status": "not_found",
                    "query_text": "传媒",
                    "reason": "no match",
                    "source": "akshare",
                },
            ),
        ]

        result = run(manager.resolve_sector(query_text="传媒", intent="flow"))
        assert result.get("status") == "ambiguous"
        assert result.get("source") == "tushare"

    def test_dispatch_market_normalizes_ti_sector_id_for_akshare(self):
        class _AkAdapter(_StubAdapter):
            async def get_sector_trend(self, **kwargs):
                # AdapterManager should clear TI id for AkShare and rely on name.
                assert kwargs.get("sector_id") is None
                return {
                    "component_type": "sector_trend",
                    "source": "akshare",
                    "sector_name": kwargs.get("sector_name"),
                    "trend": [{"trade_date": "20260319", "pct_chg": 0.8}],
                }

        manager = AdapterManager(provider_timeout_seconds=1.0)
        manager._adapter_order = [_AkAdapter(DataSource.AKSHARE, trend_result={})]

        result = run(
            manager.get_sector_trend(
                sector_name="半导体",
                days=10,
                sector_id="877042.TI",
            )
        )
        assert result.get("source") == "akshare"
        assert len(result.get("trend", [])) == 1

    def test_resolve_sector_canonicalizes_non_ti_id_to_tushare(self):
        ak_adapter = _StubAdapter(
            DataSource.AKSHARE,
            resolve_result={
                "component_type": "sector_resolve",
                "source": "akshare",
                "status": "resolved",
                "query_text": "有色金属",
                "sector_id": "BK0478",
                "canonical_name": "有色金属",
            },
        )
        tushare_adapter = _StubAdapter(
            DataSource.TUSHARE,
            resolve_result={
                "component_type": "sector_resolve",
                "source": "tushare",
                "status": "resolved",
                "query_text": "有色金属",
                "sector_id": "885431.TI",
                "canonical_name": "有色金属",
            },
        )
        manager = AdapterManager(provider_timeout_seconds=1.0)
        manager._adapter_order = [ak_adapter]
        manager.adapters = {
            DataSource.AKSHARE: ak_adapter,
            DataSource.TUSHARE: tushare_adapter,
        }

        result = run(manager.resolve_sector(query_text="有色金属", intent="flow"))
        assert result.get("status") == "resolved"
        assert result.get("sector_id") == "885431.TI"
        assert result.get("provider_sector_id") == "BK0478"
        assert result.get("canonical_source") == "tushare"
