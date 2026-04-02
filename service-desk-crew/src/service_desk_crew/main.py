#!/usr/bin/env python
"""CLI (`crewai run`) and worker entry: `run_l1_support`."""
from __future__ import annotations

import logging
import os
import sys
import warnings
from typing import Any

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

log = logging.getLogger(__name__)


def run_l1_support(issue_key: str, job_id: int | None = None) -> str:
    """Run L1 CrewAI Flow for one ticket (worker calls this after claim + idempotency check).

    Returns a flow outcome constant from service_desk_crew.flow (e.g. full_resolution).
    """
    from service_desk_crew.flow import L1SupportFlow

    prev_issue = os.environ.get("SERVICE_DESK_ISSUE_KEY")
    os.environ["SERVICE_DESK_ISSUE_KEY"] = issue_key
    flow = L1SupportFlow()
    try:
        flow.kickoff(inputs={"issue_key": issue_key, "job_id": job_id})
    finally:
        if prev_issue is not None:
            os.environ["SERVICE_DESK_ISSUE_KEY"] = prev_issue
        else:
            os.environ.pop("SERVICE_DESK_ISSUE_KEY", None)
    outcome = flow.state.outcome
    if not outcome:
        log.error("flow finished without outcome issue_key=%s job_id=%s", issue_key, job_id)
        raise RuntimeError("L1 flow completed without setting state.outcome")
    return outcome


def run() -> None:
    """`crewai run` / package script: uses SERVICE_DESK_ISSUE_KEY or DEMO-1."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    issue_key = os.environ.get("SERVICE_DESK_ISSUE_KEY", "DEMO-1")
    try:
        run_l1_support(issue_key, job_id=None)
    except Exception as e:
        raise Exception(f"An error occurred while running the L1 flow: {e}") from e


def train() -> None:
    from service_desk_crew.crew import ServiceDeskCrew

    inputs = {"topic": "smoke", "current_year": "2026"}
    try:
        ServiceDeskCrew().crew().train(
            n_iterations=int(sys.argv[1]), filename=sys.argv[2], inputs=inputs
        )
    except Exception as e:
        raise Exception(f"An error occurred while training: {e}") from e


def replay() -> None:
    from service_desk_crew.crew import ServiceDeskCrew

    try:
        ServiceDeskCrew().crew().replay(task_id=sys.argv[1])
    except Exception as e:
        raise Exception(f"An error occurred while replaying: {e}") from e


def test() -> None:
    from service_desk_crew.crew import ServiceDeskCrew

    inputs = {"topic": "smoke", "current_year": "2026"}
    try:
        ServiceDeskCrew().crew().test(
            n_iterations=int(sys.argv[1]), eval_llm=sys.argv[2], inputs=inputs
        )
    except Exception as e:
        raise Exception(f"An error occurred while testing: {e}") from e


def run_with_trigger() -> None:
    import json

    from service_desk_crew.crew import ServiceDeskCrew

    if len(sys.argv) < 2:
        raise Exception("No trigger payload provided.")

    try:
        trigger_payload = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        raise Exception("Invalid JSON payload") from e

    inputs = {
        "crewai_trigger_payload": trigger_payload,
        "topic": "",
        "current_year": "",
    }
    try:
        return ServiceDeskCrew().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"An error occurred while running with trigger: {e}") from e
