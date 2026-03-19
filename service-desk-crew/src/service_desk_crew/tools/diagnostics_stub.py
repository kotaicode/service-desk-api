"""Phase 3: fixed diagnostic bundle — no network (tech spec §6.2)."""
from __future__ import annotations

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class DiagnosticsStubInput(BaseModel):
    namespace: str = Field(default="", description="Kubernetes namespace")
    service_name: str = Field(default="", description="Service or workload name")


class DiagnosticsStubTool(BaseTool):
    name: str = "run_kubernetes_diagnostics_stub"
    description: str = (
        "Returns a placeholder diagnostic bundle for the POC (no cluster access). "
        "Use when namespace and service are known."
    )
    args_schema: Type[BaseModel] = DiagnosticsStubInput

    def _run(self, namespace: str = "", service_name: str = "") -> str:
        ns = namespace or "(unspecified)"
        svc = service_name or "(unspecified)"
        return (
            "[POC stub — Phase 4 will attach real kagent MCP data]\n"
            f"Namespace: {ns}\n"
            f"Workload: {svc}\n"
            "- Simulated: no unhealthy pods reported in stub mode.\n"
            "- Simulated: no recent Warning events in stub mode.\n"
            "Replace with live cluster queries in Phase 4."
        )
