from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from src.server.capabilities.code_export.schemas import (
    AlphaVantageJsonExportRequest,
    CodeExportResponse,
    TushareCsvExportRequest,
)
from src.server.capabilities.code_export.service import get_code_export_capability_service
from src.server.runtime.models import RuntimeContext
from src.server.utils.logger import logger


def build_router(runtime: RuntimeContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/code-export", tags=["Code Export"])
    service = get_code_export_capability_service(runtime)

    @router.post("/tushare/csv", response_model=CodeExportResponse, summary="Export raw Tushare tabular data for code runtime")
    async def export_tushare_csv(request: TushareCsvExportRequest) -> CodeExportResponse:
        try:
            logger.info("Capability API: export_tushare_csv", api_name=request.api_name)
            return await service.export_tushare_csv(request.api_name, request.kwargs)
        except Exception as exc:
            logger.error(f"Capability API error in export_tushare_csv: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to export tushare CSV: {exc}") from exc

    @router.post("/alphavantage/json", response_model=CodeExportResponse, summary="Export raw Alpha Vantage JSON data for code runtime")
    async def export_alphavantage_json(request: AlphaVantageJsonExportRequest) -> CodeExportResponse:
        try:
            logger.info("Capability API: export_alphavantage_json", function=request.function, symbol=request.symbol)
            return await service.export_alphavantage_json(request.function, request.symbol, request.extra_params)
        except Exception as exc:
            logger.error(f"Capability API error in export_alphavantage_json: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to export Alpha Vantage JSON: {exc}") from exc

    return router
