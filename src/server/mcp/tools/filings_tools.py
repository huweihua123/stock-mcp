# src/server/mcp/tools/filings_tools.py
"""MCP tools for SEC and A-share filings.
Provides access to regulatory filings and announcements.
Returns structured data (JSON).
"""

from typing import Any, Dict, List

from nacos_mcp_wrapper.server.nacos_mcp import NacosMCP

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


def register_filings_tools(mcp: NacosMCP):
    """Register filings tools for Nacos MCP.

    Args:
        mcp: NacosMCP instance
    """

    @mcp.tool()
    async def fetch_sec_filings(
        ticker: str,
        filing_types: list[str] = None,
        start_date: str = None,
        end_date: str = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get SEC filings (US).

        Args:
            ticker: US Stock ticker (e.g. AAPL)
            filing_types: List of types (10-K, 10-Q, 8-K)
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            limit: Max results

        Returns:
            List of filings
        """
        try:
            service = Container.filings_service()
            logger.info(
                "MCP tool called: fetch_sec_filings",
                ticker=ticker,
                types=filing_types,
                limit=limit,
            )

            return await service.fetch_sec_filings(
                ticker=ticker,
                filing_types=filing_types,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )

        except Exception as e:
            logger.error(f"Fetch SEC filings failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def fetch_ashare_filings(
        symbol: str,
        filing_types: list[str] = None,
        start_date: str = None,
        end_date: str = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get A-share announcements.

        Args:
            symbol: A-share code (e.g. 600519)
            filing_types: List of types
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            limit: Max results

        Returns:
            List of announcements
        """
        try:
            service = Container.filings_service()
            logger.info(
                "MCP tool called: fetch_ashare_filings",
                symbol=symbol,
                types=filing_types,
                limit=limit,
            )

            return await service.fetch_ashare_filings(
                symbol=symbol,
                filing_types=filing_types,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )

        except Exception as e:
            logger.error(f"Fetch A-share filings failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_filing_detail(
        filing_id: str, filing_source: str = "sec"
    ) -> Dict[str, Any]:
        """Get detailed content of a filing.

        Args:
            filing_id: Filing ID or URL
            filing_source: 'sec' or 'ashare'

        Returns:
            Dictionary with content
        """
        try:
            service = Container.filings_service()
            logger.info(
                "MCP tool called: get_filing_detail",
                filing_id=filing_id,
                source=filing_source,
            )

            return await service.get_filing_detail(
                filing_id=filing_id, filing_source=filing_source
            )

        except Exception as e:
            logger.error(f"Get filing detail failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    async def search_filings_by_keyword(
        keyword: str,
        market: str = "us",
        filing_types: list[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search filings by keyword.

        Args:
            keyword: Search keyword
            market: 'us' or 'china'
            filing_types: Filter by types
            limit: Max results

        Returns:
            List of filings
        """
        try:
            service = Container.filings_service()
            logger.info(
                "MCP tool called: search_filings_by_keyword",
                keyword=keyword,
                market=market,
                limit=limit,
            )

            return await service.search_filings_by_keyword(
                keyword=keyword, market=market, filing_types=filing_types, limit=limit
            )

        except Exception as e:
            logger.error(f"Search filings failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_latest_earnings_reports(
        market: str = "us", days: int = 7, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get latest earnings reports.

        Args:
            market: 'us' or 'china'
            days: Lookback days
            limit: Max results

        Returns:
            List of reports
        """
        try:
            service = Container.filings_service()
            logger.info(
                "MCP tool called: get_latest_earnings_reports", market=market, days=days
            )

            return await service.get_latest_earnings_reports(
                market=market, days=days, limit=limit
            )

        except Exception as e:
            logger.error(f"Get latest earnings reports failed: {e}")
            return [{"error": str(e)}]

    logger.info("✅ Registered 5 filings tools for Nacos MCP")
