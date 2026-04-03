# service_desk_crew (Service Desk POC)

CrewAI package for the **service-desk-api** monorepo: **L1SupportFlow** in `flow.py` (Intake → route → diagnostics stub → synthesis → Jira comment). The worker imports `service_desk_crew` after `pip install -e ./service-desk-crew` from the repo root. See the repository **README** for env vars and smoke tests.

Scaffold source: `crewai create crew service_desk_crew` (CrewAI 1.x). Below is the upstream crew template blurb.

## Installation

Ensure you have Python >=3.10 <3.14 installed on your system. This project uses [UV](https://docs.astral.sh/uv/) for dependency management and package handling, offering a seamless setup and execution experience.

First, if you haven't already, install uv:

```bash
pip install uv
```

Next, navigate to your project directory and install the dependencies:

(Optional) Lock the dependencies and install them by using the CLI command:
```bash
crewai install
```
### Customizing

**Add your `OPENAI_API_KEY` into the `.env` file**

- Modify `src/service_desk_crew/config/agents.yaml` to define your agents
- Modify `src/service_desk_crew/config/tasks.yaml` to define your tasks
- Modify `src/service_desk_crew/crew.py` to add your own logic, tools and specific args
- Modify `src/service_desk_crew/main.py` to add custom inputs for your agents and tasks

## Running the Project

To kickstart your crew of AI agents and begin task execution, run this from the root folder of your project:

```bash
$ crewai run
```

This command runs `main.run()`, which executes **L1SupportFlow** for `SERVICE_DESK_ISSUE_KEY` (default `DEMO-1`). Set `JIRA_*`, `OPENAI_API_KEY`, and run from this directory so YAML paths resolve.

## Understanding Your Crew

The service_desk_crew Crew is composed of multiple AI agents, each with unique roles, goals, and tools. These agents collaborate on a series of tasks, defined in `config/tasks.yaml`, leveraging their collective skills to achieve complex objectives. The `config/agents.yaml` file outlines the capabilities and configurations of each agent in your crew.

## Support

For support, questions, or feedback regarding the ServiceDeskCrew Crew or crewAI.
- Visit our [documentation](https://docs.crewai.com)
- Reach out to us through our [GitHub repository](https://github.com/joaomdmoura/crewai)
- [Join our Discord](https://discord.com/invite/X4JWnZnxPb)
- [Chat with our docs](https://chatg.pt/DWjSBZn)

Let's create wonders together with the power and simplicity of crewAI.
