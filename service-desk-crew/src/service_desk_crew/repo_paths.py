"""Resolve monorepo root (service-desk-api/) from this package location."""
from __future__ import annotations

from pathlib import Path


def monorepo_root() -> Path:
    # service-desk-api/service-desk-crew/src/service_desk_crew/*.py -> parents[3]
    return Path(__file__).resolve().parents[3]
