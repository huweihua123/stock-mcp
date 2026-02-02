# src/server/domain/services/chip_service.py
"""筹码分布分析服务 (Chip Distribution Analysis Service)

基于历史成交量和价格数据，估算当前筹码分布情况。
返回结构化 JSON 数据，供前端可视化组件渲染。

筹码分布的核心逻辑：
1. 获取过去 N 个交易日的成交量和成交均价
2. 假设每日成交量代表在该价格区间换手的筹码
3. 按时间衰减计算当前仍持有的筹码（越久远的成交，换手概率越高）
4. 按价格区间聚合，得到筹码分布图
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.server.utils.logger import logger


class ChipService:
    """筹码分布分析服务"""

    def __init__(self, adapter_manager, cache):
        """
        Args:
            adapter_manager: 数据适配器管理器
            cache: Redis 缓存实例
        """
        self.adapter_manager = adapter_manager
        self.cache = cache
        self.logger = logger

    async def get_chip_distribution(
        self, symbol: str, period_days: int = 120, price_bins: int = 50
    ) -> Dict[str, Any]:
        """
        计算筹码分布

        Args:
            symbol: 股票代码
            period_days: 回溯天数（越长越准确，但计算量越大）
            price_bins: 价格区间数量

        Returns:
            {
                "symbol": "600519.SH",
                "component_type": "chip_distribution",
                "current_price": 1800.0,
                "data": {
                    "price_levels": [1700, 1710, 1720, ...],  # 价格区间中点
                    "chip_percent": [0.05, 0.12, 0.08, ...],  # 各区间筹码占比
                    "profit_chip": [0.05, 0.12, ...],         # 获利筹码（价格 < 现价）
                    "loss_chip": [0.0, 0.0, 0.08, ...]        # 套牢筹码（价格 > 现价）
                },
                "summary": {
                    "profit_ratio": 0.65,           # 获利盘占比
                    "avg_cost": 1650.5,             # 平均成本
                    "concentration_90": 150.0,      # 90%筹码集中度（价格区间）
                    "main_peak_price": 1720.0       # 主峰价格
                }
            }
        """
        cache_key = f"chip_dist:{symbol}:{period_days}"
        cached = await self.cache.get(cache_key)
        if cached:
            self.logger.info(f"Chip distribution cache hit for {symbol}")
            return cached

        try:
            # 1. 获取历史价格数据
            end_date = datetime.now()
            start_date = end_date - timedelta(days=period_days + 30)

            prices = await self.adapter_manager.get_historical_prices(
                symbol, start_date, end_date, "1d"
            )

            if not prices or len(prices) < 20:
                return {
                    "error": f"Insufficient price data for {symbol}",
                    "symbol": symbol,
                    "component_type": "chip_distribution",
                }

            # 2. 转换为 DataFrame
            df = pd.DataFrame([p.to_dict() for p in prices])
            df = df.rename(
                columns={
                    "close_price": "close",
                    "high_price": "high",
                    "low_price": "low",
                    "open_price": "open",
                }
            )

            for col in ["close", "high", "low", "open", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=["close", "volume"])
            df = df.sort_values("timestamp").tail(period_days)

            if df.empty:
                return {"error": "No valid price data", "symbol": symbol}

            # 3. 计算筹码分布
            current_price = float(df["close"].iloc[-1])
            price_min = float(df["low"].min()) * 0.95
            price_max = float(df["high"].max()) * 1.05

            # 生成价格区间
            price_edges = np.linspace(price_min, price_max, price_bins + 1)
            price_centers = (price_edges[:-1] + price_edges[1:]) / 2

            # 初始化筹码分布
            chip_distribution = np.zeros(price_bins)

            # 4. 基于成交量和价格计算筹码
            # 使用时间衰减因子：越近的成交，保留的筹码越多
            total_days = len(df)

            for idx, (_, row) in enumerate(df.iterrows()):
                # 时间衰减因子 (0.5 ~ 1.0)，越近期权重越高
                decay_factor = 0.5 + 0.5 * (idx / total_days)

                # 假设当日成交均匀分布在 low ~ high 之间
                day_low = float(row["low"])
                day_high = float(row["high"])
                day_volume = float(row["volume"]) * decay_factor

                # 找到价格落入的区间
                for i in range(price_bins):
                    bin_low = price_edges[i]
                    bin_high = price_edges[i + 1]

                    # 计算该区间与当日价格范围的重叠比例
                    overlap_low = max(bin_low, day_low)
                    overlap_high = min(bin_high, day_high)

                    if overlap_high > overlap_low:
                        overlap_ratio = (overlap_high - overlap_low) / (
                            day_high - day_low + 0.001
                        )
                        chip_distribution[i] += day_volume * overlap_ratio

            # 5. 归一化
            total_chips = chip_distribution.sum()
            if total_chips > 0:
                chip_percent = chip_distribution / total_chips
            else:
                chip_percent = chip_distribution

            # 6. 区分获利盘和套牢盘
            profit_chip = np.where(price_centers < current_price, chip_percent, 0)
            loss_chip = np.where(price_centers >= current_price, chip_percent, 0)

            # 7. 计算汇总指标
            profit_ratio = float(profit_chip.sum())
            avg_cost = float(np.average(price_centers, weights=chip_percent + 1e-10))

            # 找主峰（筹码最集中的价格）
            main_peak_idx = np.argmax(chip_percent)
            main_peak_price = float(price_centers[main_peak_idx])

            # 支撑/压力位：按价格排序的累计占比 15% / 85%
            cum = np.cumsum(chip_percent)
            support_price = float(price_centers[np.searchsorted(cum, 0.15, side="left")])
            resistance_price = float(price_centers[np.searchsorted(cum, 0.85, side="left")])

            # 平盘占比：靠近现价 +/-1% 的筹码
            band = current_price * 0.01
            flat_mask = (price_centers >= current_price - band) & (price_centers <= current_price + band)
            flat_ratio = float(chip_percent[flat_mask].sum())

            # 90% 筹码集中度
            sorted_indices = np.argsort(chip_percent)[::-1]
            cumsum = 0
            concentration_prices = []
            for idx in sorted_indices:
                cumsum += chip_percent[idx]
                concentration_prices.append(price_centers[idx])
                if cumsum >= 0.9:
                    break
            concentration_90 = (
                max(concentration_prices) - min(concentration_prices)
                if concentration_prices
                else 0
            )

            trade_ts = df["timestamp"].iloc[-1]
            trade_date = trade_ts.strftime("%Y%m%d") if hasattr(trade_ts, "strftime") else None
            distribution = [
                {"price": round(p, 2), "percentage": round(float(v), 4)}
                for p, v in zip(price_centers.tolist(), chip_percent.tolist())
            ]

            result = {
                "symbol": symbol,
                "component_type": "chip_distribution",
                "current_price": round(current_price, 2),
                "trade_date": trade_date,
                "chip_trade_date": trade_date,
                "price_trade_date": trade_date,
                "avg_cost": round(avg_cost, 2),
                "support_price": round(support_price, 2),
                "resistance_price": round(resistance_price, 2),
                "profit_ratio": round(profit_ratio, 4),
                "loss_ratio": round(1 - profit_ratio, 4),
                "flat_ratio": round(flat_ratio, 4),
                "distribution": distribution,
                "data": {
                    "price_levels": [round(p, 2) for p in price_centers.tolist()],
                    "chip_percent": [round(p, 4) for p in chip_percent.tolist()],
                    "profit_chip": [round(p, 4) for p in profit_chip.tolist()],
                    "loss_chip": [round(p, 4) for p in loss_chip.tolist()],
                },
                "summary": {
                    "profit_ratio": round(profit_ratio, 4),
                    "avg_cost": round(avg_cost, 2),
                    "concentration_90": round(concentration_90, 2),
                    "main_peak_price": round(main_peak_price, 2),
                    "period_days": len(df),
                },
            }

            # 缓存 1 小时
            await self.cache.set(cache_key, result, ttl=3600)
            self.logger.info(f"Chip distribution calculated for {symbol}")
            return result

        except Exception as e:
            self.logger.error(
                f"Failed to calculate chip distribution for {symbol}: {e}",
                exc_info=True,
            )
            return {
                "error": str(e),
                "symbol": symbol,
                "component_type": "chip_distribution",
            }

    async def get_winner_rate(self, symbol: str) -> Dict[str, Any]:
        """
        计算胜率（获利盘比例）的简化版本

        基于最近的筹码分布，快速计算当前价格下的获利盘比例。
        """
        result = await self.get_chip_distribution(symbol, period_days=60, price_bins=30)

        if "error" in result:
            return result

        return {
            "symbol": symbol,
            "current_price": result["current_price"],
            "profit_ratio": result["summary"]["profit_ratio"],
            "avg_cost": result["summary"]["avg_cost"],
        }
