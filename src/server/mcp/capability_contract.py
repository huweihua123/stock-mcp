"""Capability contract layer for Stock MCP.

This module defines a stable capability surface that is independent from
internal tool names. Tool metadata is transformed at startup so clients can
discover and route by capability tags/meta instead of hard-coded tool ids.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from fastmcp import Context, FastMCP
from fastmcp.tools.tool_transform import ToolTransformConfig

from src.server.utils.logger import logger

CAPABILITY_CONTRACT_VERSION = "1.0.0"
CAPABILITY_STABILITY = "stable"
CAP_TAG_PREFIX = "cap:"
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_DEFAULT_CONTRACT_FILE = Path(__file__).with_name("capabilities.json")


def _validate_semver(value: str, *, field: str) -> None:
    if not _SEMVER_RE.match(value):
        raise ValueError(f"Invalid {field} '{value}': expected semver format x.y.z")


def _canonical_contract_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _coerce_binding(
    tool_name: str,
    raw_binding: Any,
    *,
    default_version: str,
) -> dict[str, str]:
    if isinstance(raw_binding, str):
        capability_id = raw_binding
        capability_version = default_version
        input_schema = f"{capability_id}.input.v1"
        output_schema = f"{capability_id}.output.v1"
    elif isinstance(raw_binding, dict):
        capability_id = str(raw_binding.get("capability_id", "")).strip()
        capability_version = str(
            raw_binding.get("capability_version", default_version)
        ).strip()
        input_schema = str(
            raw_binding.get("input_schema", f"{capability_id}.input.v1")
        ).strip()
        output_schema = str(
            raw_binding.get("output_schema", f"{capability_id}.output.v1")
        ).strip()
    else:
        raise ValueError(
            f"Invalid binding for tool '{tool_name}': expected string or object"
        )

    if not capability_id:
        raise ValueError(f"Binding for tool '{tool_name}' must include capability_id")
    _validate_semver(capability_version, field=f"capability_version({tool_name})")
    if not input_schema:
        raise ValueError(f"Binding for tool '{tool_name}' must include input_schema")
    if not output_schema:
        raise ValueError(f"Binding for tool '{tool_name}' must include output_schema")

    return {
        "capability_id": capability_id,
        "capability_version": capability_version,
        "input_schema": input_schema,
        "output_schema": output_schema,
    }


def _aggregate_capabilities(
    bindings: Mapping[str, Mapping[str, str]],
    *,
    tag_prefix: str,
) -> list[dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}
    for tool_name, binding in bindings.items():
        capability_id = binding["capability_id"]
        current = aggregated.get(capability_id)
        if current is None:
            aggregated[capability_id] = {
                "id": capability_id,
                "version": binding["capability_version"],
                "stability": CAPABILITY_STABILITY,
                "tag": f"{tag_prefix}{capability_id}",
                "input_schema": binding["input_schema"],
                "output_schema": binding["output_schema"],
                "tools": [tool_name],
            }
            continue

        # Same capability id can map to multiple tools, but schema/version must stay stable.
        if current["version"] != binding["capability_version"]:
            raise ValueError(
                f"Conflicting capability_version for '{capability_id}': "
                f"{current['version']} vs {binding['capability_version']}"
            )
        if current["input_schema"] != binding["input_schema"]:
            raise ValueError(
                f"Conflicting input_schema for '{capability_id}': "
                f"{current['input_schema']} vs {binding['input_schema']}"
            )
        if current["output_schema"] != binding["output_schema"]:
            raise ValueError(
                f"Conflicting output_schema for '{capability_id}': "
                f"{current['output_schema']} vs {binding['output_schema']}"
            )
        current["tools"].append(tool_name)

    return [
        {
            **cap,
            "tools": sorted(cap["tools"]),
        }
        for _, cap in sorted(aggregated.items())
    ]


@lru_cache(maxsize=2)
def load_capability_contract(contract_file: str | None = None) -> dict[str, Any]:
    path = Path(contract_file) if contract_file else _DEFAULT_CONTRACT_FILE
    if not path.exists():
        raise FileNotFoundError(f"Capability contract file not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Capability contract must be a JSON object")

    contract_version = str(raw.get("contract_version", "")).strip()
    if not contract_version:
        raise ValueError("Capability contract missing 'contract_version'")
    _validate_semver(contract_version, field="contract_version")

    stability = str(raw.get("capability_stability", CAPABILITY_STABILITY)).strip() or CAPABILITY_STABILITY
    tag_prefix = str(raw.get("capability_tag_prefix", CAP_TAG_PREFIX)).strip() or CAP_TAG_PREFIX

    raw_bindings = raw.get("bindings")
    if not isinstance(raw_bindings, dict) or not raw_bindings:
        raise ValueError("Capability contract 'bindings' must be a non-empty object")

    normalized_bindings: dict[str, dict[str, str]] = {}
    for tool_name, raw_binding in sorted(raw_bindings.items()):
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError(f"Invalid tool name in bindings: {tool_name!r}")
        normalized_bindings[tool_name] = _coerce_binding(
            tool_name.strip(),
            raw_binding,
            default_version=contract_version,
        )

    capabilities = _aggregate_capabilities(normalized_bindings, tag_prefix=tag_prefix)
    canonical_payload = {
        "contract_version": contract_version,
        "capability_stability": stability,
        "capability_tag_prefix": tag_prefix,
        "bindings": normalized_bindings,
    }
    return {
        **canonical_payload,
        "capabilities": capabilities,
        "binding_count": len(normalized_bindings),
        "capability_count": len(capabilities),
        "contract_hash": _canonical_contract_hash(canonical_payload),
        "contract_file": str(path),
    }


def _get_raw_tools(mcp: FastMCP) -> dict[str, Any]:
    tool_manager = getattr(mcp, "_tool_manager", None)
    if tool_manager is None or not hasattr(tool_manager, "_tools"):
        raise RuntimeError("FastMCP internals unavailable: _tool_manager._tools not found")
    return dict(getattr(tool_manager, "_tools"))


def _merge_capability_meta(
    existing: dict[str, Any] | None,
    *,
    capability_id: str,
    capability_version: str,
    stability: str,
    contract_version: str,
) -> dict[str, Any]:
    merged = dict(existing or {})
    merged.update(
        {
            "capability_id": capability_id,
            "capability_version": capability_version,
            "capability_stability": stability,
            "capability_contract_version": contract_version,
            "contract_layer": "stock-mcp-capability-contract",
        }
    )
    return merged


def build_capability_catalog(tool_names: Iterable[str]) -> dict[str, Any]:
    contract = load_capability_contract()
    capability_to_tools: dict[str, list[str]] = defaultdict(list)
    unbound_tools: list[str] = []

    for tool_name in sorted(set(tool_names)):
        binding = contract["bindings"].get(tool_name)
        if binding is None:
            unbound_tools.append(tool_name)
            continue
        capability_id = binding["capability_id"]
        capability_to_tools[capability_id].append(tool_name)

    capabilities: list[dict[str, Any]] = []
    by_capability = {c["id"]: c for c in contract["capabilities"]}
    for capability_id, tool_list in sorted(capability_to_tools.items()):
        cap = by_capability[capability_id]
        capabilities.append(
            {
                "id": capability_id,
                "version": cap["version"],
                "stability": cap["stability"],
                "tag": cap["tag"],
                "input_schema": cap["input_schema"],
                "output_schema": cap["output_schema"],
                "tools": sorted(tool_list),
            }
        )

    return {
        "contract_version": contract["contract_version"],
        "contract_hash": contract["contract_hash"],
        "capability_count": len(capabilities),
        "bound_tool_count": sum(len(x["tools"]) for x in capabilities),
        "capabilities": capabilities,
        "unbound_tools": unbound_tools,
    }


def install_capability_contract(mcp: FastMCP, *, strict: bool = True) -> dict[str, Any]:
    """Attach capability tags/meta to registered tools via transformations."""
    contract = load_capability_contract()
    bindings: dict[str, dict[str, str]] = contract["bindings"]
    tag_prefix = contract["capability_tag_prefix"]
    raw_tools = _get_raw_tools(mcp)
    raw_tool_names = set(raw_tools.keys())

    unbound = sorted(raw_tool_names - set(bindings.keys()))
    stale = sorted(set(bindings.keys()) - raw_tool_names)

    if unbound:
        message = f"Capability contract missing bindings for tools: {unbound}"
        if strict:
            raise RuntimeError(message)
        logger.warning(message)
    if stale:
        logger.warning(
            "Capability contract has stale tool bindings (not currently registered)",
            stale_bindings=stale,
        )

    for tool_name, tool in raw_tools.items():
        binding = bindings.get(tool_name)
        if binding is None:
            continue
        capability_id = binding["capability_id"]
        merged_tags = set(getattr(tool, "tags", set()) or set())
        merged_tags.add(f"{tag_prefix}{capability_id}")
        merged_meta = _merge_capability_meta(
            getattr(tool, "meta", None),
            capability_id=capability_id,
            capability_version=binding["capability_version"],
            stability=contract["capability_stability"],
            contract_version=contract["contract_version"],
        )
        merged_meta.update(
            {
                "capability_input_schema": binding["input_schema"],
                "capability_output_schema": binding["output_schema"],
                "capability_contract_hash": contract["contract_hash"],
            }
        )
        mcp.add_tool_transformation(
            tool_name,
            ToolTransformConfig(
                tags=merged_tags,
                meta=merged_meta,
                enabled=True,
            ),
        )

    catalog = build_capability_catalog(raw_tool_names)
    logger.info(
        "✅ Capability contract installed",
        contract_version=contract["contract_version"],
        contract_hash=contract["contract_hash"],
        capability_count=catalog["capability_count"],
        bound_tool_count=catalog["bound_tool_count"],
        unbound_tools=catalog["unbound_tools"],
    )
    return catalog


def get_capability_contract_overview() -> dict[str, Any]:
    """Static capability contract summary for info endpoints."""
    contract = load_capability_contract()
    return {
        "contract_version": contract["contract_version"],
        "contract_hash": contract["contract_hash"],
        "declared_capability_count": contract["capability_count"],
        "declared_tool_binding_count": contract["binding_count"],
        "contract_file": contract["contract_file"],
    }


def register_capability_tools(mcp: FastMCP, catalog_ref: dict[str, Any]) -> None:
    """Register contract introspection tools."""

    @mcp.tool(tags={"meta", "contract"})
    async def list_capabilities(ctx: Context = None) -> Dict[str, Any]:
        """Return stable capability catalog for dynamic agent routing."""
        catalog = dict(catalog_ref.get("catalog") or {})
        if not catalog:
            catalog = build_capability_catalog(_get_raw_tools(mcp).keys())

        if ctx:
            await ctx.info(
                "🔧 获取能力契约清单",
                extra={
                    "capability_count": catalog.get("capability_count", 0),
                    "bound_tool_count": catalog.get("bound_tool_count", 0),
                },
            )

        return {
            "summary": (
                "Capability contract loaded: "
                f"{catalog.get('capability_count', 0)} capabilities, "
                f"{catalog.get('bound_tool_count', 0)} bound tools."
            ),
            "component_type": "capability_catalog",
            **catalog,
        }
