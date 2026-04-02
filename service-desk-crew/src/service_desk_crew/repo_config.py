"""Load POC YAML from monorepo config/ (tech spec §10)."""
from __future__ import annotations

from pathlib import Path

import yaml

from service_desk_crew.repo_paths import monorepo_root


def load_required_fields_yaml() -> str:
    path = monorepo_root() / "config" / "required_fields.yml"
    return path.read_text(encoding="utf-8")


def load_routing_keywords() -> list[str]:
    path = monorepo_root() / "config" / "routing.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("keywords") or []
    return [str(k) for k in raw if k]


def is_k8s_ish(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def load_mcp_endpoints() -> dict:
    """Load config/mcp_endpoints.yml (Phase 4 kagent MCP allowlist)."""
    path = monorepo_root() / "config" / "mcp_endpoints.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    return data
