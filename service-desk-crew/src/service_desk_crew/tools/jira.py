"""
Jira REST helpers and CrewAI tools (tech spec §7.1). Credentials from env or explicit args.
"""
from __future__ import annotations

import os
from typing import Any, Type

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


def _adf_from_plain_text(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def jira_get_issue(
    issue_key: str,
    base_url: str | None = None,
    email: str | None = None,
    api_token: str | None = None,
) -> dict[str, Any]:
    base_url = (base_url or os.environ.get("JIRA_BASE_URL", "")).rstrip("/")
    email = email or os.environ.get("JIRA_EMAIL", "")
    api_token = api_token or os.environ.get("JIRA_API_TOKEN", "")
    url = f"{base_url}/rest/api/3/issue/{issue_key}"
    auth = (email, api_token)
    headers = {"Accept": "application/json"}
    resp = requests.get(url, auth=auth, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    fields = data.get("fields", {})
    summary = fields.get("summary") or ""
    desc = fields.get("description")
    if isinstance(desc, dict):
        content = desc.get("content") or []
        parts: list[str] = []
        for block in content:
            if block.get("type") == "paragraph":
                for c in block.get("content") or []:
                    if c.get("type") == "text":
                        parts.append(c.get("text", ""))
        description = " ".join(parts)
    else:
        description = str(desc) if desc else ""
    return {
        "key": data.get("key"),
        "summary": summary,
        "description": description,
        "fields": fields,
    }


def format_ticket_for_agents(issue: dict[str, Any]) -> str:
    """Compact text for LLM intake (INFO-level logging should not dump full body per §3.5)."""
    summary = issue.get("summary") or ""
    desc = issue.get("description") or ""
    return f"Summary:\n{summary}\n\nDescription:\n{desc}\n"


def jira_post_comment(
    issue_key: str,
    body: str,
    base_url: str | None = None,
    email: str | None = None,
    api_token: str | None = None,
    internal: bool = True,
) -> None:
    base_url = (base_url or os.environ.get("JIRA_BASE_URL", "")).rstrip("/")
    email = email or os.environ.get("JIRA_EMAIL", "")
    api_token = api_token or os.environ.get("JIRA_API_TOKEN", "")
    url = f"{base_url}/rest/api/3/issue/{issue_key}/comment"
    auth = (email, api_token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    payload: dict[str, Any] = {"body": _adf_from_plain_text(body)}
    if internal:
        payload["jsdPublic"] = False
    resp = requests.post(url, auth=auth, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()


class JiraGetIssueToolInput(BaseModel):
    issue_key: str = Field(..., description="Jira issue key, e.g. PROJ-123")


class JiraGetIssueTool(BaseTool):
    name: str = "jira_get_issue"
    description: str = "Fetch Jira issue summary and description for the given issue key."
    args_schema: Type[BaseModel] = JiraGetIssueToolInput

    def _run(self, issue_key: str) -> str:
        data = jira_get_issue(issue_key)
        return format_ticket_for_agents(
            {"summary": data.get("summary", ""), "description": data.get("description", "")}
        )


class JiraPostCommentToolInput(BaseModel):
    issue_key: str = Field(..., description="Jira issue key")
    body: str = Field(..., description="Plain-text comment body (markdown allowed)")


class JiraPostCommentTool(BaseTool):
    name: str = "jira_post_comment"
    description: str = "Post an internal comment on a Jira issue."
    args_schema: Type[BaseModel] = JiraPostCommentToolInput

    def _run(self, issue_key: str, body: str) -> str:
        jira_post_comment(issue_key, body, internal=True)
        return "Comment posted."
