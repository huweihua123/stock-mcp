# src/server/domain/symbols/resolver.py
"""SymbolResolver: normalize raw symbols into EXCHANGE:SYMBOL and InstrumentRef."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from src.server.domain.symbols.types import (
    InstrumentRef,
    ResolutionStatus,
    SymbolCandidate,
    SymbolResolution,
)
from src.server.utils.logger import logger


class SymbolResolver:
    def __init__(self, security_master_repo, adapter_manager):
        self._repo = security_master_repo
        self._adapters = adapter_manager

    async def resolve(self, raw_symbol: str) -> SymbolResolution:
        raw = (raw_symbol or "").strip().upper()
        if not raw:
            return SymbolResolution(
                raw=raw_symbol or "", status=ResolutionStatus.INVALID, reason="empty"
            )

        # Commodity spot aliases (e.g., XAUUSD, XAGUSD)
        commodity_spot = self._resolve_commodity_spot(raw)
        if commodity_spot:
            normalized, asset_type, exchange, base, quote, contract = commodity_spot
            asset_id, resolved_type, canonical_id = await self._persist_resolution(
                raw_symbol,
                normalized,
                asset_type=asset_type,
                base=base,
                quote=quote,
                contract=contract,
            )
            instrument = self._build_instrument(
                raw_symbol=raw_symbol,
                normalized=normalized,
                asset_type=resolved_type or asset_type,
                exchange=exchange,
                base=base,
                quote=quote,
                contract=contract,
                canonical_id=canonical_id,
            )
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange=exchange,
                asset_type=resolved_type or asset_type,
                asset_id=asset_id,
                canonical_id=canonical_id,
                instrument=instrument,
            )

        # Commodity futures aliases (e.g., GC, SI, CL)
        commodity_future = self._resolve_commodity_future(raw)
        if commodity_future:
            normalized, asset_type, exchange, base, quote, contract = commodity_future
            asset_id, resolved_type, canonical_id = await self._persist_resolution(
                raw_symbol,
                normalized,
                asset_type=asset_type,
                base=base,
                quote=quote,
                contract=contract,
            )
            instrument = self._build_instrument(
                raw_symbol=raw_symbol,
                normalized=normalized,
                asset_type=resolved_type or asset_type,
                exchange=exchange,
                base=base,
                quote=quote,
                contract=contract,
                canonical_id=canonical_id,
            )
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange=exchange,
                asset_type=resolved_type or asset_type,
                asset_id=asset_id,
                canonical_id=canonical_id,
                instrument=instrument,
            )

        # FX pairs (e.g., EURUSD, USDJPY, EUR/USD)
        fx = self._resolve_fx(raw)
        if fx:
            normalized, asset_type, exchange, base, quote, contract = fx
            asset_id, resolved_type, canonical_id = await self._persist_resolution(
                raw_symbol,
                normalized,
                asset_type=asset_type,
                base=base,
                quote=quote,
                contract=contract,
            )
            instrument = self._build_instrument(
                raw_symbol=raw_symbol,
                normalized=normalized,
                asset_type=resolved_type or asset_type,
                exchange=exchange,
                base=base,
                quote=quote,
                contract=contract,
                canonical_id=canonical_id,
            )
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange=exchange,
                asset_type=resolved_type or asset_type,
                asset_id=asset_id,
                canonical_id=canonical_id,
                instrument=instrument,
            )

        # Already in EXCHANGE:SYMBOL
        if ":" in raw:
            exchange, symbol = self._split_exchange(raw)
            if not exchange or not symbol:
                return SymbolResolution(
                    raw=raw_symbol,
                    status=ResolutionStatus.INVALID,
                    reason="invalid_format",
                )
            asset_type_hint = self._infer_cn_equity_asset_type(exchange, symbol)
            if asset_type_hint != "index":
                exchange, symbol = self._autocorrect_a_share_exchange(
                    exchange, symbol, raw_symbol
                )
                asset_type_hint = self._infer_cn_equity_asset_type(exchange, symbol)
            normalized = f"{exchange}:{symbol}"
            asset_id, resolved_type, canonical_id = await self._persist_resolution(
                raw_symbol, normalized, asset_type=asset_type_hint
            )
            instrument = self._build_instrument(
                raw_symbol=raw_symbol,
                normalized=normalized,
                asset_type=resolved_type or asset_type_hint or "stock",
                exchange=exchange,
                base=None,
                quote=None,
                contract=None,
                canonical_id=canonical_id,
            )
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange=exchange,
                asset_type=resolved_type or asset_type_hint,
                asset_id=asset_id,
                canonical_id=canonical_id,
                instrument=instrument,
            )

        # Suffix forms like 600519.SH / 000001.SZ / 3988.HK
        suffix = self._resolve_suffix(raw)
        if suffix:
            exchange, symbol = suffix
            asset_type_hint = self._infer_cn_equity_asset_type(exchange, symbol)
            if asset_type_hint != "index":
                exchange, symbol = self._autocorrect_a_share_exchange(
                    exchange, symbol, raw_symbol
                )
                asset_type_hint = self._infer_cn_equity_asset_type(exchange, symbol)
            normalized = f"{exchange}:{symbol}"
            asset_id, resolved_type, canonical_id = await self._persist_resolution(
                raw_symbol, normalized, asset_type=asset_type_hint
            )
            instrument = self._build_instrument(
                raw_symbol=raw_symbol,
                normalized=normalized,
                asset_type=resolved_type or asset_type_hint or "stock",
                exchange=exchange,
                base=None,
                quote=None,
                contract=None,
                canonical_id=canonical_id,
            )
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange=exchange,
                asset_type=resolved_type or asset_type_hint,
                asset_id=asset_id,
                canonical_id=canonical_id,
                instrument=instrument,
            )

        # Numeric heuristics
        numeric = self._resolve_numeric(raw)
        if numeric:
            exchange, symbol = numeric
            asset_type_hint = self._infer_cn_equity_asset_type(exchange, symbol)
            if asset_type_hint != "index":
                exchange, symbol = self._autocorrect_a_share_exchange(
                    exchange, symbol, raw_symbol
                )
                asset_type_hint = self._infer_cn_equity_asset_type(exchange, symbol)
            normalized = f"{exchange}:{symbol}"
            asset_id, resolved_type, canonical_id = await self._persist_resolution(
                raw_symbol, normalized, asset_type=asset_type_hint
            )
            instrument = self._build_instrument(
                raw_symbol=raw_symbol,
                normalized=normalized,
                asset_type=resolved_type or asset_type_hint or "stock",
                exchange=exchange,
                base=None,
                quote=None,
                contract=None,
                canonical_id=canonical_id,
            )
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange=exchange,
                asset_type=resolved_type or asset_type_hint,
                asset_id=asset_id,
                canonical_id=canonical_id,
                instrument=instrument,
            )

        # Crypto heuristic
        crypto = self._resolve_crypto(raw)
        if crypto:
            normalized, asset_type, exchange, base, quote, contract = crypto
            asset_id, resolved_type, canonical_id = await self._persist_resolution(
                raw_symbol,
                normalized,
                asset_type=asset_type,
                base=base,
                quote=quote,
                contract=contract,
            )
            instrument = self._build_instrument(
                raw_symbol=raw_symbol,
                normalized=normalized,
                asset_type=resolved_type or asset_type,
                exchange=exchange,
                base=base,
                quote=quote,
                contract=contract,
                canonical_id=canonical_id,
            )
            return SymbolResolution(
                raw=raw_symbol,
                normalized=normalized,
                status=ResolutionStatus.RESOLVED,
                exchange=exchange,
                asset_type=resolved_type or asset_type,
                asset_id=asset_id,
                canonical_id=canonical_id,
                instrument=instrument,
            )

        # Security master lookup (aliases/listings)
        candidates = await self._repo.find_candidates(raw)
        if candidates:
            resolved = self._select_candidate(candidates)
            if resolved:
                asset_id, resolved_type, canonical_id = await self._persist_resolution(
                    raw_symbol, resolved
                )
                exchange, _ = self._split_exchange(resolved)
                instrument = self._build_instrument(
                    raw_symbol=raw_symbol,
                    normalized=resolved,
                    asset_type=resolved_type or "stock",
                    exchange=exchange or "",
                    base=None,
                    quote=None,
                    contract=None,
                    canonical_id=canonical_id,
                )
                return SymbolResolution(
                    raw=raw_symbol,
                    normalized=resolved,
                    status=ResolutionStatus.RESOLVED,
                    exchange=exchange,
                    asset_type=resolved_type,
                    asset_id=asset_id,
                    canonical_id=canonical_id,
                    instrument=instrument,
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
                asset_id, resolved_type, canonical_id = await self._persist_resolution(
                    raw_symbol, probe
                )
                exchange, _ = self._split_exchange(probe)
                instrument = self._build_instrument(
                    raw_symbol=raw_symbol,
                    normalized=probe,
                    asset_type=resolved_type or "stock",
                    exchange=exchange or "",
                    base=None,
                    quote=None,
                    contract=None,
                    canonical_id=canonical_id,
                )
                return SymbolResolution(
                    raw=raw_symbol,
                    normalized=probe,
                    status=ResolutionStatus.RESOLVED,
                    exchange=exchange,
                    asset_type=resolved_type,
                    asset_id=asset_id,
                    canonical_id=canonical_id,
                    instrument=instrument,
                )

        return SymbolResolution(
            raw=raw_symbol,
            status=ResolutionStatus.NOT_FOUND,
            reason="not_resolved",
        )

    async def _persist_resolution(
        self,
        raw: str,
        normalized: str,
        asset_type: Optional[str] = None,
        base: Optional[str] = None,
        quote: Optional[str] = None,
        contract: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        try:
            exchange, symbol = self._split_exchange(normalized)
            if not exchange or not symbol:
                return None, None, None

            existing = await self._repo.find_by_listing(exchange, symbol)
            if existing and existing.get("asset_id"):
                asset_id = existing.get("asset_id")
                resolved_asset_type = existing.get("asset_type") or asset_type
                if isinstance(resolved_asset_type, str):
                    resolved_asset_type = resolved_asset_type.lower()
            else:
                # Try to enrich asset metadata via adapter
                name = symbol
                resolved_asset_type = asset_type or "stock"
                country = None
                currency = None
                timezone = None
                try:
                    asset = await self._adapters.get_asset_info(normalized)
                    if asset:
                        name = asset.name
                        if hasattr(asset, "asset_type"):
                            resolved_asset_type = (
                                asset.asset_type.value
                                if hasattr(asset.asset_type, "value")
                                else str(asset.asset_type)
                            )
                        mi = asset.market_info
                        if mi:
                            country = getattr(mi, "country", None)
                            currency = getattr(mi, "currency", None)
                            timezone = getattr(mi, "timezone", None)
                except Exception:
                    pass

                if isinstance(resolved_asset_type, str):
                    resolved_asset_type = resolved_asset_type.lower()
                asset_id = await self._repo.upsert_asset(
                    asset_id=None,
                    name=name,
                    asset_type=resolved_asset_type,
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

            if asset_id and not isinstance(asset_id, str):
                asset_id = str(asset_id)
            if asset_id and raw and raw.strip().upper() != symbol.upper():
                await self._repo.add_alias(
                    asset_id,
                    raw,
                    alias_type="user_input",
                    source="user_input",
                    confidence=0.3,
                )

            if isinstance(resolved_asset_type, str):
                resolved_asset_type = resolved_asset_type.lower()
            canonical_id = self._build_canonical_id(
                asset_type=resolved_asset_type or asset_type or "stock",
                exchange=exchange,
                symbol=symbol,
                base=base,
                quote=quote,
                contract=contract,
            )
            if canonical_id and asset_id:
                await self._repo.add_identifier(asset_id, "canonical_id", canonical_id)

            return asset_id, resolved_asset_type, canonical_id
        except Exception as e:
            logger.warning("SecurityMaster persist failed", error=str(e))
            return None, None, None

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
            "SHSE": "SSE",
            "SS": "SSE",
            "SZ": "SZSE",
            "SZSE": "SZSE",
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
            # Common broad-market indices (CSI/SSE) to avoid misrouting to SZSE stocks.
            if raw in {"000001", "000016", "000300", "000688", "000852", "000905"}:
                return "SSE", raw
            if raw.startswith("6"):
                return "SSE", raw
            if raw.startswith("0") or raw.startswith("3"):
                return "SZSE", raw
            if raw.startswith("8"):
                return "BSE", raw
        if len(raw) == 5:
            return "HKEX", raw
        return None

    def _infer_cn_equity_asset_type(
        self, exchange: Optional[str], symbol: Optional[str]
    ) -> Optional[str]:
        """Infer CN equity asset type from code pattern for routing hints."""
        if not exchange or not symbol:
            return None
        ex = exchange.upper()
        sym = symbol.upper()
        if ex not in {"SSE", "SZSE", "BSE"}:
            return None
        if not sym.isdigit() or len(sym) != 6:
            return None
        if ex == "SSE":
            if sym.startswith("000") or sym.startswith(("880", "881", "882", "883")):
                return "index"
            return "stock"
        if ex == "SZSE":
            if sym.startswith("399"):
                return "index"
            return "stock"
        return "stock"

    def _resolve_crypto(
        self, raw: str
    ) -> Optional[Tuple[str, str, str, Optional[str], Optional[str], Optional[str]]]:
        base = None
        quote = None
        if "/" in raw:
            parts = raw.split("/", 1)
            base = parts[0].strip().upper() or None
            quote = parts[1].strip().upper() or None
        elif "-" in raw:
            parts = raw.split("-", 1)
            base = parts[0].strip().upper() or None
            quote = parts[1].strip().upper() or None
        else:
            base = raw.strip().upper()

        if base in {
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
            normalized = f"CRYPTO:{base}"
            return normalized, "crypto", "CRYPTO", base, quote, None
        return None

    def _resolve_commodity_spot(
        self, raw: str
    ) -> Optional[Tuple[str, str, str, Optional[str], Optional[str], Optional[str]]]:
        # Normalize common spot metals to OTC
        spot_map = {
            "XAUUSD": "OTC:XAUUSD",  # Gold
            "XAGUSD": "OTC:XAGUSD",  # Silver
            "XPTUSD": "OTC:XPTUSD",  # Platinum
            "XPDUSD": "OTC:XPDUSD",  # Palladium
        }
        alias_map = {
            "黄金": "OTC:XAUUSD",
            "金": "OTC:XAUUSD",
            "白银": "OTC:XAGUSD",
            "银": "OTC:XAGUSD",
            "铂金": "OTC:XPTUSD",
            "钯金": "OTC:XPDUSD",
            "SILVER": "OTC:XAGUSD",
            "GOLD": "OTC:XAUUSD",
            "PLATINUM": "OTC:XPTUSD",
            "PALLADIUM": "OTC:XPDUSD",
        }
        if raw in spot_map:
            base = raw[:3]
            quote = raw[3:]
            return spot_map[raw], "commodity_spot", "OTC", base, quote, None
        if raw in alias_map:
            norm = alias_map[raw]
            _, sym = norm.split(":", 1)
            base = sym[:3]
            quote = sym[3:]
            return norm, "commodity_spot", "OTC", base, quote, None
        # Accept Yahoo-style spot symbols: XAUUSD=X
        if raw.endswith("=X"):
            core = raw.replace("=X", "")
            if core in spot_map:
                base = core[:3]
                quote = core[3:]
                return spot_map[core], "commodity_spot", "OTC", base, quote, None
        return None

    def _resolve_commodity_future(
        self, raw: str
    ) -> Optional[Tuple[str, str, str, Optional[str], Optional[str], Optional[str]]]:
        # Common futures root symbols
        future_map = {
            "GC": ("COMEX", "GC"),  # Gold
            "SI": ("COMEX", "SI"),  # Silver
            "HG": ("COMEX", "HG"),  # Copper
            "CL": ("NYMEX", "CL"),  # Crude Oil
            "NG": ("NYMEX", "NG"),  # Natural Gas
            "BRN": ("ICE", "BRN"),  # Brent
            "BZ": ("ICE", "BRN"),   # Brent alias
            "HO": ("NYMEX", "HO"),  # Heating Oil
            "RB": ("NYMEX", "RB"),  # RBOB Gasoline
        }
        alias_map = {
            "黄金期货": ("COMEX", "GC"),
            "白银期货": ("COMEX", "SI"),
            "铜期货": ("COMEX", "HG"),
            "原油期货": ("NYMEX", "CL"),
            "天然气期货": ("NYMEX", "NG"),
            "布油": ("ICE", "BRN"),
            "布伦特": ("ICE", "BRN"),
            "BRENT": ("ICE", "BRN"),
            "COPPER": ("COMEX", "HG"),
            "CRUDE": ("NYMEX", "CL"),
            "NATGAS": ("NYMEX", "NG"),
        }
        if raw in future_map:
            ex, sym = future_map[raw]
            return f"{ex}:{sym}", "commodity_future", ex, sym, None, "CONTINUOUS"
        if raw in alias_map:
            ex, sym = alias_map[raw]
            return f"{ex}:{sym}", "commodity_future", ex, sym, None, "CONTINUOUS"
        # Yahoo-style futures symbols: GC=F
        if raw.endswith("=F"):
            core = raw.replace("=F", "")
            if core in future_map:
                ex, sym = future_map[core]
                return f"{ex}:{sym}", "commodity_future", ex, sym, None, "CONTINUOUS"
        return None

    def _resolve_fx(
        self, raw: str
    ) -> Optional[Tuple[str, str, str, Optional[str], Optional[str], Optional[str]]]:
        # Normalize FX pairs like EURUSD, USDJPY, EUR/USD, EUR-USD
        fx_quotes = {"USD", "EUR", "JPY", "GBP", "CHF", "AUD", "CAD", "NZD", "CNH"}
        clean = raw.replace("/", "").replace("-", "")
        if len(clean) == 6 and clean.isalpha():
            base = clean[:3]
            quote = clean[3:]
            if quote in fx_quotes:
                return f"FOREX:{base}{quote}", "fx", "FOREX", base, quote, None
        # Yahoo-style FX symbols: EURUSD=X
        if raw.endswith("=X"):
            core = raw.replace("=X", "")
            if len(core) == 6 and core.isalpha():
                base = core[:3]
                quote = core[3:]
                if quote in fx_quotes:
                    return f"FOREX:{base}{quote}", "fx", "FOREX", base, quote, None
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

    def _build_instrument(
        self,
        raw_symbol: str,
        normalized: str,
        asset_type: str,
        exchange: str,
        base: Optional[str] = None,
        quote: Optional[str] = None,
        contract: Optional[str] = None,
        canonical_id: Optional[str] = None,
    ) -> InstrumentRef:
        symbol = normalized.split(":", 1)[1] if ":" in normalized else normalized
        canonical_id = canonical_id or self._build_canonical_id(
            asset_type=asset_type,
            exchange=exchange,
            symbol=symbol,
            base=base,
            quote=quote,
            contract=contract,
        )
        return InstrumentRef(
            canonical_id=canonical_id,
            normalized=normalized,
            asset_type=asset_type,
            exchange=exchange,
            raw_input=raw_symbol,
            base=base,
            quote=quote,
            contract=contract,
            aliases=[],
        )

    def _build_canonical_id(
        self,
        asset_type: str,
        exchange: str,
        symbol: str,
        base: Optional[str] = None,
        quote: Optional[str] = None,
        contract: Optional[str] = None,
    ) -> str:
        at = (asset_type or "stock").lower()
        ex = (exchange or "").upper()
        sym = (symbol or "").upper()
        if at in {"commodity_spot", "spot"}:
            base = base or sym[:3]
            quote = quote or sym[3:]
            return f"spot|{ex}|{base}|{quote}"
        if at in {"commodity_future", "future"}:
            contract = contract or "CONTINUOUS"
            return f"future|{ex}|{sym}|{contract}"
        if at == "fx":
            base = base or sym[:3]
            quote = quote or sym[3:]
            return f"fx|{ex}|{base}|{quote}"
        if at == "crypto":
            base = base or sym
            quote = quote or "USD"
            return f"crypto|{ex}|{base}|{quote}"
        return f"{at}|{ex}|{sym}"
