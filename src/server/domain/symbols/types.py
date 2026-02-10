# src/server/domain/symbols/types.py
"""Symbol resolution types and status models."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    INVALID = "invalid"


class SymbolCandidate(BaseModel):
    ticker: str
    exchange: Optional[str] = None
    asset_id: Optional[str] = None
    name: Optional[str] = None
    asset_type: Optional[str] = None


class SymbolResolution(BaseModel):
    raw: str
    normalized: Optional[str] = None
    status: ResolutionStatus = ResolutionStatus.INVALID
    exchange: Optional[str] = None
    asset_id: Optional[str] = None
    asset_type: Optional[str] = None
    reason: Optional[str] = None
    candidates: List[SymbolCandidate] = Field(default_factory=list)
