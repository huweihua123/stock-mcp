# src/server/domain/services/fundamental_service.py
"""FundamentalService – Complete fundamental analysis with financial ratios and scoring.

Returns structured data (JSON).
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from src.server.utils.logger import logger


class FinancialRatios:
    """Financial ratio calculator."""

    @staticmethod
    def calculate_valuation_ratios(data: Dict) -> Dict:
        """Calculate valuation ratios."""
        ratios = {}
        try:
            if "pe_ratio" in data and data["pe_ratio"]:
                ratios["pe_ratio"] = round(data["pe_ratio"], 2)
            else:
                market_cap = data.get("market_cap", 0)
                net_profit = data.get("net_profit", 0)
                ratios["pe_ratio"] = (
                    round(market_cap / net_profit, 2) if net_profit > 0 else None
                )

            if "pb_ratio" in data and data["pb_ratio"]:
                ratios["pb_ratio"] = round(data["pb_ratio"], 2)
            else:
                market_cap = data.get("market_cap", 0)
                net_assets = data.get("net_assets", 0)
                ratios["pb_ratio"] = (
                    round(market_cap / net_assets, 2) if net_assets > 0 else None
                )

            market_cap = data.get("market_cap", 0)
            revenue = data.get("revenue", 0)
            ratios["ps_ratio"] = round(market_cap / revenue, 2) if revenue > 0 else None

            dividend = data.get("dividend", 0)
            market_cap = data.get("market_cap", 0)
            ratios["dividend_yield"] = (
                round((dividend / market_cap) * 100, 2) if market_cap > 0 else None
            )
        except Exception as e:
            logger.warning(f"Error calculating valuation ratios: {e}")
        return ratios

    @staticmethod
    def calculate_profitability_ratios(data: Dict) -> Dict:
        """Calculate profitability ratios."""
        ratios = {}
        try:
            if "roe" in data and data["roe"]:
                ratios["roe"] = (
                    round(data["roe"] * 100, 2)
                    if data["roe"] < 1
                    else round(data["roe"], 2)
                )
            else:
                net_profit = data.get("net_profit", 0)
                net_assets = data.get("net_assets", 0)
                ratios["roe"] = (
                    round((net_profit / net_assets) * 100, 2)
                    if net_assets > 0
                    else None
                )

            if "roa" in data and data["roa"]:
                ratios["roa"] = (
                    round(data["roa"] * 100, 2)
                    if data["roa"] < 1
                    else round(data["roa"], 2)
                )
            else:
                net_profit = data.get("net_profit", 0)
                total_assets = data.get("total_assets", 0)
                ratios["roa"] = (
                    round((net_profit / total_assets) * 100, 2)
                    if total_assets > 0
                    else None
                )

            revenue = data.get("revenue", 0)
            gross_profit = data.get("gross_profit", 0)
            net_profit = data.get("net_profit", 0)
            operating_profit = data.get("operating_profit", 0)

            ratios["gross_margin"] = (
                round((gross_profit / revenue) * 100, 2) if revenue > 0 else None
            )
            ratios["net_margin"] = (
                round((net_profit / revenue) * 100, 2) if revenue > 0 else None
            )
            ratios["operating_margin"] = (
                round((operating_profit / revenue) * 100, 2) if revenue > 0 else None
            )
        except Exception as e:
            logger.warning(f"Error calculating profitability ratios: {e}")
        return ratios

    @staticmethod
    def calculate_solvency_ratios(data: Dict) -> Dict:
        """Calculate solvency ratios."""
        ratios = {}
        try:
            current_assets = data.get("current_assets", 0)
            current_liabilities = data.get("current_liabilities", 0)
            inventory = data.get("inventory", 0)
            total_liabilities = data.get("total_liabilities", 0)
            total_assets = data.get("total_assets", 0)
            net_assets = data.get("net_assets", 0)

            ratios["current_ratio"] = (
                round(current_assets / current_liabilities, 2)
                if current_liabilities > 0
                else None
            )
            ratios["quick_ratio"] = (
                round((current_assets - inventory) / current_liabilities, 2)
                if current_liabilities > 0
                else None
            )
            ratios["debt_to_equity"] = (
                round(total_liabilities / net_assets, 2) if net_assets > 0 else None
            )
            ratios["asset_liability_ratio"] = (
                round((total_liabilities / total_assets) * 100, 2)
                if total_assets > 0
                else None
            )
        except Exception as e:
            logger.warning(f"Error calculating solvency ratios: {e}")
        return ratios

    @staticmethod
    def calculate_growth_ratios(
        current_data: Dict, previous_data: Optional[Dict] = None
    ) -> Dict:
        """Calculate growth ratios."""
        ratios = {}
        if not previous_data:
            return {
                "revenue_growth": None,
                "earnings_growth": None,
                "assets_growth": None,
            }

        try:
            curr_revenue = current_data.get("revenue", 0)
            prev_revenue = previous_data.get("revenue", 0)
            curr_profit = current_data.get("net_profit", 0)
            prev_profit = previous_data.get("net_profit", 0)
            curr_assets = current_data.get("total_assets", 0)
            prev_assets = previous_data.get("total_assets", 0)

            ratios["revenue_growth"] = (
                round(((curr_revenue - prev_revenue) / prev_revenue) * 100, 2)
                if prev_revenue > 0
                else None
            )
            ratios["earnings_growth"] = (
                round(((curr_profit - prev_profit) / prev_profit) * 100, 2)
                if prev_profit > 0
                else None
            )
            ratios["assets_growth"] = (
                round(((curr_assets - prev_assets) / prev_assets) * 100, 2)
                if prev_assets > 0
                else None
            )
        except Exception as e:
            logger.warning(f"Error calculating growth ratios: {e}")
        return ratios

    @staticmethod
    def calculate_dupont_analysis(data: Dict) -> Dict:
        """Calculate Dupont Analysis components (ROE Breakdown)."""
        analysis = {
            "net_profit_margin": None,
            "asset_turnover": None,
            "equity_multiplier": None,
            "roe": None,
        }
        try:
            revenue = data.get("revenue", 0)
            net_profit = data.get("net_profit", 0)
            total_assets = data.get("total_assets", 0)
            net_assets = data.get("net_assets", 0)

            if revenue > 0 and total_assets > 0 and net_assets > 0:
                # 1. Net Profit Margin (Net Profit / Revenue)
                net_profit_margin = net_profit / revenue

                # 2. Asset Turnover (Revenue / Total Assets)
                asset_turnover = revenue / total_assets

                # 3. Equity Multiplier (Total Assets / Net Assets)
                equity_multiplier = total_assets / net_assets

                # ROE (Check consistency)
                roe = net_profit_margin * asset_turnover * equity_multiplier

                analysis = {
                    "net_profit_margin": round(net_profit_margin * 100, 2),  # %
                    "asset_turnover": round(asset_turnover, 4),  # times
                    "equity_multiplier": round(equity_multiplier, 2),  # times
                    "roe": round(roe * 100, 2),  # %
                }
        except Exception as e:
            logger.warning(f"Error calculating Dupont Analysis: {e}")
        return analysis


class FundamentalService:
    """Fundamental analysis service backed by the runtime provider facade."""

    def __init__(self, provider_facade, cache):
        self.provider_facade = provider_facade
        self.cache = cache
        self.logger = logger

    async def _fetch_financial_data(self, ticker: str) -> Dict[str, Any]:
        try:
            return await self.provider_facade.get_financials(ticker)
        except Exception as e:
            self.logger.error(f"Failed to fetch financial data for {ticker}: {e}")
            raise

    def _extract_financial_data(
        self,
        balance_df,
        income_df,
        cashflow_df,
        indicator_df,
        raw_info=None,
        market_metrics=None,
    ) -> Dict:
        """Extract key financial data from DataFrames or list of dicts."""
        data = {}
        try:
            # Helper to parse value
            def parse(val):
                if val is None or val == "" or val == "--":
                    return 0.0
                try:
                    if isinstance(val, str):
                        val = (
                            val.replace(",", "")
                            .replace("元", "")
                            .replace("万", "")
                            .strip()
                        )
                    return float(val)
                except:
                    return 0.0

            # Helper to check if data is empty
            def is_empty(df_or_list):
                if df_or_list is None:
                    return True
                if isinstance(df_or_list, list):
                    return len(df_or_list) == 0
                # DataFrame
                return df_or_list.empty if hasattr(df_or_list, "empty") else True

            # Helper to get latest record
            def get_latest(df_or_list):
                if isinstance(df_or_list, list):
                    return df_or_list[0] if len(df_or_list) > 0 else {}
                # DataFrame
                return df_or_list.iloc[0] if hasattr(df_or_list, "iloc") else {}

            # Balance Sheet
            if not is_empty(balance_df):
                latest = get_latest(balance_df)
                data["total_assets"] = parse(
                    latest.get("资产总计") or latest.get("total_assets", 0)
                )
                data["current_assets"] = parse(
                    latest.get("流动资产合计") or latest.get("current_assets", 0)
                )
                data["total_liabilities"] = parse(
                    latest.get("负债合计")
                    or latest.get("total_liab")
                    or latest.get("total_liabilities", 0)
                )
                data["current_liabilities"] = parse(
                    latest.get("流动负债合计") or latest.get("current_liabilities", 0)
                )
                data["net_assets"] = parse(
                    latest.get("股东权益合计")
                    or latest.get("total_hldr_eqy_exc_min_int")
                    or latest.get("net_assets", 0)
                )
                data["inventory"] = parse(
                    latest.get("存货") or latest.get("inventory", 0)
                )

            # Income Statement
            if not is_empty(income_df):
                latest = get_latest(income_df)
                data["revenue"] = parse(
                    latest.get("营业总收入") or latest.get("revenue", 0)
                )
                data["operating_profit"] = parse(
                    latest.get("营业利润") or latest.get("operate_profit", 0)
                )
                data["net_profit"] = parse(
                    latest.get("净利润")
                    or latest.get("n_income")
                    or latest.get("n_income_attr_p", 0)
                )

                # Calculate gross profit if not directly available
                if "gross_profit" not in latest:
                    revenue_val = parse(
                        latest.get("营业总收入") or latest.get("revenue", 0)
                    )
                    cost_val = parse(latest.get("营业总成本", 0))
                    data["gross_profit"] = revenue_val - cost_val if cost_val > 0 else 0
                else:
                    data["gross_profit"] = parse(latest.get("gross_profit", 0))

            # Financial Indicators
            if not is_empty(indicator_df):
                latest = get_latest(indicator_df)
                data["market_cap"] = parse(
                    latest.get("总市值") or latest.get("market_cap", 0)
                )
                data["dividend"] = parse(
                    latest.get("分红") or latest.get("dividend", 0)
                )

                # Additional ratios if available
                if "eps" in latest:
                    data["eps"] = parse(latest.get("eps", 0))
                if "roe" in latest:
                    data["roe"] = parse(latest.get("roe", 0))
                if "roa" in latest:
                    data["roa"] = parse(latest.get("roa", 0))
                if "grossprofit_margin" in latest:
                    data["gross_margin"] = parse(latest.get("grossprofit_margin", 0))
                if "debt_to_assets" in latest:
                    data["asset_liability_ratio"] = (
                        parse(latest.get("debt_to_assets", 0)) * 100
                    )
                if "current_ratio" in latest:
                    data["current_ratio"] = parse(latest.get("current_ratio", 0))

            # Market Metrics (from daily_basic)
            if not is_empty(market_metrics):
                latest = get_latest(market_metrics)
                # Tushare returns total_mv in 10k CNY usually, but let's check.
                # Actually daily_basic total_mv is in 10k.
                # But fundamental_service seems to expect raw numbers or handles units?
                # The parse function removes "万".
                # Let's assume standard units. Tushare daily_basic total_mv is in "ten thousands".
                # So 10000 means 100 million.
                # However, balance sheet items from Tushare are usually in raw currency (CNY).
                # We need to be careful with units.
                # Let's just pass raw for now and assume consistency or fix later if needed.
                # Actually, let's convert total_mv to raw if we know it's 10k.
                # Tushare docs: total_mv: 总市值 （万元）

                mv = parse(latest.get("total_mv", 0)) * 10000
                if mv > 0:
                    data["market_cap"] = mv

                if "pe_ttm" in latest:
                    data["pe_ratio"] = parse(latest.get("pe_ttm", 0))
                elif "pe" in latest:
                    data["pe_ratio"] = parse(latest.get("pe", 0))

                if "pb" in latest:
                    data["pb_ratio"] = parse(latest.get("pb", 0))
                if "ps_ttm" in latest:
                    data["ps_ratio"] = parse(latest.get("ps_ttm", 0))
                if "dv_ratio" in latest:
                    data["dividend_yield"] = parse(latest.get("dv_ratio", 0))

            elif raw_info:
                data["market_cap"] = parse(raw_info.get("marketCap", 0))
                data["dividend"] = parse(
                    raw_info.get("dividendRate", 0) or raw_info.get("dividendYield", 0)
                )
                if "peRatio" in raw_info:
                    data["pe_ratio"] = parse(raw_info.get("peRatio", 0))
                    data["pb_ratio"] = parse(raw_info.get("pbRatio", 0))
                    data["roe"] = parse(raw_info.get("roe", 0))
                    data["roa"] = parse(raw_info.get("roa", 0))
                    data["current_ratio"] = parse(raw_info.get("currentRatio", 0))
                    data["quick_ratio"] = parse(raw_info.get("quickRatio", 0))
                    data["debt_equity"] = parse(raw_info.get("debtEquity", 0))
                    data["revenue_growth"] = parse(raw_info.get("revenueGrowth", 0))
                    data["eps_growth"] = parse(raw_info.get("epsGrowth", 0))

        except Exception as e:
            self.logger.warning(f"Error extracting financial data: {e}")

        return data

    def _calculate_health_score(self, ratios: Dict) -> int:
        """Calculate financial health score (0-100)."""
        score = 0

        def safe_get(d, k, default=0):
            val = d.get(k)
            return val if val is not None else default

        # Profitability (30)
        p = ratios.get("profitability", {})
        roe = safe_get(p, "roe")
        if roe > 15:
            score += 15
        elif roe > 10:
            score += 10
        elif roe > 5:
            score += 5

        net_margin = safe_get(p, "net_margin")
        if net_margin > 20:
            score += 15
        elif net_margin > 10:
            score += 10
        elif net_margin > 5:
            score += 5

        # Solvency (30)
        s = ratios.get("solvency", {})
        current_ratio = safe_get(s, "current_ratio")
        if current_ratio > 2:
            score += 15
        elif current_ratio > 1.5:
            score += 10
        elif current_ratio > 1:
            score += 5

        al = safe_get(s, "asset_liability_ratio", 100)
        if al < 40:
            score += 15
        elif al < 60:
            score += 10
        elif al < 70:
            score += 5

        # Growth (20)
        g = ratios.get("growth", {})
        rev_growth = safe_get(g, "revenue_growth")
        if rev_growth > 20:
            score += 10
        elif rev_growth > 10:
            score += 7
        elif rev_growth > 0:
            score += 5

        earn_growth = safe_get(g, "earnings_growth")
        if earn_growth > 20:
            score += 10
        elif earn_growth > 10:
            score += 7
        elif earn_growth > 0:
            score += 5

        # Valuation (20)
        v = ratios.get("valuation", {})
        pe = safe_get(v, "pe_ratio")
        if pe > 0:
            if 10 < pe < 25:
                score += 10
            elif 5 < pe < 40:
                score += 7
            else:
                score += 3

        pb = safe_get(v, "pb_ratio")
        if pb > 0:
            if 1 < pb < 3:
                score += 10
            elif 0.5 < pb < 5:
                score += 7
            else:
                score += 3

        return min(score, 100)

    def _generate_investment_advice(
        self, health_score: int, ratios: Dict
    ) -> Dict[str, str]:
        """Generate investment advice."""
        if health_score >= 85:
            rating = "Strong Buy"
            reason = "Excellent financial health, strong profitability and growth."
        elif health_score >= 70:
            rating = "Buy"
            reason = "Good financial health, undervalued or growing."
        elif health_score >= 55:
            rating = "Hold"
            reason = "Average financial health, wait and see."
        elif health_score >= 40:
            rating = "Reduce"
            reason = "Below average health, consider reducing position."
        else:
            rating = "Sell"
            reason = "Poor financial health, high risk."

        return {"rating": rating, "reason": reason}

    async def get_fundamental_analysis(self, ticker: str) -> Dict[str, Any]:
        """Generate complete fundamental analysis report."""
        try:
            financial_data_raw = await self._fetch_financial_data(ticker)

            balance_df = financial_data_raw.get("balance_sheet")
            income_df = financial_data_raw.get("income_statement")
            cashflow_df = financial_data_raw.get("cash_flow")
            indicator_df = financial_data_raw.get("financial_indicators")
            market_metrics = financial_data_raw.get("market_metrics")
            company_info = financial_data_raw.get("company_info", {})
            raw_info = financial_data_raw.get("_raw_info")

            financial_data = self._extract_financial_data(
                balance_df,
                income_df,
                cashflow_df,
                indicator_df,
                raw_info,
                market_metrics,
            )

            valuation = FinancialRatios.calculate_valuation_ratios(financial_data)
            profitability = FinancialRatios.calculate_profitability_ratios(
                financial_data
            )
            solvency = FinancialRatios.calculate_solvency_ratios(financial_data)
            growth = FinancialRatios.calculate_growth_ratios(financial_data)
            dupont = FinancialRatios.calculate_dupont_analysis(financial_data)

            all_ratios = {
                "valuation": valuation,
                "profitability": profitability,
                "solvency": solvency,
                "growth": growth,
                "dupont": dupont,
            }

            health_score = self._calculate_health_score(all_ratios)
            advice = self._generate_investment_advice(health_score, all_ratios)

            return {
                "variant": "financial_chart",
                "ticker": ticker,
                "symbol": ticker,
                "title": f"财务分析: {ticker}",
                "company_info": company_info,
                "health_score": health_score,
                "ratios": all_ratios,
                "analysis": advice,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            self.logger.error(f"Fundamental analysis failed for {ticker}: {e}")
            return {"error": str(e)}
