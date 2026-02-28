# src/server/mcp/tools/asset_tools.py
"""MCP tools for asset search and management.
Provides asset search, price queries, and asset information retrieval.
Returns structured data (JSON).

Active Tools (1):
  - get_kline_data: 获取K线历史价格数据

Disabled Tools:
  - get_asset_info: 🔇 当前策略仅保留K线原子能力
  - get_real_time_price: 🔇 由策略层改用 get_kline_data 派生最新价
  - get_multiple_prices: 🔇 当前策略仅保留K线原子能力
  - get_market_report: 🔇 聚合工具，应由 Agent 层调用原子工具组合
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP, Context

from src.server.core.use_cases import market as market_use_cases
from src.server.domain.types import AssetType
from src.server.utils.logger import logger
from src.server.mcp.tools.artifact_utils import (
    create_artifact_envelope,
    create_artifact_response,
    create_symbol_error_response,
)
from src.server.domain.symbols.errors import SymbolResolutionError


# ============================================================
# MCP 工具注册 (保持原有接口不变)
# ============================================================


def register_asset_tools(mcp: FastMCP):
    """Register asset-related tools."""

    async def get_asset_info(ticker: str, ctx: Context = None) -> Dict[str, Any]:
        """Get detailed asset information.

        Args:
            ticker: Asset identifier. Prefer EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
              Raw input like “苹果”, “白银期货”, “EURUSD” is also supported.
            ctx: FastMCP Context for logging

        Returns:
            Asset details
        """
        if ctx:
            await ctx.info(f"🔧 获取资产信息: {ticker}", extra={"ticker": ticker})

        try:
            logger.info("MCP tool called: get_asset_info", ticker=ticker)
            asset = await market_use_cases.get_asset_info(ticker)
            if asset:
                result = asset
                result["component_type"] = "asset_info"

                if ctx:
                    await ctx.info(f"✅ 获取资产信息完成: {ticker}")

                # 构造 Artifact
                artifact = create_artifact_envelope(
                    component_type="asset_info",
                    name=f"{ticker} 资产信息",
                    content=result,
                    description=f"{result.get('name')} ({ticker}) 基本信息",
                )

                # 构造 Summary
                summary = (
                    f"已获取 {result.get('name')} ({ticker}) 的基本信息。\\n"
                    f"行业：{result.get('industry')}\\n"
                    f"市值：{result.get('market_cap')}"
                )

                return create_artifact_response(summary=summary, artifact=artifact)

            if ctx:
                await ctx.warning(f"⚠️ 未找到资产信息: {ticker}")

            return {
                "error": f"Asset not found: {ticker}",
                "component_type": "asset_info",
            }

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="asset_info", name=f"{ticker} 资产信息"
            )
        except Exception as e:
            logger.error(f"Get asset info failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取资产信息失败: {ticker}", extra={"error": str(e)}
                )
            return {"error": str(e), "component_type": "asset_info"}

    async def get_real_time_price(ticker: str, ctx: Context = None) -> Dict[str, Any]:
        """Get real-time price for an asset.

        Args:
            ticker: Asset identifier. Prefer EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
              Raw input like “苹果”, “白银期货”, “EURUSD” is also supported.
            ctx: FastMCP Context for logging

        Returns:
            Real-time price data
        """
        if ctx:
            await ctx.info(f"🔧 获取实时价格: {ticker}", extra={"ticker": ticker})

        try:
            logger.info("MCP tool called: get_real_time_price", ticker=ticker)
            price = await market_use_cases.get_real_time_price(ticker)
            if price:
                result = price
                result["component_type"] = "real_time_price"

                if ctx:
                    await ctx.info(
                        f"✅ 获取实时价格完成: {ticker}",
                        extra={"price": result.get("price")},
                    )

                # 构造 Artifact
                artifact = create_artifact_envelope(
                    component_type="real_time_price",
                    name=f"{ticker} 实时报价",
                    content=result,
                    description=(
                        f"{ticker} 当前价格: {result.get('price')} {result.get('currency')}"
                    ),
                )

                # 构造 Summary
                summary = (
                    f"{ticker} 最新价 {result.get('price')} {result.get('currency')}，"
                    f"涨跌幅 {result.get('change_percent')}%"
                )

                return create_artifact_response(summary=summary, artifact=artifact)

            if ctx:
                await ctx.warning(f"⚠️ 未找到实时价格: {ticker}")

            return {
                "error": f"Price not found for {ticker}",
                "component_type": "real_time_price",
            }

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="real_time_price", name=f"{ticker} 实时报价"
            )
        except Exception as e:
            logger.error(f"Get real-time price failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取实时价格失败: {ticker}", extra={"error": str(e)}
                )
            return {"error": str(e), "component_type": "real_time_price"}

    async def get_multiple_prices(
        tickers: list[str], ctx: Context = None
    ) -> Dict[str, Any]:
        """Get real-time prices for multiple assets.

        Args:
            tickers: List of asset tickers. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            ctx: FastMCP Context for logging

        Returns:
            Dictionary mapping tickers to price data
        """
        if ctx:
            await ctx.info(
                f"🔧 批量获取价格: {len(tickers)}个资产", extra={"tickers": tickers}
            )

        try:
            result = await market_use_cases.get_multiple_prices(tickers)
            result["component_type"] = "multiple_prices"

            if ctx:
                await ctx.info(f"✅ 批量获取价格完成: {len(result)}个结果")

            # 构造 Artifact
            artifact = create_artifact_envelope(
                component_type="multiple_prices",
                name="批量实时报价",
                content=result,
                description=f"包含 {len(tickers)} 个资产的实时价格",
            )

            # 构造 Summary
            summary = f"已获取 {len(tickers)} 个资产的实时价格。"

            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="multiple_prices", name="批量实时报价"
            )
        except Exception as e:
            logger.error(f"MCP tool error in get_multiple_prices: {e}", exc_info=True)
            if ctx:
                await ctx.error(f"❌ 批量获取价格失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "multiple_prices"}

    @mcp.tool(tags={"asset"})
    async def get_kline_data(
        ticker: str,
        start_date: str | None = None,
        end_date: str | None = None,
        interval: str = "1d",
        period: str | None = None,
        limit: int | None = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get K-line historical price data.

        Supports two usage modes:
        1. Date range mode: provide start_date + end_date
        2. Period + limit mode: provide period and/or limit,
           dates are auto-calculated from today

        Args:
            ticker: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
            start_date: Start date (YYYY-MM-DD), optional if
                limit is provided
            end_date: End date (YYYY-MM-DD), defaults to today
            interval: Data interval (1d, 1wk, 1mo)
            period: Shorthand for interval: "daily" -> 1d,
                "weekly" -> 1wk, "monthly" -> 1mo.
                If provided, overrides interval.
            limit: Number of data points (trading days) to
                fetch. Auto-derives start_date from today.
                E.g. limit=60 fetches ~60 trading days.
            ctx: FastMCP Context for logging

        Returns:
            Dictionary containing historical price data list
        """
        # Resolve period -> interval mapping
        if period:
            period_map = {
                "daily": "1d",
                "weekly": "1wk",
                "monthly": "1mo",
                "1d": "1d",
                "1wk": "1wk",
                "1mo": "1mo",
            }
            interval = period_map.get(period.lower(), interval)

        # Resolve dates
        if end_date:
            end = datetime.strptime(end_date, "%Y-%m-%d")
        else:
            end = datetime.now()
            end_date = end.strftime("%Y-%m-%d")

        if start_date:
            start = datetime.strptime(start_date, "%Y-%m-%d")
        elif limit:
            # Estimate calendar days from trading days
            if interval in ("1wk", "weekly"):
                calendar_days = limit * 7 + 14
            elif interval in ("1mo", "monthly"):
                calendar_days = limit * 31 + 31
            else:
                # daily: ~1.5x for weekends/holidays
                calendar_days = int(limit * 1.5) + 10
            start = end - timedelta(days=calendar_days)
            start_date = start.strftime("%Y-%m-%d")
        else:
            # Default: 6 months
            start = end - timedelta(days=180)
            start_date = start.strftime("%Y-%m-%d")

        if ctx:
            await ctx.info(
                f"🔧 获取历史价格: {ticker}",
                extra={
                    "ticker": ticker,
                    "start": start_date,
                    "end": end_date,
                    "interval": interval,
                    "limit": limit,
                },
            )

        try:
            logger.info(
                "MCP tool called: get_kline_data",
                ticker=ticker,
                start=start_date,
                end=end_date,
            )

            prices = await market_use_cases.get_historical_prices(
                ticker=ticker,
                start_date=start,
                end_date=end,
                interval=interval,
            )

            # If limit specified, trim to exact count
            if limit and len(prices) > limit:
                prices = prices[-limit:]

            if ctx:
                await ctx.info(
                    f"✅ 获取历史价格完成: {ticker}",
                    extra={"count": len(prices)},
                )

            result = {
                "component_type": "price_chart",
                "symbol": ticker,
                "data": prices,
            }

            def _to_float(v: Any) -> float | None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            latest = prices[-1] if prices else {}
            first = prices[0] if prices else {}
            latest_date = str(latest.get("timestamp") or end_date)
            latest_date = latest_date[:10] if latest_date else "N/A"

            first_close = _to_float(first.get("close_price") or first.get("price"))
            latest_close = _to_float(latest.get("close_price") or latest.get("price"))
            pct_change = (
                ((latest_close - first_close) / first_close * 100)
                if (
                    latest_close is not None
                    and first_close is not None
                    and first_close != 0
                )
                else None
            )

            highs = []
            lows = []
            for row in prices:
                high_val = _to_float(row.get("high_price"))
                low_val = _to_float(row.get("low_price"))
                if high_val is not None:
                    highs.append(high_val)
                if low_val is not None:
                    lows.append(low_val)
            period_high = max(highs) if highs else None
            period_low = min(lows) if lows else None

            if latest_close is None:
                description = (
                    f"{ticker} K线({interval}, {start_date}~{end_date}): "
                    f"样本{len(prices)}根, 缺少有效收盘价"
                )
            else:
                description = (
                    f"{ticker} K线({interval}, {start_date}~{end_date}): 最新{latest_date}收盘"
                    f"{latest_close:.2f}, 区间涨跌"
                    f"{f'{pct_change:+.2f}%' if pct_change is not None else 'N/A'}, "
                    f"最高/最低{f'{period_high:.2f}' if period_high is not None else 'N/A'}"
                    f"/{f'{period_low:.2f}' if period_low is not None else 'N/A'}, "
                    f"样本{len(prices)}根"
                )

            artifact = create_artifact_envelope(
                component_type="price_chart",
                name=f"{ticker} 历史价格",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=True,
            )

            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(
                    f"⚠️ 符号解析失败: {ticker}",
                    extra=e.to_dict(),
                )
            return create_symbol_error_response(
                e,
                component_type="price_chart",
                name=f"{ticker} 历史价格",
            )
        except Exception as e:
            logger.error(f"Get historical prices failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取历史价格失败: {ticker}",
                    extra={"error": str(e)},
                )
            return {"error": str(e), "component_type": "kline_chart"}
