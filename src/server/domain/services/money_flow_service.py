"""资金流向分析服务 (Money Flow Analysis Service)

基于适配器模式，提供个股和板块的资金流入流出数据。
支持多数据源自动降级 (Tushare → AkShare → Baostock)。
返回结构化 JSON 数据，供前端可视化组件渲染。
"""

from typing import Any, Dict

from src.server.utils.logger import logger


class MoneyFlowService:
    """资金流向分析服务

    遵循项目适配器模式，通过 AdapterManager 获取数据，
    而非直接依赖具体的数据源连接。
    """

    def __init__(self, adapter_manager):
        """
        Args:
            adapter_manager: AdapterManager 实例，负责数据源路由与降级
        """
        self.adapter_manager = adapter_manager
        self.logger = logger

    async def get_money_flow(self, symbol: str, days: int = 20) -> Dict[str, Any]:
        """
        获取个股资金流向数据

        Args:
            symbol: 股票代码 (支持 SSE:600519 或 600519 格式)
            days: 获取最近 N 天数据

        Returns:
            结构化的资金流向数据
        """
        ticker = self._normalize_to_internal(symbol)

        self.logger.info(f"Getting money flow for {ticker}, days={days}")

        try:
            result = await self.adapter_manager.get_money_flow(ticker, days)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get money flow for {symbol}: {e}")
            return {"error": str(e), "symbol": symbol, "component_type": "money_flow"}

    async def get_north_bound_flow(self, days: int = 30) -> Dict[str, Any]:
        """
        获取北向资金(沪深港通)流向数据

        Args:
            days: 获取最近 N 天数据

        Returns:
            结构化的北向资金数据
        """
        self.logger.info(f"Getting north bound flow, days={days}")

        try:
            result = await self.adapter_manager.get_north_bound_flow(days)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get north bound flow: {e}")
            return {"error": str(e), "component_type": "north_bound_flow"}

    def _normalize_to_internal(self, symbol: str) -> str:
        """将各种格式转换为内部格式 (SSE:600519)"""
        if ":" in symbol:
            return symbol.upper()

        if "." in symbol:
            code, suffix = symbol.split(".", 1)
            suffix = suffix.upper()
            if suffix == "SH":
                return f"SSE:{code}"
            elif suffix == "SZ":
                return f"SZSE:{code}"
            elif suffix == "BJ":
                return f"BSE:{code}"
            return symbol

        symbol = symbol.upper().strip()
        if len(symbol) == 6 and symbol.isdigit():
            if symbol.startswith("6"):
                return f"SSE:{symbol}"
            elif symbol.startswith(("0", "3")):
                return f"SZSE:{symbol}"
            elif symbol.startswith("8"):
                return f"BSE:{symbol}"

        return symbol
