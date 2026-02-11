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


class InstrumentRef(BaseModel):
    canonical_id: str
    normalized: str
    asset_type: str
    exchange: str
    raw_input: str
    base: Optional[str] = None
    quote: Optional[str] = None
    contract: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)


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
    canonical_id: Optional[str] = None
    reason: Optional[str] = None
    candidates: List[SymbolCandidate] = Field(default_factory=list)
    instrument: Optional[InstrumentRef] = None
