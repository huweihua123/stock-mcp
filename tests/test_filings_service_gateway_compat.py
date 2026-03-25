import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.domain.market_gateway import MarketGateway
from src.server.domain.services.filings_service import FilingsService
from src.server.domain.symbols.types import ResolutionStatus


def run(coro):
    return asyncio.run(coro)


def test_filings_service_uses_keyword_args_and_normalizes_keys():
    adapter_manager = SimpleNamespace()
    adapter_manager.get_filings = AsyncMock(
        return_value=[
            {
                "accessionNumber": "0000950170-25-027421",
                "filingDate": "2025-02-26",
                "reportDate": "2025-01-31",
                "form": "10-K",
                "filingUrl": "https://www.sec.gov/Archives/...",
                "description": "Annual report",
            }
        ]
    )
    service = FilingsService(adapter_manager=adapter_manager)

    result = run(
        service._fetch_filings(
            ticker="NVDA",
            filing_types=["10-K"],
            start_date_str="2025-01-01",
            end_date_str="2025-12-31",
            limit=10,
        )
    )

    adapter_manager.get_filings.assert_awaited_once()
    args, kwargs = adapter_manager.get_filings.await_args
    assert args[0] == "NVDA"
    assert kwargs["limit"] == 10
    assert kwargs["filing_types"] == ["10-K"]

    assert len(result) == 1
    row = result[0]
    assert row["doc_id"] == "0000950170-25-027421"
    assert row["accession"] == "0000950170-25-027421"
    assert row["accession_number"] == "0000950170-25-027421"
    assert row["filing_date"] == "2025-02-26"
    assert row["filingDate"] == "2025-02-26"


def test_market_gateway_ticker_method_accepts_legacy_positional_args():
    class _Resolver:
        async def resolve(self, raw_symbol: str):
            return SimpleNamespace(
                status=ResolutionStatus.RESOLVED,
                normalized=f"NASDAQ:{raw_symbol}",
                reason=None,
                candidates=[],
                instrument=None,
                asset_type="stock",
            )

    class _AdapterManager:
        async def get_filings(
            self,
            ticker: str,
            start_date=None,
            end_date=None,
            limit: int = 10,
            filing_types=None,
        ):
            return [
                {
                    "ticker": ticker,
                    "limit": limit,
                    "filing_types": filing_types or [],
                }
            ]

    gateway = MarketGateway(
        adapter_manager=_AdapterManager(),
        symbol_resolver=_Resolver(),
        market_router=None,
    )

    rows = run(gateway.get_filings("NVDA", None, None, 5, ["10-Q"]))
    assert rows[0]["ticker"] == "NASDAQ:NVDA"
    assert rows[0]["limit"] == 5
    assert rows[0]["filing_types"] == ["10-Q"]
