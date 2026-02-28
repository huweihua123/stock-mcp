# src/server/mcp/tools/technical_tools.py
"""MCP tools for technical analysis.
Provides technical indicators calculation and analysis.
Returns structured data (JSON).

Active Tools (1):
  - get_technical_indicators: 获取技术指标 (RSI, MACD, SMA, EMA, BB, KDJ, ATR)

Disabled Tools:
  - analyze_price_patterns: 🔇 当前策略聚焦核心指标
  - calculate_support_resistance: 🔇 当前策略聚焦核心指标
  - analyze_volume_profile: 🔇 当前策略聚焦核心指标
  - generate_trading_signal: 🔇 交易信号应由 LLM 基于技术指标自行判断
"""

from typing import Any, Dict

from fastmcp import FastMCP, Context

from src.server.utils.logger import logger
from src.server.core.use_cases import technical as technical_use_cases
from src.server.core.dependencies import Container
from src.server.mcp.tools.artifact_utils import (
    create_artifact_envelope,
    create_artifact_response,
    create_symbol_error_response,
)
from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.domain.symbols import to_ts_code


# ============================================================
# 核心业务逻辑实现 (可被 MCP 和 FastAPI 共享)
# ============================================================


# ============================================================
# MCP 工具注册 (保持原有接口不变)
# ============================================================


def register_technical_tools(mcp: FastMCP):
    """Register technical analysis tools."""

    async def _resolve_ts_code(raw_symbol: str) -> str:
        resolved = await Container.market_gateway().resolve_ticker(raw_symbol)
        return to_ts_code(resolved)



    async def calculate_technical_indicators(
        symbol: str,
        period: str = "30d",
        interval: str = "1d",
        limit: int | None = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Calculate technical indicators.

        Supported indicators:
        - SMA (20, 50, 200)
        - EMA (12, 26)
        - RSI (14)
        - MACD
        - Bollinger Bands
        - KDJ
        - ATR

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            period: Data period (30d, 90d, 1y)
            interval: Data interval (1d)
            limit: 返回最近N个交易日(优先于period)
            ctx: FastMCP Context for logging

        Returns:
            ArtifactEnvelope containing calculated indicators
        """
        # 记录工具调用
        if ctx:
            await ctx.info(
                f"🔧 计算技术指标: {symbol}",
                extra={
                    "symbol": symbol,
                    "period": period,
                    "interval": interval,
                    "limit": limit,
                },
            )

        try:
            logger.info(
                "MCP tool called: calculate_technical_indicators",
                symbol=symbol,
                period=period,
                interval=interval,
                limit=limit,
            )
            result = await technical_use_cases.calculate_technical_indicators(
                symbol, period, interval, limit
            )

            if isinstance(result, dict) and result.get("error"):
                err = str(result.get("error"))
                logger.warning(
                    "calculate_technical_indicators returned error",
                    symbol=symbol,
                    period=period,
                    interval=interval,
                    limit=limit,
                    error=err,
                )
                if ctx:
                    await ctx.error(
                        f"❌ 技术指标计算失败: {symbol}",
                        extra={
                            "error": err,
                            "period": period,
                            "interval": interval,
                            "limit": limit,
                        },
                    )
                return {"error": err}

            result["component_type"] = "technical_indicators"

            # 构建摘要 - 只基于 rows(竞品格式)
            rows = result.get("rows") if isinstance(result.get("rows"), list) else []
            logger.info(
                "calculate_technical_indicators result stats",
                symbol=symbol,
                ts_code=result.get("ts_code"),
                rows_count=len(rows),
                has_dates=isinstance(result.get("dates"), list),
                dates_count=len(result.get("dates", [])) if isinstance(result.get("dates"), list) else 0,
            )
            rsi = 0.0
            has_rsi = False
            macd_signal = "中性"
            current_price = 0.0
            has_current_price = False
            if rows:
                # RSI: last non-null
                for row in reversed(rows):
                    val = row.get("RSI")
                    if val is not None:
                        rsi = float(val)
                        has_rsi = True
                        break
                # MACD: diff vs signal
                for row in reversed(rows):
                    diff = row.get("MACD")
                    dea = row.get("MACD_signal")
                    if diff is not None and dea is not None:
                        macd_signal = "多头" if diff > dea else "空头"
                        break
                # price
                last_close = rows[-1].get("close")
                current_price = float(last_close) if last_close is not None else 0.0
                has_current_price = last_close is not None

            # 判断 RSI 状态
            if not has_rsi:
                rsi_status = "未知"
            elif rsi > 70:
                rsi_status = "超买"
            elif rsi < 30:
                rsi_status = "超卖"
            else:
                rsi_status = "中性"

            # 记录成功
            if ctx:
                await ctx.info(
                    f"✅ 技术指标计算完成: {symbol}",
                    extra={
                        "rsi": rsi if has_rsi else None,
                        "rsi_status": rsi_status,
                        "macd_signal": macd_signal,
                    },
                )

            # 对齐竞品: 只返回行记录列表
            content = rows

            ts_code = result.get("ts_code") or await _resolve_ts_code(symbol)
            name = f"Technical Indicators: {ts_code}"
            start_date = rows[0].get("trade_date") if rows else None
            end_date = rows[-1].get("trade_date") if rows else None
            range_text = (
                f"{start_date}~{end_date}" if start_date and end_date else "区间未知"
            )
            rsi_text = f"{rsi:.1f}({rsi_status})" if has_rsi else "N/A"
            price_text = f"{current_price:.2f}" if has_current_price else "N/A"
            metadata = (
                f"{ts_code} 技术面: 区间{range_text}, 收盘{price_text}, "
                f"RSI={rsi_text}, MACD={macd_signal}, 样本{len(rows)}日"
            )

            artifact = create_artifact_envelope(
                component_type="technical_indicators",
                name=name,
                content=content,
                description=metadata,
                metadata={"type": "technical_indicators", "ts_code": ts_code},
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=metadata, artifact=artifact)
        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="technical_indicators", name=f"{symbol} 技术指标"
            )
        except Exception as e:
            error_msg = f"MCP tool error in calculate_technical_indicators: {e}"
            logger.error(error_msg, exc_info=True)

            # 记录错误到客户端
            if ctx:
                await ctx.error(
                    f"❌ 技术指标计算失败: {symbol}", extra={"error": str(e)}
                )

            return {"error": str(e)}

    @mcp.tool(tags={"technical"})
    async def get_technical_indicators(
        ts_code: str, limit: int = 60, ctx: Context = None
    ) -> Dict[str, Any]:
        """Get technical indicators in competitor-compatible format.

        Args:
            ts_code: Tushare ts_code (e.g. 600519.SH)
            limit: 返回最近N个交易日
            ctx: FastMCP Context for logging

        Returns:
            ArtifactEnvelope containing indicator rows
        """
        symbol = ts_code
        if ctx:
            await ctx.info(
                f"🔧 获取技术指标: {ts_code}",
                extra={"ts_code": ts_code, "symbol": symbol, "limit": limit},
            )

        try:
            result = await technical_use_cases.calculate_technical_indicators(
                symbol, "30d", "1d", limit
            )
            result["component_type"] = "technical_indicators"
            result["ts_code"] = to_ts_code(symbol) if "." in symbol else await _resolve_ts_code(symbol)

            content = result.get("rows") if isinstance(result.get("rows"), list) else result

            start_date = None
            end_date = None
            if isinstance(content, list) and content:
                start_date = content[0].get("trade_date")
                end_date = content[-1].get("trade_date")

            latest_rsi = None
            latest_close = None
            macd_signal = "中性"
            if isinstance(content, list) and content:
                for row in reversed(content):
                    val = row.get("RSI")
                    if val is not None:
                        latest_rsi = float(val)
                        break
                for row in reversed(content):
                    close_val = row.get("close")
                    if close_val is not None:
                        latest_close = float(close_val)
                        break
                for row in reversed(content):
                    diff = row.get("MACD")
                    dea = row.get("MACD_signal")
                    if diff is not None and dea is not None:
                        macd_signal = "多头" if diff > dea else "空头"
                        break

            range_text = (
                f"{start_date}~{end_date}" if start_date and end_date else "区间未知"
            )
            if latest_rsi is None:
                rsi_text = "N/A"
            elif latest_rsi > 70:
                rsi_text = f"{latest_rsi:.1f}(超买)"
            elif latest_rsi < 30:
                rsi_text = f"{latest_rsi:.1f}(超卖)"
            else:
                rsi_text = f"{latest_rsi:.1f}(中性)"
            price_text = f"{latest_close:.2f}" if latest_close is not None else "N/A"
            description = (
                f"{result['ts_code']} 技术面: 区间{range_text}, 收盘{price_text}, "
                f"RSI={rsi_text}, MACD={macd_signal}, 样本{len(content) if isinstance(content, list) else 0}日"
            )

            artifact = create_artifact_envelope(
                component_type="technical_indicators",
                name=f"Technical Indicators: {result['ts_code']}",
                content=content,
                description=description,
                metadata={"type": "technical_indicators", "ts_code": result["ts_code"]},
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=description, artifact=artifact)
        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ts_code}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="technical_indicators", name=f"{ts_code} 技术指标"
            )
        except Exception as e:
            logger.error(f"Get technical indicators failed: {e}", exc_info=True)
            if ctx:
                await ctx.error(
                    f"❌ 获取技术指标失败: {ts_code}", extra={"error": str(e)}
                )
            return {"error": str(e)}

    async def analyze_price_patterns(
        symbol: str, period: str = "90d", ctx: Context = None
    ) -> Dict[str, Any]:
        """Analyze price patterns.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            period: Analysis period (30d, 90d, 1y)
            ctx: FastMCP Context for logging

        Returns:
            Dictionary containing pattern analysis
        """
        if ctx:
            await ctx.info(
                f"🔧 分析价格形态: {symbol}", extra={"symbol": symbol, "period": period}
            )

        try:
            logger.info(
                "MCP tool called: analyze_price_patterns", symbol=symbol, period=period
            )
            result = await technical_use_cases.analyze_price_patterns(
                symbol=symbol, period=period
            )
            result["component_type"] = "price_patterns"

            # 构建描述
            patterns = result.get("patterns", [])
            pattern_names = [p.get("name", "未知形态") for p in patterns[:3]]
            patterns_str = ", ".join(pattern_names) if pattern_names else "无明显形态"
            description = f"{symbol}价格形态分析: 发现{len(patterns)}个形态 ({patterns_str})"

            if ctx:
                await ctx.info(f"✅ 价格形态分析完成: {symbol}")

            artifact = create_artifact_envelope(
                component_type="price_patterns",
                name=f"{symbol} 价格形态",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="price_patterns", name=f"{symbol} 价格形态"
            )
        except Exception as e:
            logger.error(f"Analyze price patterns failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 价格形态分析失败: {symbol}", extra={"error": str(e)}
                )
            return {"error": str(e)}

    async def calculate_support_resistance(
        symbol: str, period: str = "90d", ctx: Context = None
    ) -> Dict[str, Any]:
        """Calculate support and resistance levels.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            period: Analysis period (30d, 90d, 1y)
            ctx: FastMCP Context for logging

        Returns:
            Dictionary containing support/resistance levels
        """
        if ctx:
            await ctx.info(
                f"🔧 计算支撑/阻力位: {symbol}",
                extra={"symbol": symbol, "period": period},
            )

        try:
            logger.info(
                "MCP tool called: calculate_support_resistance",
                symbol=symbol,
                period=period,
            )
            result = await technical_use_cases.calculate_support_resistance(
                symbol=symbol, period=period
            )
            result["component_type"] = "support_resistance"

            # 构建描述
            supports = result.get("supports", [])
            resistances = result.get("resistances", [])
            s_levels = [f"{s.get('price', 0):.2f}" for s in supports[:2]]
            r_levels = [f"{r.get('price', 0):.2f}" for r in resistances[:2]]
            
            description = f"{symbol}关键价位: 支撑{', '.join(s_levels)}, 阻力{', '.join(r_levels)}"

            if ctx:
                await ctx.info(f"✅ 支撑/阻力位计算完成: {symbol}")

            artifact = create_artifact_envelope(
                component_type="support_resistance",
                name=f"{symbol} 支撑/阻力位",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="support_resistance", name=f"{symbol} 支撑/阻力位"
            )
        except Exception as e:
            logger.error(f"Calculate support/resistance failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 支撑/阻力位计算失败: {symbol}", extra={"error": str(e)}
                )
            return {"error": str(e)}

    async def analyze_volume_profile(
        symbol: str, period: str = "90d", ctx: Context = None
    ) -> Dict[str, Any]:
        """Analyze volume profile.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            period: Analysis period (30d, 90d, 1y)
            ctx: FastMCP Context for logging

        Returns:
            Dictionary containing volume analysis
        """
        if ctx:
            await ctx.info(
                f"🔧 分析成交量分布: {symbol}",
                extra={"symbol": symbol, "period": period},
            )

        try:
            logger.info(
                "MCP tool called: analyze_volume_profile", symbol=symbol, period=period
            )
            result = await technical_use_cases.analyze_volume_profile(
                symbol=symbol, period=period
            )
            result["component_type"] = "volume_profile"

            # 构建描述
            poc = result.get("poc_price", 0)
            va_high = result.get("value_area_high", 0)
            va_low = result.get("value_area_low", 0)
            description = f"{symbol}成交量分布: POC={poc:.2f}, VA=[{va_low:.2f}, {va_high:.2f}]"

            if ctx:
                await ctx.info(f"✅ 成交量分布分析完成: {symbol}")

            artifact = create_artifact_envelope(
                component_type="volume_profile",
                name=f"{symbol} 成交量分布",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="volume_profile", name=f"{symbol} 成交量分布"
            )
        except Exception as e:
            logger.error(f"Analyze volume profile failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 成交量分布分析失败: {symbol}", extra={"error": str(e)}
                )
            return {"error": str(e)}
