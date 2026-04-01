"""L1 CrewAI Flow — tech spec §8.2."""
from __future__ import annotations

import json
import logging
from typing import Any

from crewai import Crew, Process
from crewai.flow import Flow, listen, router, start
from pydantic import BaseModel, Field

from service_desk_crew.crew import ServiceDeskCrew
from service_desk_crew.repo_config import is_k8s_ish, load_required_fields_yaml, load_routing_keywords
from service_desk_crew.tools.jira import format_ticket_for_agents, jira_get_issue, jira_post_comment

log = logging.getLogger(__name__)

# Terminal outcomes for worker: only FLOW_OUTCOME_FULL_RESOLUTION inserts processed_issues.
FLOW_OUTCOME_FULL_RESOLUTION = "full_resolution"
FLOW_OUTCOME_AWAITING_CUSTOMER = "awaiting_customer"
FLOW_OUTCOME_UNSUPPORTED = "completed_unsupported"


class L1State(BaseModel):
    id: str = ""
    issue_key: str = ""
    job_id: int | None = None
    ticket_raw: str = ""
    required_fields_yaml: str = ""
    intake_text: str = ""
    can_proceed: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    namespace: str = ""
    service_name: str = ""
    diagnostics_artifact: str = ""
    synthesis_output: str = ""
    outcome: str = ""


def _parse_first_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in intake output")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("intake JSON is not an object")
    return obj


def _apply_intake_dict(state: L1State, data: dict[str, Any]) -> None:
    state.can_proceed = bool(data.get("can_proceed"))
    mf = data.get("missing_fields") or []
    state.missing_fields = [str(x) for x in mf] if isinstance(mf, list) else []
    cq = data.get("clarifying_questions") or []
    state.clarifying_questions = [str(x) for x in cq] if isinstance(cq, list) else []
    state.namespace = str(data.get("namespace") or "").strip()
    state.service_name = str(data.get("service_name") or "").strip()


class L1SupportFlow(Flow[L1State]):
    """load_ticket → intake → route → (missing | unsupported | k8s path) → comment → done."""

    initial_state = L1State

    @start()
    def load_ticket(self) -> str:
        log.info("flow step load_ticket issue_key=%s job_id=%s", self.state.issue_key, self.state.job_id)
        issue = jira_get_issue(self.state.issue_key)
        self.state.ticket_raw = format_ticket_for_agents(
            {"summary": issue.get("summary", ""), "description": issue.get("description", "")}
        )
        self.state.required_fields_yaml = load_required_fields_yaml()
        return self.state.ticket_raw

    @listen(load_ticket)
    def intake_check(self) -> str:
        log.info("flow step intake_check issue_key=%s", self.state.issue_key)
        base = ServiceDeskCrew()
        crew = Crew(
            agents=[base.intake_specialist()],
            tasks=[base.intake_task()],
            process=Process.sequential,
            verbose=True,
        )
        result = crew.kickoff(
            inputs={
                "issue_key": self.state.issue_key,
                "ticket_context": self.state.ticket_raw,
                "required_fields_yaml": self.state.required_fields_yaml,
            }
        )
        self.state.intake_text = str(result)
        try:
            data = _parse_first_json_object(self.state.intake_text)
            _apply_intake_dict(self.state, data)
        except Exception as e:
            log.warning(
                "intake JSON parse failed issue_key=%s error=%s; treating as cannot proceed",
                self.state.issue_key,
                e,
            )
            self.state.can_proceed = False
            self.state.missing_fields = ["namespace", "service_name"]
            self.state.clarifying_questions = ["Provide a clear namespace and service/workload name."]
        return self.state.intake_text

    @router(intake_check)
    def route_after_intake(self) -> str:
        if not self.state.can_proceed:
            return "path_missing_info"
        blob = f"{self.state.ticket_raw}\n{self.state.intake_text}"
        if is_k8s_ish(blob, load_routing_keywords()):
            return "path_k8s"
        return "path_unsupported"

    @listen("path_missing_info")
    def post_missing_info(self) -> str:
        log.info("flow path missing_info issue_key=%s", self.state.issue_key)
        lines = []
        if self.state.missing_fields:
            lines.append("Please provide:")
            lines.extend(f"- {x}" for x in self.state.missing_fields)
        if self.state.clarifying_questions:
            lines.append("")
            lines.extend(self.state.clarifying_questions)
        body = "\n".join(lines) if lines else "Please add namespace and service/workload details for triage."
        jira_post_comment(self.state.issue_key, body, internal=True)
        self.state.outcome = FLOW_OUTCOME_AWAITING_CUSTOMER
        return "posted_missing_info"

    @listen("path_unsupported")
    def post_unsupported(self) -> str:
        log.info("flow path unsupported issue_key=%s", self.state.issue_key)
        body = (
            "This POC automation only covers Kubernetes workload / service degradation cases. "
            "For other request types, please route to your team’s standard process."
        )
        jira_post_comment(self.state.issue_key, body, internal=True)
        self.state.outcome = FLOW_OUTCOME_UNSUPPORTED
        return "posted_unsupported"

    @listen("path_k8s")
    def run_diagnostics(self) -> str:
        log.info("flow step diagnostics issue_key=%s", self.state.issue_key)
        base = ServiceDeskCrew()
        crew = Crew(
            agents=[base.diagnostics_collector()],
            tasks=[base.diagnostics_task()],
            process=Process.sequential,
            verbose=True,
        )
        result = crew.kickoff(
            inputs={
                "issue_key": self.state.issue_key,
                "namespace": self.state.namespace,
                "service_name": self.state.service_name,
            }
        )
        self.state.diagnostics_artifact = str(result)
        return self.state.diagnostics_artifact

    @listen(run_diagnostics)
    def run_synthesis(self) -> str:
        log.info("flow step synthesis issue_key=%s", self.state.issue_key)
        base = ServiceDeskCrew()
        crew = Crew(
            agents=[base.synthesis_writer()],
            tasks=[base.synthesis_task()],
            process=Process.sequential,
            verbose=True,
        )
        result = crew.kickoff(
            inputs={
                "issue_key": self.state.issue_key,
                "ticket_context": self.state.ticket_raw,
                "diagnostics_artifact": self.state.diagnostics_artifact,
            }
        )
        self.state.synthesis_output = str(result)
        return self.state.synthesis_output

    @listen(run_synthesis)
    def post_final_comment(self) -> str:
        log.info("flow step post_comment issue_key=%s", self.state.issue_key)
        jira_post_comment(self.state.issue_key, self.state.synthesis_output, internal=True)
        self.state.outcome = FLOW_OUTCOME_FULL_RESOLUTION
        return "posted_final"
