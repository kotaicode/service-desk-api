from __future__ import annotations

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from service_desk_crew.config.llm_factory import get_llm
from service_desk_crew.tools.mcp_k8s import diagnostics_tools_for_crew


@CrewBase
class ServiceDeskCrew:
    """L1 agents: Intake, Diagnostics (stub), Synthesis."""

    agents: list[Agent]
    tasks: list[Task]

    @agent
    def intake_specialist(self) -> Agent:
        return Agent(
            config=self.agents_config["intake_specialist"],  # type: ignore[index]
            tools=[],
            llm=get_llm(),
            verbose=True,
        )

    @agent
    def diagnostics_collector(self) -> Agent:
        return Agent(
            config=self.agents_config["diagnostics_collector"],  # type: ignore[index]
            tools=diagnostics_tools_for_crew(),
            llm=get_llm(),
            verbose=True,
        )

    @agent
    def synthesis_writer(self) -> Agent:
        return Agent(
            config=self.agents_config["synthesis_writer"],  # type: ignore[index]
            tools=[],
            llm=get_llm(),
            verbose=True,
        )

    @task
    def intake_task(self) -> Task:
        return Task(config=self.tasks_config["intake_task"])  # type: ignore[index]

    @task
    def diagnostics_task(self) -> Task:
        return Task(config=self.tasks_config["diagnostics_task"])  # type: ignore[index]

    @task
    def synthesis_task(self) -> Task:
        return Task(config=self.tasks_config["synthesis_task"])  # type: ignore[index]

    @crew
    def crew(self) -> Crew:
        """Full sequential crew (optional for `crewai run` smoke tests)."""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
