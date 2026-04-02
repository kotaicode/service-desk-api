"""Kubernetes diagnostics via kagent MCP (Phase 4) — streamable HTTP + allowlist."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import urlparse

from crewai.tools import BaseTool
from mcp.types import TextContent
from pydantic import BaseModel, Field

from service_desk_crew.repo_config import load_mcp_endpoints

log = logging.getLogger(__name__)

# Markers for flow routing (flow.py)
DIAGNOSTICS_UNAVAILABLE_PREFIX = "[DIAGNOSTICS_UNAVAILABLE]"

_DEFAULT_MCP_TIMEOUT_S = 120.0


def _mcp_url() -> str:
    return (os.environ.get("KAGENT_MCP_URL") or "").strip()


def _headers() -> dict[str, str]:
    h: dict[str, str] = {}
    token = (os.environ.get("KAGENT_MCP_TOKEN") or "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _timeout_seconds() -> float:
    raw = os.environ.get("MCP_TOOL_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_MCP_TIMEOUT_S
    try:
        return max(5.0, float(raw))
    except ValueError:
        return _DEFAULT_MCP_TIMEOUT_S


def _allowlist() -> tuple[set[str], list[str], str]:
    """Returns (allowed_tool_names, allowed_invoker_refs, diagnostics_agent_ref)."""
    cfg = load_mcp_endpoints()
    tools = cfg.get("allowed_tools") or []
    allowed = {str(t) for t in tools if t}
    refs = cfg.get("allowed_invoke_agent_refs") or []
    allowed_refs = [str(r) for r in refs if r]
    diag_ref = (
        (os.environ.get("KAGENT_DIAGNOSTICS_AGENT_REF") or "").strip()
        or str(cfg.get("diagnostics_agent_ref") or "").strip()
    )
    return allowed, allowed_refs, diag_ref


def _invoke_allowed(agent_ref: str) -> bool:
    _, allowed_refs, _ = _allowlist()
    if not allowed_refs:
        return True
    return agent_ref in allowed_refs


async def _call_tool_async(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    issue_key: str = "",
) -> str:
    """Call one MCP tool; enforce allowlist; return text for CrewAI."""
    url = _mcp_url()
    if not url:
        return f"{DIAGNOSTICS_UNAVAILABLE_PREFIX} KAGENT_MCP_URL is not set."

    allowed, _, _ = _allowlist()
    if tool_name not in allowed:
        log.warning(
            "mcp tool blocked by allowlist tool=%s issue_key=%s allowlist=%s",
            tool_name,
            issue_key,
            sorted(allowed),
        )
        return f"{DIAGNOSTICS_UNAVAILABLE_PREFIX} Tool {tool_name!r} is not in the allowlist."

    if tool_name == "invoke_agent":
        ref = str(arguments.get("agent") or "")
        if ref and not _invoke_allowed(ref):
            log.warning(
                "mcp invoke_agent blocked agent ref=%s issue_key=%s",
                ref,
                issue_key,
            )
            return f"{DIAGNOSTICS_UNAVAILABLE_PREFIX} Agent {ref!r} is not allowed by config."

    timeout = _timeout_seconds()
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    log.info(
        "mcp tool call start tool=%s issue_key=%s timeout_s=%s",
        tool_name,
        issue_key,
        timeout,
    )

    async def _run() -> str:
        async with streamablehttp_client(
            url,
            headers=_headers() or None,
            timeout=timeout,
            sse_read_timeout=timeout,
        ) as streams:
            read, write, _ = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments),
                    timeout=timeout + 10.0,
                )
                parts: list[str] = []
                if getattr(result, "content", None):
                    for block in result.content:
                        if isinstance(block, TextContent):
                            parts.append(block.text)
                        elif isinstance(block, dict) and block.get("text"):
                            parts.append(str(block["text"]))
                        else:
                            t = getattr(block, "text", None)
                            if t:
                                parts.append(str(t))
                text = "\n".join(parts) if parts else ""
                if getattr(result, "isError", False) and not text:
                    text = str(result)
                return text

    try:
        out = await _run()
        log.info(
            "mcp tool call ok tool=%s issue_key=%s bytes=%s",
            tool_name,
            issue_key,
            len(out) if out else 0,
        )
        return out or "(empty tool result)"
    except asyncio.TimeoutError:
        log.error("mcp tool call timeout tool=%s issue_key=%s", tool_name, issue_key)
        return f"{DIAGNOSTICS_UNAVAILABLE_PREFIX} MCP call timed out ({tool_name})."
    except Exception as e:
        log.exception("mcp tool call failed tool=%s issue_key=%s", tool_name, issue_key)
        return f"{DIAGNOSTICS_UNAVAILABLE_PREFIX} MCP error ({tool_name}): {e!s}"


def call_mcp_tool_sync(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    issue_key: str = "",
) -> str:
    """Sync wrapper for CrewAI tools (new event loop per call)."""
    return asyncio.run(_call_tool_async(tool_name, arguments, issue_key=issue_key))


class KagentDiagnosticsInput(BaseModel):
    namespace: str = Field(default="", description="Kubernetes namespace")
    service_name: str = Field(default="", description="Service or workload name")
    issue_key: str = Field(default="", description="Correlation (Jira issue key)")


class KagentKubernetesDiagnosticsTool(BaseTool):
    """Single high-level tool: invoke kagent/k8s-agent (or configured ref) with a read-only task."""

    name: str = "collect_kubernetes_diagnostics_kagent"
    description: str = (
        "Gather read-only Kubernetes evidence via kagent MCP (pods, deployments, events) "
        "for the given namespace and service/workload. Call exactly once per task."
    )
    args_schema: type[BaseModel] = KagentDiagnosticsInput

    def _run(self, namespace: str = "", service_name: str = "", issue_key: str = "") -> str:
        issue_key = (issue_key or "").strip() or (os.environ.get("SERVICE_DESK_ISSUE_KEY") or "").strip()
        url = _mcp_url()
        if not url:
            return (
                f"{DIAGNOSTICS_UNAVAILABLE_PREFIX} KAGENT_MCP_URL is not set. "
                "Configure port-forward to kagent-controller and set the URL in .env."
            )

        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return f"{DIAGNOSTICS_UNAVAILABLE_PREFIX} KAGENT_MCP_URL is not a valid URL."

        _, _, diag_ref = _allowlist()
        if not diag_ref:
            return (
                f"{DIAGNOSTICS_UNAVAILABLE_PREFIX} Set diagnostics_agent_ref in config/mcp_endpoints.yml "
                "or KAGENT_DIAGNOSTICS_AGENT_REF (e.g. kagent/k8s-agent)."
            )

        ns = (namespace or "").strip() or "(unspecified)"
        svc = (service_name or "").strip() or "(unspecified)"
        task = (
            "You are assisting L1 service desk triage. "
            "Use read-only tools only. Do not apply, delete, patch, or scale resources. "
            f"Namespace: {ns}. Workload/service focus: {svc}. "
            "Produce a compact evidence bundle: "
            "(1) pods in the namespace (status, restarts if relevant); "
            "(2) deployments/statefulsets that match the workload name if identifiable; "
            "(3) describe 1–3 unhealthy or notable pods if any; "
            "(4) recent Warning/Error events in the namespace (last ~60–120 minutes). "
            "If namespace is unspecified, say what is missing. "
            "Keep output concise and factual."
        )
        args = {"agent": diag_ref, "task": task}
        return call_mcp_tool_sync("invoke_agent", args, issue_key=issue_key)


def diagnostics_tools_for_crew() -> list[BaseTool]:
    """Tools attached to the diagnostics collector (stub vs kagent MCP)."""
    stub = os.environ.get("USE_DIAGNOSTICS_STUB", "").lower() in ("1", "true", "yes")
    if stub:
        from service_desk_crew.tools.diagnostics_stub import DiagnosticsStubTool

        return [DiagnosticsStubTool()]

    return [KagentKubernetesDiagnosticsTool()]
