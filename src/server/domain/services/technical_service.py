# src/server/domain/services/technical_service.py
"""Technical analysis service.
Calculates technical indicators and generates trading signals.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.server.utils.logger import logger


class TechnicalService:
    """Technical analysis service."""

    def __init__(self, provider_facade):
        """Initialize the service with the runtime provider facade."""
        self.provider_facade = provider_facade
        logger.info("Technical analysis service initialized")

    async def _get_price_data(
        self, symbol: str, period: str = "90d", interval: str = "1d"
    ) -> Optional[pd.DataFrame]:
        """Fetch price data and convert to DataFrame."""
        try:
            if not self.provider_facade:
                logger.error("Provider facade not initialized")
                return None

            # Parse period to days
            days = 90
            if period.endswith("d"):
                days = int(period[:-1])
            elif period.endswith("m"):
                days = int(period[:-1]) * 30
            elif period.endswith("y"):
                days = int(period[:-1]) * 365

            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)

            prices = await self.provider_facade.get_historical_prices(
                symbol, start_date, end_date, interval
            )

            if not prices:
                logger.warning(f"No historical data found for {symbol}")
                return None

            # Convert to DataFrame
            data = [p.to_dict() for p in prices]
            df = pd.DataFrame(data)

            # Rename columns to match technical analysis expectations if needed
            # AssetPrice dict keys: ticker, price, currency, timestamp, volume, open_price, high_price, low_price, close_price, ...
            # We need: open, high, low, close, volume, date (index)

            rename_map = {
                "open_price": "open",
                "high_price": "high",
                "low_price": "low",
                "close_price": "close",
                "timestamp": "date",
            }
            df = df.rename(columns=rename_map)

            # Ensure numeric types
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col])

            # Set index
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")

            df = df.sort_index()
            return df

        except Exception as e:
            logger.error(f"Failed to get price data for {symbol}: {e}")
            return None

    def _calculate_sma(self, data: pd.DataFrame, period: int) -> pd.Series:
        return data["close"].rolling(window=period).mean()

    def _calculate_ema(self, data: pd.DataFrame, period: int) -> pd.Series:
        return data["close"].ewm(span=period, adjust=False).mean()

    def _calculate_rsi(self, data: pd.DataFrame, period: int = 14) -> pd.Series:
        delta = data["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def _calculate_macd(
        self,
        data: pd.DataFrame,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        ema_fast = data["close"].ewm(span=fast_period, adjust=False).mean()
        ema_slow = data["close"].ewm(span=slow_period, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _calculate_bollinger_bands(
        self, data: pd.DataFrame, period: int = 20, std_dev: float = 2.0
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        middle_band = data["close"].rolling(window=period).mean()
        std = data["close"].rolling(window=period).std()
        upper_band = middle_band + (std * std_dev)
        lower_band = middle_band - (std * std_dev)
        return upper_band, middle_band, lower_band

    def _calculate_stochastic(
        self, data: pd.DataFrame, k_period: int = 14, d_period: int = 3
    ) -> Tuple[pd.Series, pd.Series]:
        low_min = data["low"].rolling(window=k_period).min()
        high_max = data["high"].rolling(window=k_period).max()
        k_line = 100 * ((data["close"] - low_min) / (high_max - low_min))
        d_line = k_line.rolling(window=d_period).mean()
        return k_line, d_line

    def _calculate_atr(self, data: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = data["high"] - data["low"]
        high_close = np.abs(data["high"] - data["close"].shift())
        low_close = np.abs(data["low"] - data["close"].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        return true_range.rolling(window=period).mean()

    async def calculate_indicators(
        self, symbol: str, period: str = "90d", interval: str = "1d", limit: int | None = None
    ) -> Dict[str, Any]:
        """Calculate technical indicators and return structured data (Time Series)."""
        try:
            # Parse requested period to start/end dates
            days = 90
            if limit is None:
                if period.endswith("d"):
                    days = int(period[:-1])
                elif period.endswith("m"):
                    days = int(period[:-1]) * 30
                elif period.endswith("y"):
                    days = int(period[:-1]) * 365
            else:
                # limit is in trading days; use it to size the display window
                days = max(1, int(limit))

            end_date = datetime.now()
            # Warm-up window: ensure indicators (e.g., MA60/MACD) are stable
            warmup_days = max(120, days)
            start_date = end_date - timedelta(days=days + warmup_days)
            logger.info(
                "TechnicalService.calculate_indicators request",
                symbol=symbol,
                period=period,
                interval=interval,
                limit=limit,
                days=days,
                warmup_days=warmup_days,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
            )

            result = await self.provider_facade.get_technical_indicators(
                ticker=symbol,
                indicators=["MA", "MACD", "KDJ", "RSI", "VOL"],
                period="daily",  # Adapter currently only supports daily logic effectively
                start_date=start_date,
                end_date=end_date,
            )

            if isinstance(result, dict) and result.get("error"):
                logger.warning(
                    "TechnicalService.calculate_indicators upstream error",
                    symbol=symbol,
                    error=result.get("error"),
                    source=result.get("source"),
                )
            else:
                rows = result.get("rows") if isinstance(result, dict) else None
                logger.info(
                    "TechnicalService.calculate_indicators upstream ok",
                    symbol=symbol,
                    source=result.get("source") if isinstance(result, dict) else None,
                    ts_code=result.get("ts_code") if isinstance(result, dict) else None,
                    rows_count=len(rows) if isinstance(rows, list) else 0,
                    dates_count=len(result.get("dates", []))
                    if isinstance(result, dict) and isinstance(result.get("dates"), list)
                    else 0,
                )

            # Trim to requested window (keep last `days` calendar days)
            if "error" not in result:
                if limit is not None:
                    # Trim to last N trading days
                    start_idx = max(0, len(result.get("dates", [])) - days)
                else:
                    dates = result.get("dates", [])
                    if dates:
                        cutoff = (end_date - timedelta(days=days)).date()
                        start_idx = 0
                        for i, d in enumerate(dates):
                            try:
                                if datetime.strptime(d, "%Y-%m-%d").date() >= cutoff:
                                    start_idx = i
                                    break
                            except Exception:
                                start_idx = 0
                                break
                    else:
                        start_idx = 0

                if start_idx > 0:
                    # Slice base series
                    result["dates"] = result.get("dates", [])[start_idx:]
                    result["close"] = result.get("close", [])[start_idx:]

                    # Slice indicator series
                    indicators = result.get("indicators", {})
                    ma = indicators.get("ma", {})
                    for k, v in list(ma.items()):
                        if isinstance(v, list):
                            ma[k] = v[start_idx:]
                    macd = indicators.get("macd", {})
                    for k, v in list(macd.items()):
                        if isinstance(v, list):
                            macd[k] = v[start_idx:]
                    kdj = indicators.get("kdj", {})
                    for k, v in list(kdj.items()):
                        if isinstance(v, list):
                            kdj[k] = v[start_idx:]
                    rsi = indicators.get("rsi")
                    if isinstance(rsi, list):
                        indicators["rsi"] = rsi[start_idx:]
                    vol = indicators.get("vol", {})
                    for k, v in list(vol.items()):
                        if isinstance(v, list):
                            vol[k] = v[start_idx:]

                    # Slice rows if present
                    rows = result.get("rows")
                    if isinstance(rows, list):
                        result["rows"] = rows[start_idx:]

            # Preserve renderer hints for downstream resource descriptors.
            if "error" not in result:
                result["variant"] = "technical_indicators"
                result["symbol"] = symbol
                result["title"] = f"技术指标分析: {symbol}"

            return result

        except Exception as e:
            logger.error(f"Calculate technical indicators failed: {e}", exc_info=True)
            return {"error": str(e)}

    async def generate_trading_signal(
        self, symbol: str, period: str = "90d", interval: str = "1d"
    ) -> Dict[str, Any]:
        """Generate trading signals based on technical indicators.

        Strategy: Moving Average Crossover (Golden Cross / Death Cross)
        """
        try:
            # 1. Fetch sufficient data (at least 60 days for SMA50 calculation)
            fetch_period = "100d"  # Ensure enough data for SMA50

            data = await self._get_price_data(symbol, fetch_period, interval)
            if data is None or data.empty or len(data) < 50:
                return {
                    "symbol": symbol,
                    "signal": "neutral",
                    "reason": "Insufficient data for analysis (need > 50 days)",
                }

            # 2. Calculate Indicators
            sma_20 = self._calculate_sma(data, 20)
            sma_50 = self._calculate_sma(data, 50)
            rsi = self._calculate_rsi(data, 14)

            # Get latest and previous values
            current_sma20 = sma_20.iloc[-1]
            current_sma50 = sma_50.iloc[-1]
            prev_sma20 = sma_20.iloc[-2]
            prev_sma50 = sma_50.iloc[-2]
            current_rsi = rsi.iloc[-1]
            current_price = data["close"].iloc[-1]

            # 3. Determine Signal
            signal = "neutral"
            reason = []
            confidence = 0.0

            # Golden Cross: SMA20 crosses above SMA50
            if prev_sma20 <= prev_sma50 and current_sma20 > current_sma50:
                signal = "buy"
                reason.append("Golden Cross: SMA20 crossed above SMA50")
                confidence += 0.6

            # Death Cross: SMA20 crosses below SMA50
            elif prev_sma20 >= prev_sma50 and current_sma20 < current_sma50:
                signal = "sell"
                reason.append("Death Cross: SMA20 crossed below SMA50")
                confidence += 0.6

            # Trend Confirmation
            if current_sma20 > current_sma50:
                if signal == "neutral":
                    reason.append("Bullish Trend: SMA20 > SMA50")
                confidence += 0.2
            elif current_sma20 < current_sma50:
                if signal == "neutral":
                    reason.append("Bearish Trend: SMA20 < SMA50")
                confidence += 0.2

            # RSI Filter
            if current_rsi < 30:
                reason.append(f"RSI Oversold ({current_rsi:.1f})")
                if signal == "buy":
                    confidence += 0.2
            elif current_rsi > 70:
                reason.append(f"RSI Overbought ({current_rsi:.1f})")
                if signal == "sell":
                    confidence += 0.2

            return {
                "symbol": symbol,
                "signal": signal,
                "confidence": min(confidence, 1.0),
                "timestamp": datetime.now().isoformat(),
                "price": float(current_price),
                "indicators": {
                    "sma_20": float(current_sma20),
                    "sma_50": float(current_sma50),
                    "rsi": float(current_rsi),
                },
                "reasons": reason,
            }

        except Exception as e:
            logger.error(f"Generate trading signal failed: {e}", exc_info=True)
            return {"error": str(e)}

    async def calculate_support_resistance(
        self, symbol: str, period: str = "90d"
    ) -> Dict[str, Any]:
        """Calculate support and resistance levels."""
        try:
            data = await self._get_price_data(symbol, period)
            if data is None or data.empty:
                return {"error": "No data"}

            # Simple pivot point calculation based on recent highs/lows
            recent_high = data["high"].max()
            recent_low = data["low"].min()
            current = data["close"].iloc[-1]

            levels = []
            # Add recent high/low as levels
            levels.append(
                {
                    "price": float(recent_high),
                    "type": "resistance",
                    "strength": "strong",
                }
            )
            levels.append(
                {"price": float(recent_low), "type": "support", "strength": "strong"}
            )

            return {"symbol": symbol, "current_price": float(current), "levels": levels}
        except Exception as e:
            return {"error": str(e)}

    async def analyze_price_patterns(
        self, symbol: str, period: str = "90d"
    ) -> Dict[str, Any]:
        """Analyze price patterns (Candlestick patterns)."""
        try:
            data = await self._get_price_data(symbol, period)
            if data is None or data.empty:
                return {"error": "No data"}

            patterns = []
            latest = data.iloc[-1]
            prev = data.iloc[-2]

            # 1. Doji (Open approx equal to Close)
            body_size = abs(latest["close"] - latest["open"])
            total_range = latest["high"] - latest["low"]
            if total_range > 0 and (body_size / total_range) < 0.1:
                patterns.append(
                    {
                        "name": "Doji",
                        "signal": "neutral",
                        "significance": "potential reversal",
                    }
                )

            # 2. Hammer (Small body, long lower shadow)
            lower_shadow = min(latest["open"], latest["close"]) - latest["low"]
            if (
                total_range > 0
                and body_size > 0
                and (lower_shadow / body_size) > 2
                and (latest["high"] - max(latest["open"], latest["close"])) < body_size
            ):
                patterns.append(
                    {
                        "name": "Hammer",
                        "signal": "buy",
                        "significance": "bullish reversal",
                    }
                )

            # 3. Bullish Engulfing
            if (
                prev["close"] < prev["open"]  # Previous red
                and latest["close"] > latest["open"]  # Current green
                and latest["open"] < prev["close"]
                and latest["close"] > prev["open"]
            ):
                patterns.append(
                    {
                        "name": "Bullish Engulfing",
                        "signal": "buy",
                        "significance": "strong bullish",
                    }
                )

            return {
                "symbol": symbol,
                "patterns": patterns,
                "last_candle": {
                    "open": float(latest["open"]),
                    "high": float(latest["high"]),
                    "low": float(latest["low"]),
                    "close": float(latest["close"]),
                },
            }
        except Exception as e:
            return {"error": str(e)}

    async def analyze_volume_profile(
        self, symbol: str, period: str = "90d"
    ) -> Dict[str, Any]:
        """Analyze volume profile (Simplified / Visible Range).

        Calculates volume distribution across price levels to identify
        high volume nodes (strong support/resistance) and low volume nodes.
        """
        try:
            data = await self._get_price_data(symbol, period)
            if data is None or data.empty:
                return {"error": "No data"}

            # 1. Determine Price Range
            min_price = data["low"].min()
            max_price = data["high"].max()
            price_range = max_price - min_price

            if price_range == 0:
                return {"error": "Price range is zero"}

            # 2. Create Bins (e.g., 24 bins)
            num_bins = 24
            bin_size = price_range / num_bins

            # Initialize bins
            # Each bin: {'min': float, 'max': float, 'volume': float}
            bins = []
            for i in range(num_bins):
                bins.append(
                    {
                        "min": min_price + (i * bin_size),
                        "max": min_price + ((i + 1) * bin_size),
                        "volume": 0.0,
                    }
                )

            # 3. Distribute Volume
            # We use Typical Price (H+L+C)/3 to attribute volume
            typical_prices = (data["high"] + data["low"] + data["close"]) / 3
            volumes = data["volume"]

            total_volume = 0.0

            for price, vol in zip(typical_prices, volumes):
                if pd.isna(price) or pd.isna(vol):
                    continue

                # Find bin index
                bin_idx = int((price - min_price) / bin_size)
                # Handle edge case where price == max_price
                if bin_idx >= num_bins:
                    bin_idx = num_bins - 1
                if bin_idx < 0:
                    bin_idx = 0

                bins[bin_idx]["volume"] += float(vol)
                total_volume += float(vol)

            # 4. Analyze Results
            # Find POC (Point of Control) - Price level with highest volume
            poc_bin = max(bins, key=lambda x: x["volume"])
            poc_price = (poc_bin["min"] + poc_bin["max"]) / 2

            # Calculate Value Area (e.g., 70% of volume)
            # Sort bins by volume descending
            sorted_bins = sorted(bins, key=lambda x: x["volume"], reverse=True)
            accumulated_vol = 0.0
            value_area_vol = total_volume * 0.70
            value_area_bins = []

            for b in sorted_bins:
                accumulated_vol += b["volume"]
                value_area_bins.append(b)
                if accumulated_vol >= value_area_vol:
                    break

            # Determine Value Area High (VAH) and Low (VAL)
            vah = max(b["max"] for b in value_area_bins)
            val = min(b["min"] for b in value_area_bins)

            # Current price relation
            current_price = float(data["close"].iloc[-1])
            status = "inside_value_area"
            if current_price > vah:
                status = "above_value_area (bullish)"
            elif current_price < val:
                status = "below_value_area (bearish)"

            return {
                "symbol": symbol,
                "period": period,
                "poc_price": poc_price,  # Point of Control (Strongest Support/Resistance)
                "value_area": {"high": vah, "low": val, "coverage": "70%"},
                "current_price": current_price,
                "status": status,
                "volume_profile": bins,  # Full distribution for visualization
            }

        except Exception as e:
            logger.error(f"Volume profile analysis failed: {e}", exc_info=True)
            return {"error": str(e)}
