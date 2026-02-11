# src/server/domain/routing/policy.py
"""Routing policy loader and selector."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from src.server.utils.logger import logger


@dataclass
class RoutingRule:
    asset_type: str
    data_type: str
    exchange: Optional[str]
    providers: List[str]


class RoutingPolicy:
    def __init__(self, rules: List[RoutingRule]):
        self._rules = rules

    @classmethod
    def load(cls, path: Optional[str] = None) -> "RoutingPolicy":
        policy_path = path or os.getenv("ROUTING_POLICY_PATH") or cls._default_path()
        data = None
        if policy_path and Path(policy_path).exists():
            try:
                data = cls._load_file(policy_path)
            except Exception as e:
                logger.warning("Failed to load routing policy", error=str(e))
        if not data:
            data = cls._default_policy()
        rules = []
        for item in data.get("routing_policy", []):
            rules.append(
                RoutingRule(
                    asset_type=str(item.get("asset_type", "")),
                    data_type=str(item.get("data_type", "")),
                    exchange=item.get("exchange"),
                    providers=list(item.get("providers") or []),
                )
            )
        return cls(rules)

    def select_providers(
        self, asset_type: str, exchange: Optional[str], data_type: str
    ) -> List[str]:
        asset_type = (asset_type or "").lower()
        exchange = (exchange or "").upper()
        data_type = (data_type or "").lower()

        matched: List[str] = []
        for rule in self._rules:
            if rule.asset_type.lower() != asset_type:
                continue
            if rule.data_type.lower() != data_type:
                continue
            if rule.exchange and rule.exchange.upper() != exchange:
                continue
            matched = list(rule.providers)
            break
        return matched

    @staticmethod
    def _default_path() -> str:
        return str(Path(__file__).resolve().parents[2] / "config" / "routing_policy.json")

    @staticmethod
    def _load_file(path: str) -> dict:
        if path.endswith(".json"):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        # Try YAML if available
        try:
            import yaml  # type: ignore

            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise RuntimeError(f"Unsupported policy format: {path}: {e}")

    @staticmethod
    def _default_policy() -> dict:
        return {
            "routing_policy": [
                {
                    "asset_type": "commodity_spot",
                    "data_type": "historical",
                    "exchange": "OTC",
                    "providers": ["twelve_data", "alpha_vantage", "yahoo"],
                },
                {
                    "asset_type": "commodity_spot",
                    "data_type": "realtime",
                    "exchange": "OTC",
                    "providers": ["twelve_data", "alpha_vantage", "yahoo"],
                },
                {
                    "asset_type": "commodity_future",
                    "data_type": "historical",
                    "exchange": "COMEX",
                    "providers": ["yahoo", "twelve_data"],
                },
                {
                    "asset_type": "commodity_future",
                    "data_type": "realtime",
                    "exchange": "COMEX",
                    "providers": ["yahoo", "twelve_data"],
                },
                {
                    "asset_type": "stock",
                    "data_type": "historical",
                    "exchange": "NASDAQ",
                    "providers": ["yahoo", "twelve_data", "finnhub"],
                },
                {
                    "asset_type": "stock",
                    "data_type": "realtime",
                    "exchange": "NASDAQ",
                    "providers": ["yahoo", "twelve_data", "finnhub"],
                },
                {
                    "asset_type": "stock",
                    "data_type": "historical",
                    "exchange": "HKEX",
                    "providers": ["yahoo", "twelve_data"],
                },
                {
                    "asset_type": "stock",
                    "data_type": "realtime",
                    "exchange": "HKEX",
                    "providers": ["yahoo", "twelve_data"],
                },
                {
                    "asset_type": "fx",
                    "data_type": "historical",
                    "exchange": "FOREX",
                    "providers": ["twelve_data", "alpha_vantage"],
                },
                {
                    "asset_type": "fx",
                    "data_type": "realtime",
                    "exchange": "FOREX",
                    "providers": ["twelve_data", "alpha_vantage"],
                },
                {
                    "asset_type": "crypto",
                    "data_type": "historical",
                    "exchange": "CRYPTO",
                    "providers": ["ccxt", "crypto"],
                },
                {
                    "asset_type": "crypto",
                    "data_type": "realtime",
                    "exchange": "CRYPTO",
                    "providers": ["ccxt", "crypto"],
                },
            ]
        }
