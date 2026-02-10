# src/server/domain/symbols/resolver.py
"""SymbolResolver: normalize raw symbols into EXCHANGE:SYMBOL."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from src.server.domain.symbols.types import ResolutionStatus, SymbolCandidate, SymbolResolution
from src.server.utils.logger import logger


class SymbolResolver:
    def __init__(self, security_master_repo, adapter_manager):
        self._repo = security_master_repo
        self._adapters = adapter_manager

    async def resolve(self, raw_symbol: str) -> SymbolResolution:
        raw = (raw_symbol or "").strip().upper()
        if not raw:
            return SymbolResolution(raw=raw_symbol or "", status=ResolutionStatus.INVALID, reason="empty")

        # Already in EXCHANGE:SYMBOL
        if ":" in raw:
            exchange, symbol = self._split_exchange(raw)
            if not exchange or not symbol:
                return SymbolResolution(raw=raw_symbol, status=ResolutionStatus.INVALID, reason="invalid_format")
            exchange, symbol = self._autocorrect_a_share_exchange(exchange, symbol, raw_symbol)
            normalized = f"{exchange}:{symbol}"
            await self._persist_resolution(raw_symbol, normalized)
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange=exchange,
            )

        # Suffix forms like 600519.SH / 000001.SZ / 3988.HK
        suffix = self._resolve_suffix(raw)
        if suffix:
            exchange, symbol = suffix
            exchange, symbol = self._autocorrect_a_share_exchange(exchange, symbol, raw_symbol)
            normalized = f"{exchange}:{symbol}"
            await self._persist_resolution(raw_symbol, normalized)
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange=exchange,
            )

        # Numeric heuristics
        numeric = self._resolve_numeric(raw)
        if numeric:
            exchange, symbol = numeric
            exchange, symbol = self._autocorrect_a_share_exchange(exchange, symbol, raw_symbol)
            normalized = f"{exchange}:{symbol}"
            await self._persist_resolution(raw_symbol, normalized)
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange=exchange,
            )

        # Crypto heuristic
        crypto = self._resolve_crypto(raw)
        if crypto:
            normalized = f"CRYPTO:{crypto}"
            await self._persist_resolution(raw_symbol, normalized)
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange="CRYPTO",
                asset_type="crypto",
            )

        # Security master lookup (aliases/listings)
        candidates = await self._repo.find_candidates(raw)
        if candidates:
            resolved = self._select_candidate(candidates)
            if resolved:
                await self._persist_resolution(raw_symbol, resolved)
                exchange, symbol = self._split_exchange(resolved)
                return SymbolResolution(
                    raw=raw_symbol,
                    normalized=resolved,
                    status=ResolutionStatus.RESOLVED,
                    exchange=exchange,
                )
            return SymbolResolution(
                raw=raw_symbol,
                status=ResolutionStatus.AMBIGUOUS,
                reason="multiple_candidates",
                candidates=[
                    SymbolCandidate(
                        ticker=f"{c.get('exchange')}:{c.get('ticker')}",
                        exchange=c.get("exchange"),
                        asset_id=c.get("asset_id"),
                        name=c.get("name"),
                        asset_type=c.get("asset_type"),
                    )
                    for c in candidates
                    if c.get("exchange") and c.get("ticker")
                ],
            )

        # Probe common US exchanges for alpha symbols
        if re.fullmatch(r"[A-Z]{1,6}", raw):
            probe = await self._probe_us_exchanges(raw)
            if probe:
                await self._persist_resolution(raw_symbol, probe)
                exchange, _ = self._split_exchange(probe)
                return SymbolResolution(
                    raw=raw_symbol,
                    normalized=probe,
                    status=ResolutionStatus.RESOLVED,
                    exchange=exchange,
                )

        return SymbolResolution(
            raw=raw_symbol,
            status=ResolutionStatus.NOT_FOUND,
            reason="not_resolved",
        )

    async def _persist_resolution(self, raw: str, normalized: str) -> None:
        try:
            exchange, symbol = self._split_exchange(normalized)
            if not exchange or not symbol:
                return

            existing = await self._repo.find_by_listing(exchange, symbol)
            if existing and existing.get("asset_id"):
                asset_id = existing.get("asset_id")
            else:
                # Try to enrich asset metadata via adapter
                name = symbol
                asset_type = "stock"
                country = None
                currency = None
                timezone = None
                try:
                    asset = await self._adapters.get_asset_info(normalized)
                    if asset:
                        name = asset.name
                        asset_type = str(asset.asset_type) if hasattr(asset, "asset_type") else "stock"
                        mi = asset.market_info
                        if mi:
                            country = getattr(mi, "country", None)
                            currency = getattr(mi, "currency", None)
                            timezone = getattr(mi, "timezone", None)
                except Exception:
                    pass

                asset_id = await self._repo.upsert_asset(
                    asset_id=None,
                    name=name,
                    asset_type=asset_type,
                    country=country,
                    currency=currency,
                    timezone=timezone,
                )
                await self._repo.upsert_listing(
                    asset_id=asset_id,
                    exchange=exchange,
                    ticker=symbol,
                    is_primary=True,
                )

            if raw and raw.strip().upper() != symbol.upper():
                await self._repo.add_alias(asset_id, raw)
        except Exception as e:
            logger.warning("SecurityMaster persist failed", error=str(e))

    def _split_exchange(self, raw: str) -> Tuple[Optional[str], Optional[str]]:
        if ":" not in raw:
            return None, None
        exchange, symbol = raw.split(":", 1)
        exchange = exchange.strip().upper()
        exchange = self._normalize_exchange(exchange)
        symbol = symbol.strip().upper()
        return exchange or None, symbol or None

    def _normalize_exchange(self, exchange: str) -> str:
        mapping = {
            "SH": "SSE",
            "SS": "SSE",
            "SZ": "SZSE",
            "BJ": "BSE",
            "HK": "HKEX",
        }
        return mapping.get(exchange, exchange)

    def _autocorrect_a_share_exchange(
        self, exchange: str, symbol: str, raw_symbol: str
    ) -> Tuple[str, str]:
        """Auto-correct A-share exchange prefix when it mismatches the 6-digit code."""
        if not exchange or not symbol:
            return exchange, symbol
        if not symbol.isdigit() or len(symbol) != 6:
            return exchange, symbol

        ex = exchange.upper()
        # Only auto-correct between SSE/SZSE to avoid unexpected changes for other markets.
        if ex in {"SSE", "SZSE"}:
            if symbol.startswith("6") and ex != "SSE":
                logger.info(
                    "Auto-correct A-share exchange prefix",
                    raw=raw_symbol,
                    from_exchange=ex,
                    to_exchange="SSE",
                    symbol=symbol,
                )
                return "SSE", symbol
            if symbol.startswith(("0", "3")) and ex != "SZSE":
                logger.info(
                    "Auto-correct A-share exchange prefix",
                    raw=raw_symbol,
                    from_exchange=ex,
                    to_exchange="SZSE",
                    symbol=symbol,
                )
                return "SZSE", symbol
        return exchange, symbol

    def _resolve_suffix(self, raw: str) -> Optional[Tuple[str, str]]:
        if "." not in raw:
            return None
        symbol, suffix = raw.split(".", 1)
        suffix = suffix.strip().upper()
        symbol = symbol.strip().upper()
        mapping = {
            "SH": "SSE",
            "SS": "SSE",
            "SZ": "SZSE",
            "BJ": "BSE",
            "HK": "HKEX",
            "US": "NASDAQ",
        }
        if suffix in mapping and symbol:
            return mapping[suffix], symbol
        return None

    def _resolve_numeric(self, raw: str) -> Optional[Tuple[str, str]]:
        if not raw.isdigit():
            return None
        if len(raw) == 6:
            if raw.startswith("6"):
                return "SSE", raw
            if raw.startswith("0") or raw.startswith("3"):
                return "SZSE", raw
            if raw.startswith("8"):
                return "BSE", raw
        if len(raw) == 5:
            return "HKEX", raw
        return None

    def _resolve_crypto(self, raw: str) -> Optional[str]:
        # BTC/USDT -> BTC, ETH-USDT -> ETH
        if "/" in raw:
            base = raw.split("/", 1)[0].strip().upper()
            return base if base else None
        if "-" in raw:
            base = raw.split("-", 1)[0].strip().upper()
            return base if base else None
        if raw in {
            "BTC",
            "ETH",
            "USDT",
            "BNB",
            "USDC",
            "XRP",
            "ADA",
            "DOGE",
            "SOL",
            "DOT",
        }:
            return raw
        return None

    async def _probe_us_exchanges(self, symbol: str) -> Optional[str]:
        for exchange in ["NASDAQ", "NYSE", "AMEX"]:
            candidate = f"{exchange}:{symbol}"
            try:
                price = await self._adapters.get_real_time_price(candidate)
                if price:
                    return candidate
            except Exception:
                continue
        return None

    def _select_candidate(self, candidates: List[dict]) -> Optional[str]:
        # Prefer primary listing
        primaries = [c for c in candidates if c.get("is_primary")]
        selected = primaries[0] if primaries else candidates[0] if candidates else None
        if selected and selected.get("exchange") and selected.get("ticker"):
            return f"{selected.get('exchange')}:{selected.get('ticker')}"
        return None
