# TECHNICAL SPECIFICATION  
# Service Desk POC — L1 Support Automation  
**CrewAI + kagent + Jira Service Management**  
v1.1 — March 2026  

This specification describes a **reference proof of concept (POC)** for an agentic L1 support desk. **Goal:** validate the end-to-end flow (ticket → analysis → comment) before broadening to more ticket types and production hardening. It is maintained with the **service-desk-api** repository for contributors and anyone reusing or extending the design.

A central integration point for **Kubernetes diagnostics** is **kagent** exposed as an **MCP (Model Context Protocol)** server: agents call read-only cluster tools through **`KAGENT_MCP_URL`** (see §§3.2, 6.2, 7.2) without embedding `kubectl` in the worker. This keeps the POC safe (no cluster writes) and interoperable with standard MCP clients.

---

## 1. Introduction

This document defines the **minimum proof of concept (POC)** for an agentic L1 support desk. When a Jira Service Management ticket is created, a chain of AI agents checks that the ticket has enough information, runs read-only diagnostics (Kubernetes, and optionally logs and metrics), and posts an internal comment with findings and suggested next steps. A human performs any fixes; the POC does not auto-remediate.

**Automatic trigger:** Whenever the **user** creates a Jira Service Management ticket, the agent pipeline is **automatically triggered** to process that ticket. No manual kick-off or user action is required beyond creating the ticket; Jira Automation sends a webhook to the system; the Go API stores the job in the database and workers run the agents.

The POC uses **CrewAI** for multi-agent orchestration (Flows), **kagent** (MCP server) for **read-only Kubernetes diagnostics**, and **Jira Service Management** for triggers and posting results. The structure below is enough to start implementation.

**Local first, then cluster:** Implement and test **on a developer machine first** (same architecture and concepts: webhook → API → DB → worker → CrewAI → Jira and optional kagent). Use a **single set of environment variable names** loaded from **one `.env` file** locally. When moving to the server or Kubernetes, **map the same variable names** into the process environment via **Kubernetes Secrets** (or a secret manager). **Application code reads only `os.Getenv` / `os.environ`** — no separate “local vs cluster” code paths; switching environments is **only** how env is supplied (`.env` + process start vs Secret → Pod env).

### 1.1 POC Goals

- **Automatic processing:** Whenever the user creates a Jira ticket, the agent is automatically triggered (no manual step). Ticket created → webhook → agents run → internal comment with triage + evidence + recommended next steps.
- **One ticket class:** “Service degradation / workload unhealthy in Kubernetes” (e.g. crashloop, 5xx, pods not ready).
- **Two paths:** (1) Ticket has enough info → run diagnostics → post analysis. (2) Ticket missing required info → post a comment asking for details and stop.
- **Read-only:** No writes to the cluster; only get/describe/list and query logs/metrics.
- **No infinite loops:** Idempotency so the bot does not re-trigger on its own comments.

### 1.2 Non-Goals (POC)

- Auto-remediation or any write actions on Kubernetes.
- Multiple ticket types or full routing taxonomy.
- Public-facing status pages or customer notifications (internal comment only).
- Full production hardening (RBAC per agent, full observability pipeline).
- Support for MFA-protected flows or complex auth.

*These items are explicitly out of scope for the POC but may be included in a future implementation or as extensions to this system.*

---

## 2. System Overview

### 2.1 High-Level Architecture

```
[Jira Service Management]
         │
         │ Automation: "When request created" → Webhook
         ▼
[Go API] ──► validates secret, parses issue_key from body, stores job in DB (pending); does NOT call Jira
         │
         ▼
[Database, e.g. PostgreSQL] ──► Job table (issueKey, status, payload) + idempotency (processed markers)
         │
         ▼
[Worker(s)] pull pending jobs from DB ──► [service_desk_crew package in service-desk-crew/ — CrewAI Flow] Intake → Route (optional) → Diagnostics → Synthesis → Post comment
         │
         ├──► [Jira API] (read issue, post comment)
         ├──► [kagent MCP] (Kubernetes: pods, events, describe)
         └──► [Loki / Mimir] (optional, direct HTTP for POC)
         │
         ▼
[Database] ──► Update job status; store processed marker per issueKey to avoid re-processing
```

- **Go API:** Simple backend that exposes the webhook endpoint; validates webhook secret, fetches full ticket from Jira if needed, and stores the request as a job (e.g. `pending`) in the database. Returns quickly to Jira.
- **Database:** Single store for the job queue (e.g. table with `issue_key`, `status`, `payload`, `created_at`) and idempotency (processed markers per issue key). Workers poll for pending jobs and update status.
- **Worker:** Polls jobs from the database and **invokes** the **`service_desk_crew`** Python package (CrewAI Flow + crews), installed from **`service-desk-crew/`** (see §2.1.1). It does **not** embed crew source. **Locally:** a process. **On cluster:** a Deployment.
- **`service-desk-crew/` (CrewAI):** Top-level folder in the **service-desk-api** monorepo, **outside** `worker/`, holding the **entire standard layout from `crewai create crew service_desk_crew`** (typically under **`src/service_desk_crew/`** — see §2.1.1). **Import name:** `service_desk_crew`. Add **`flow.py`** and **`config/llm_factory.py`** for the L1 branching pipeline per §8.2. Repo-root **`config/required_fields.yml`**, **`config/routing.yml`**, **`config/mcp_endpoints.yml`** sit **beside** `worker/` and `service-desk-crew/` for POC-wide rules.
- **kagent:** MCP for read-only K8s. **Locally:** `KAGENT_MCP_URL` → kind/minikube, dev cluster, or mock. **On cluster:** in-target-cluster Service.

### 2.1.1 Monorepo layout (service-desk-api)

```
service-desk-api/
├── api/                         # Go webhook receiver
├── worker/                      # Job poller; pip install -e ./service-desk-crew; imports service_desk_crew
├── service-desk-crew/           # crewai create crew service_desk_crew (Phase 3 ✅); extend here in later phases
│   ├── pyproject.toml           # Installable package service_desk_crew; crewai; Python ≥ 3.10
│   ├── README.md                # From scaffold (optional)
│   ├── knowledge/               # From scaffold (optional)
│   └── src/service_desk_crew/   # import service_desk_crew — see nested tree below
├── config/                      # POC YAML: required_fields, routing, mcp_endpoints
├── db/                          # SQL migrations (if used)
├── .env.example
└── …
```

**`service-desk-crew/src/service_desk_crew/` (standard crew layout + L1 files):**

```
service-desk-crew/src/service_desk_crew/
├── crew.py                      # @CrewBase
├── main.py                      # crewai run / local kickoff
├── flow.py                      # L1SupportFlow (§8.2) — add after scaffold
├── config/
│   ├── agents.yaml
│   ├── tasks.yaml
│   └── llm_factory.py           # §10 — add after scaffold
└── tools/                       # jira.py, diagnostics_stub, Phase 4+ mcp_k8s, optional loki/mimir
```

**Why Go API + Worker (not only worker or only Go)?** We use a **Go API** to receive the Jira webhook and enqueue jobs so the webhook can return within seconds; if we ran the full CrewAI pipeline inside that request, it would take minutes and the webhook would time out. We use a **separate Worker** (Python) to run CrewAI because the agent flow is long-running and must not block the webhook handler; the worker polls the queue and processes jobs asynchronously. An alternative would be a **worker-only** design that polls Jira for new tickets (no webhook, no Go API), but that adds delay and extra Jira API load; we chose webhook + Go API + worker for event-driven, immediate triggering and a clear split between “receive and enqueue” (Go) and “run agents” (Python/CrewAI).

### 2.2 Deployment Topology

- **Local first:** Go API + Worker as **processes**; **`.env`** + **PostgreSQL**; Jira webhook via **tunnel** (ngrok, etc.) or manual job tests — see §3.8.
- **Then cluster:** Same container images; **same env var names** from **Kubernetes Secrets** — **no application code changes** (§3.8).
- **Go API:** Must be reachable by Jira (tunnel locally; Ingress on cluster).
- **Secrets:** **Local** = `.env` (gitignored). **Cluster** = Secret keys **match** `.env` variable names.

### 2.3 Why a webhook receiver (Go API)? Why not CrewAI talking to Jira for “todo” tasks?

**Why something must receive the webhook**

Jira Automation sends an HTTP POST (webhook) when a ticket is created. That request must be handled by some service that:

1. **Responds quickly** — Jira (and many webhook providers) expect a fast HTTP response (e.g. within seconds). If the handler runs the full CrewAI flow (Intake → Diagnostics → Synthesis) in the same request, the call can take minutes and may time out or be retried, causing duplicate work.
2. **Enqueues work for async processing** — So the flow runs in the background (worker polling the database) and the webhook handler only validates the request, stores the job, and returns.

So we need a **receive → enqueue → return** component. In this spec that component is the **Go API**. It could instead be a small **Python** HTTP service (e.g. FastAPI) that does the same thing: validate secret, parse `issue_key`, insert job into DB, return 200. The choice of Go is for a thin, fast, language-separate receiver; the important part is “don’t run CrewAI in the webhook request.”

**Why not have CrewAI / the worker receive the webhook directly?**

We could. The worker would expose an HTTP endpoint, receive the webhook, and then either (a) run the flow in the same request (bad: long response, timeouts) or (b) enqueue the job (e.g. write to DB) and return 200, then process in another thread or process. Option (b) is valid and avoids a separate Go service: one Python app would do “HTTP server + enqueue” and “poll DB + run CrewAI.” The spec keeps **webhook receiver** (Go API) and **worker** (Python + CrewAI) separate for clarity and so the receiver stays minimal and stateless.

**Why not skip the webhook and have CrewAI poll Jira for “todo” tasks?**

We could have the worker (or a scheduler) periodically call the Jira API (e.g. “issues created in the last N minutes” or “status = Open”) and run the CrewAI flow for each. That would remove the need for a webhook receiver and for Jira Automation.

Trade-offs of polling:

- **Delay** — New tickets are only picked up at the next poll (e.g. every 1–5 minutes).
- **Idempotency** — We still need to record “already processed” (e.g. in our DB) so we don’t re-run the flow for the same ticket; that’s the same as today.
- **Jira as queue** — Jira isn’t a job queue: no guaranteed delivery, rate limits apply, and we’d be using Jira’s API for something it wasn’t designed for.

The **webhook + receiver + DB queue** design gives event-driven, immediate triggering and a clear separation: Jira pushes once per ticket; our receiver enqueues; workers pull and run CrewAI. So the spec uses a Go (or replaceable) backend for the Jira push notification to achieve quick response and async CrewAI runs; “CrewAI communicating with Jira for todo tasks” via polling is an alternative but is not chosen here for immediacy and clarity.

---

## 3. Technical Implementation Plan

This section describes technologies, implementation phases, authentication across the full flow (Jira → Go API → database → worker → kagent → CrewAI → Jira comments), security measures, core entities needed to access tickets, and a deployment topology that can be applied in any organization and any cluster.

### 3.1 Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Trigger** | Jira Service Management + Jira Automation | Fire "Send web request" when a work item (request) is created. |
| **Webhook / API** | Go (net/http or Chi/Gin) | Validate webhook secret, parse payload, fetch ticket from Jira if needed, store job in database. |
| **Queue + idempotency** | Database (PostgreSQL) | Job table (issue_key, status, payload); processed markers per issue key. Workers poll for pending jobs. |
| **Worker** | Python 3.10+ | Runs CrewAI Flow; polls database for jobs, connects to Jira API, kagent MCP, optional Loki/Mimir. |
| **Orchestration** | CrewAI (Flows) | Multi-agent flow: Intake → Route → Diagnostics → Synthesis; state and branching. |
| **Kubernetes tools** | kagent (MCP server) | Read-only K8s operations (pods, events, describe) via MCP. |
| **Observability (optional)** | Loki, Mimir | Logs and metrics via direct HTTP or MCP for POC (diagnostic data from the cluster under investigation). |
| **Application logging** | Stdout/stderr, optional JSON | Go API and Worker log key events (webhook, job lifecycle, flow steps, errors); correlation via job_id/issue_key. Consumed by cluster logging (e.g. kubectl logs, or Fluentd/Loki). |
| **Deployment** | Local processes first, then Kubernetes | Local: `.env` + PostgreSQL + tunnel for webhook. Cluster: Helm/Kustomize; same env var names from Secrets. |

### 3.2 Phases of Implementation

**Order:** Complete **all phases locally first** (§3.8 `.env`, PostgreSQL, tunnel as needed). Then **deploy to server/cluster** by mapping the **same variables** into Pod env from Secrets — **no code changes**.

| Phase | Scope | Deliverables |
|-------|--------|--------------|
| **Phase 1 — Foundation (local first)** ✅ *Completed* | Wiring, trigger, logging, single env contract | Go API + Worker as **local processes**; **`.env`** from **`.env.example`** (§3.8). PostgreSQL `DATABASE_URL`. Go API: validate secret, store `{issueKey}` in DB; **logging** as in §3.5. Jira Automation → webhook URL (**tunnel** to localhost) or manual job insert for smoke tests. Worker skeleton polls DB; **logging:** job claimed, errors. |
| **Phase 2 — Jira and idempotency** ✅ *Completed* | Same stack locally | Jira tools using **env-only** credentials (`JIRA_BASE_URL`, `JIRA_API_TOKEN`, etc.). Idempotency + minimal comment flow. **Log:** idempotency skip, comment posted. **Implemented in `service-desk-api`:** `worker/tools/jira.py` (`jira_get_issue`, `jira_post_comment`), `processed_issues` migration, worker flow per §4.3. |
| **Phase 3 — CrewAI Flow** ✅ *Completed* | Full agent pipeline locally | **`service-desk-crew/`** package (**§2.1.1**): standard **`crewai create crew service_desk_crew`** layout under **`src/service_desk_crew/`**, plus **`flow.py`**, **`llm_factory.py`**; **`L1SupportFlow`** end-to-end (Intake → route → diagnostics **stub** → synthesis → Jira comment); Jira tools in **`service_desk_crew.tools.jira`**; repo **`config/required_fields.yml`**, **`config/routing.yml`**; worker invokes **`service_desk_crew`** only; **Log:** flow steps per §3.5. **Implemented in `service-desk-api`.** |
| **Phase 4 — kagent integration** | Real or tunneled MCP | Point `KAGENT_MCP_URL` at kagent (kind/minikube on same machine, or remote dev cluster). Allowlisted MCP tools. **Locally** same code path as cluster — only URL differs via env. |
| **Phase 5 — Optional: Loki/Mimir** | Diagnostic logs/metrics | Optional HTTP to Loki/Mimir from worker; env URLs from same `.env` pattern. |
| **Phase 6 — Deploy to server / cluster** | Packaging only | Container images for Go API and Worker; PostgreSQL (or managed DB) in cluster; **Kubernetes Secrets** populated with **the same keys** as `.env` (copy values securely); Helm/Kustomize `envFrom` secretRef. kagent in cluster; worker Deployment uses in-cluster `KAGENT_MCP_URL`. **Structured logging** doc for `kubectl logs`. Security review (RBAC, network). **No changes to application source** for config loading beyond what Phase 1 already uses (env vars only). |

### 3.3 Authentication (End-to-End Flow)

End-to-end flow: **Jira ticket creation → Jira Automation → Go API → Database → Worker → Jira API / kagent MCP / Loki / Mimir → Jira comment.**

| Connection | Authentication | Notes |
|------------|----------------|-------|
| **Jira → Go API** | Shared secret | Jira Automation "Send web request" sends a header (e.g. `X-Webhook-Secret`) or query param with a shared secret. API validates before storing job in DB. Use HTTPS only. |
| **Go API → Database** | Connection string / credentials | Database URL and credentials from config/secret. TLS for DB in production. |
| **Worker → Database** | Same or separate credentials | Worker polls DB for pending jobs; use same DB credentials or read-only role. |
| **Worker → Jira API** | API token or OAuth 2.0 | Jira Cloud: `JIRA_API_TOKEN` + `JIRA_EMAIL` (or OAuth vars). **Local:** `.env`. **Cluster:** same names in Secret → Pod env. Minimal scope: read issues, add comments (internal). |
| **Worker → kagent MCP** | In-cluster or token | Same cluster: use Kubernetes Service DNS; optional service account token or mTLS. Different cluster: bearer token or mutual TLS; store in Secret. |
| **Worker → Loki / Mimir** | Optional | Basic auth or bearer token in Secret if Loki/Mimir require it. |
| **CrewAI** | None separate | Runs inside worker process; uses worker’s credentials for Jira and MCP. |

### 3.4 Security

| Area | Measures |
|------|----------|
| **Secrets** | **Local:** only in **`.env`** (gitignored); never commit. **Cluster:** Kubernetes Secrets or Vault → same env var names as `.env`. No secrets in application source. |
| **Webhook** | Validate signature or shared secret on every request; reject invalid. HTTPS only for API (webhook) URL. Optional: rate limit by Jira IP or webhook key. |
| **Worker** | Least-privilege Kubernetes RBAC: only what is needed (e.g. read pods/logs if worker talks to API server; often worker only talks to kagent). Network policies to restrict egress to Jira, database, kagent, Loki, Mimir. |
| **kagent** | Read-only tool allowlist; no apply/delete/scale/patch. Kubernetes RBAC for kagent’s service account: read-only access to namespaces in scope. |
| **Jira** | API token or app with minimal scope (read issue, add comment). Prefer internal comments only for POC. |
| **Idempotency** | Prevents replay and duplicate processing; use in addition to (not instead of) webhook validation. |
| **Data in comments** | Optional: redact obvious secrets in log snippets (e.g. in Synthesis output) before posting to Jira. |

### 3.5 Logging and Debugging

**Why logging is required:** Debugging without application logs is not practical. The pipeline spans multiple components (webhook → Go API → database → worker → CrewAI → Jira/kagent). Without structured logs you cannot reliably answer: whether the webhook was received, whether a job was stored, which worker picked it up, which flow step failed, or what the Jira/kagent calls returned. Logging is therefore part of the implementation plan from Phase 1.

| Component | What to log | When |
|-----------|-------------|------|
| **Go API** | Webhook received (issue_key, request id); secret valid/invalid; job stored (job_id, issue_key); DB or Jira fetch errors. | Every request; errors with level ERROR. |
| **Worker** | Job claimed (job_id, issue_key); flow step start/end (step name, issue_key); tool calls (e.g. jira_get_issue, kagent tool name, success/failure); idempotency skip; comment posted; job status updated; unhandled exceptions. | Per job; DEBUG in dev, INFO in prod for key events. |
| **CrewAI / flow** | Step transitions; agent inputs/outputs (summary only in INFO, full in DEBUG); tool invocations and results (truncated if large). | Via worker’s logger; pass through correlation fields. |

**Correlation:** Use a consistent **request or job identifier** (e.g. `job_id` and `issue_key`) in every log line for a given run so that logs from Go API and Worker can be tied to one ticket and one processing run.

**Where logs go:** Standard out (stdout) / standard error (stderr). In Kubernetes, cluster logging (e.g. Fluentd, Loki, or cloud log aggregation) collects stdout/stderr; no separate log server is required for the POC. Optional: emit **structured logs** (e.g. JSON with `level`, `msg`, `job_id`, `issue_key`, `step`, `error`) so log backends can filter and search.

**Log levels:** INFO for normal operations (webhook received, job started, step completed, comment posted); WARN for recoverable issues (e.g. idempotency skip, missing optional field); ERROR for failures (DB error, Jira API error, flow exception). DEBUG for detailed step payloads and tool I/O only in development or when explicitly enabled.

**What not to log:** Secrets (webhook secret, API tokens, DB URL credentials); full ticket body or full comment body at INFO (use summary or redact). Optional: redact in DEBUG if needed for troubleshooting.

### 3.6 Core Entities Required to Access the Ticket

To read and comment on a Jira Service Management ticket, the system needs:

| Entity | Purpose |
|--------|---------|
| **Issue key** | Unique identifier (e.g. `PROJ-123`); from webhook payload or job row in database. |
| **Jira base URL** | Jira Cloud site (e.g. `https://your-domain.atlassian.net`) or Data Center URL. |
| **Jira credentials** | API token or OAuth credentials with permission to read the issue and add comments (internal). |
| **Request type (optional)** | JSM request type ID for routing or field mapping. |
| **Custom field IDs (optional)** | For "Service degradation" tickets: namespace, service/app, environment, time window, etc.; align with `config/required_fields.yml` and Intake agent. |

The webhook payload from Jira Automation should include at least the issue key; the Go API or worker fetches full issue details via Jira API when processing.

### 3.7 Deployment Topology (Organization- and Cluster-Agnostic)

The POC should be deployable in any organization and any Kubernetes cluster with no hardcoded URLs, cluster names, or org-specific IDs. The following keeps topology configurable and portable.

| Component | Deployment options | Configuration |
|-----------|--------------------|---------------|
| **Go API** | **Local:** process + tunnel for webhook. **Cluster:** Deployment + Service + Ingress. | Same env names everywhere (§3.8): `WEBHOOK_SECRET`, `DATABASE_URL`, `JIRA_BASE_URL`, etc. |
| **Worker** | **Local:** process. **Cluster:** Deployment (same cluster as kagent recommended). | Same env names as `.env` (§3.8): `DATABASE_URL`, `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_EMAIL`, `KAGENT_MCP_URL`, optional `LOKI_URL`, `MIMIR_URL`. |
| **kagent** | Installed in target cluster; exposed as Service. Worker uses in-cluster DNS (e.g. `http://kagent-mcp.namespace.svc.cluster.local`) or configurable URL for cross-cluster. | Config (e.g. `config/mcp_endpoints.yml`): `kagent_mcp_base_url`, allowed tool names. |
| **Database** | **Local:** PostgreSQL via `DATABASE_URL` in `.env` (e.g. local or Docker). **Cluster:** in-cluster or managed PostgreSQL. | Same `DATABASE_URL` env var in both environments. |
| **Secrets (cluster)** | Kubernetes Secrets in worker/API namespace (or external operator). | **Keys must match env var names** (e.g. `JIRA_API_TOKEN`, `WEBHOOK_SECRET`, `DATABASE_URL`) so `envFrom: secretRef` mirrors `.env`. |
| **Helm / Kustomize** | Single chart or overlay per env (dev/stage/prod). | Images, replicas, limits; reference Secret by name; **no second naming scheme** for the same values. |
| **Documentation** | Checklist: **local** (copy `.env.example` → `.env`, tunnel, run API + worker) then **cluster** (build/push images, apply Secret + manifests). Jira Automation URL + secret. | |

### 3.8 Configuration: one `.env` locally, Secrets on cluster (no code fork)

| Principle | Detail |
|-------------|--------|
| **Single contract** | Go API and Worker read configuration **only** from **environment variables** with **fixed names**. No K8s API for config inside app code; no hardcoded URLs. |
| **Local** | **`.env.example`** (committed): every variable, grouped with comments. Copy to **`.env`** (gitignored). Load before start: shell `export`, **direnv**, **docker-compose `env_file: .env`**, or IDE env file. **Do not commit `.env`.** |
| **Cluster** | Secret keys **identical** to env var names. Deployment: `envFrom: - secretRef: name: service-desk-secrets`. Optional External Secrets Operator — still ends as same names in Pod env. |
| **No code change** | Local → cluster = build image + wire Secret; **binaries unchanged**. |

**Recommended environment variables** (illustrative; extend if OAuth replaces API token):

| Variable | Used by | Local | Cluster |
|----------|---------|-------|---------|
| `WEBHOOK_SECRET` | Go API | In `.env` | Secret |
| `DATABASE_URL` | Go API, Worker | e.g. `postgres://localhost:5432/service_desk?sslmode=disable` | Secret |
| `JIRA_BASE_URL` | Go API, Worker | `https://org.atlassian.net` | Secret or ConfigMap |
| `JIRA_API_TOKEN` | Worker (API if needed) | Token | Secret |
| `JIRA_EMAIL` | Worker | Jira Cloud user email | Secret |
| `KAGENT_MCP_URL` | Worker | kind/minikube URL, tunnel, or mock endpoint | In-cluster Service URL |
| `LOG_LEVEL` | Both | `DEBUG` / `INFO` | ConfigMap or env |
| `LOKI_URL`, `MIMIR_URL` | Worker | Optional; empty = disabled | Optional |

*Optional vars: omit or leave empty; apps treat missing optional URLs as disabled.*

**`.env.example` deliverable:** One line per variable `NAME=` with section comments (Webhook, Database, Jira, MCP, Observability).

---

## 4. Data and State

### 4.0 Data Ownership (Avoiding Redundancy)

We do **not** store Jira ticket status or ticket content as source of truth in our database. That avoids two sources of truth and sync issues.

| What | Where it lives | Our DB holds |
|------|----------------|--------------|
| **Ticket status** (Open, In Progress, Resolved, etc.) | **Jira only** | Nothing. Worker reads current status from Jira API when it runs, if needed. |
| **Ticket content** (summary, description, custom fields) | **Jira only** | Optional: minimal webhook payload (e.g. `issue_key`, `updated_at`, event id) for idempotency and audit. Do **not** cache full ticket body; **always fetch from Jira** in the worker (e.g. in `load_ticket`) so the flow uses up-to-date data. |
| **Job / pipeline status** (pending, processing, done, failed) | **Our DB only** | Yes. This is the status of *our processing run*, not the Jira ticket. No duplication. |
| **Idempotency** (“we already ran the agent for this ticket/event”) | **Our DB only** | Yes. Prevents re-running when the bot posts a comment or the webhook is re-delivered. |

**Managing “change in status”:**

- **Jira ticket status changes** (e.g. user moves ticket to Resolved): We do not mirror this. If a worker needs to know current Jira status (e.g. skip if already Resolved), it calls the Jira API at run time. No sync required.
- **Our job status changes** (pending → processing → done): Updated only in our DB by the Go API and workers. No duplication with Jira.

**Ideal shape for the job table:** Store only what is needed for the queue and idempotency: `id`, `issue_key`, `webhook_event_id` or `updated_at` (for dedup), `job_status` (pending/processing/done/failed), `created_at`, and optionally `processed_at`. Do **not** store summary, description, or Jira status; the worker fetches those from Jira when it runs.

### 4.1 Ticket Context (What the Flow Uses)

From Jira we use (fetched at processing time, not from our DB):

- **Issue key** (e.g. `PROJ-123`)
- **Summary, description, request type**
- **Custom fields** that the Intake agent expects (see below)

### 4.2 Required Fields for “K8s / Service Degradation” (POC)

The Intake agent checks for a minimal set so diagnostics can run:

| Field / concept   | Purpose                          | If missing |
|-------------------|----------------------------------|------------|
| **Namespace**     | Scope K8s and log queries        | Ask in comment, stop |
| **Service / app name** (or label) | Identify workload and logs   | Ask in comment, stop |
| **Environment / cluster** | Optional; “where”            | Can default or ask |
| **Time window**   | “Since when” for logs/metrics    | Default e.g. last 60 minutes |
| **Symptom / error** | Short description or error text | Improves synthesis; can proceed without |

Config file (e.g. `config/required_fields.yml`) should list these per request type so the Intake agent and routing stay in sync.

### 4.3 Idempotency

- Store a processed marker per ticket in the **database** so the pipeline is not re-run on every update (e.g. when the bot posts a comment).
- **Key:** e.g. `issue_key` (or include `updatedAt` / last comment id if you want to allow re-run when the user updates the ticket).
- **Storage:** Dedicated table or column in the same database (e.g. `processed_issues` or `jobs.processed_at`); timestamp or version.
- Before running the Flow, check the database; if already processed for this event, skip.

---

## 5. POC Scope: One Ticket Type, Two Paths

### 5.1 Supported Ticket Type

- **“Service degradation / workload unhealthy in Kubernetes”**
- Examples: pods crashlooping, pods not ready, 5xx spikes, latency issues.

### 5.2 Path 1 — Missing Information

- Intake agent finds required fields missing (e.g. no namespace or service name).
- **Action:** Post **one internal comment** asking for the missing items (and optional short public comment).
- **Stop;** do not run diagnostics.

### 5.3 Path 2 — Complete Enough (K8s-Related)

- Intake agent sets `can_proceed: true` and (for POC) routing is “k8s-ish” (e.g. keywords: pod, deployment, namespace, kubernetes, crashloop, 502, etc.).
- **Action:** Run Diagnostics agent (K8s via kagent MCP; optionally Loki + Mimir). Then Synthesis agent produces the analysis and we post it as an **internal comment**.

### 5.4 Path 3 — Not in POC Scope (e.g. “Password reset”)

- Either post a short comment that this ticket type is not supported by the POC, or only run Intake and ask clarifying questions. No diagnostics.

---

## 6. Agent Design (Three Agents for POC)

### 6.1 Agent 1 — Intake (Completeness + Context Extraction)

- **Goal:** Ensure the ticket has minimum viable information and extract structured context.
- **Input:** Raw ticket (summary, description, fields).
- **Output (structured, e.g. Pydantic):**
  - `service`, `namespace`, `cluster/env`, `time_window`, `symptoms`, `links`
  - `missing_fields[]`, `clarifying_questions[]`
  - `can_proceed: bool`
- **Behaviour:**
  - If `can_proceed` is false: post comment listing missing fields / questions, then **end flow**.
  - If `can_proceed` is true: pass structured context to next step.

### 6.2 Agent 2 — Diagnostics (Collector)

- **Goal:** Gather evidence only; minimal “reasoning” in POC.
- **Input:** Structured context from Intake (namespace, service, time window, etc.).
- **Behaviour:**
  - Run a fixed **diagnostic bundle**:
    - **Kubernetes (kagent MCP):** list/get pods in namespace, deployment/statefulset status, describe top 1–3 unhealthy pods, events in namespace (e.g. last 60–120 minutes).
    - **Optional for POC:** Loki (errors for service/namespace, last N lines; cap e.g. 50 lines). Mimir (e.g. restarts, readiness, CPU/memory over last 30m).
  - Return a compact **artifact** (text/summary) for Synthesis; no need to run multiple specialist agents in POC.

### 6.3 Agent 3 — Synthesis (Final Answer)

- **Goal:** Turn diagnostics + ticket context into one human-actionable comment.
- **Input:** Ticket context + diagnostics artifact.
- **Output (structured):**
  - Short **triage summary**
  - **Evidence:** key pod/deployment status, top events, (optional) log snippets, (optional) metric highlights
  - **Most likely causes** (1–3) with confidence
  - **Recommended next steps for human** (commands, runbook links, rollback hints)
  - **Questions remaining** (if any)
- **Behaviour:** Format as markdown and pass to “Post comment” step.

---

## 7. Tools

### 7.1 Jira

- **jira_get_issue(issue_key):** Fetch issue/request details (summary, description, custom fields).
- **jira_post_comment(issue_key, body, internal=True):** Post comment. Use JSM request comment API if you need internal vs public; for POC internal-only is enough.

Credentials: API token or OAuth; store in secrets. Validate webhook with shared secret.

**Why Jira read/write is not in the Go API:** The Go API only enqueues jobs. **Jira integration** for agents is implemented as **CrewAI tools** under **`service-desk-crew/src/service_desk_crew/tools/`** (e.g. `jira.py`). The **worker** invokes the `service_desk_crew` Flow (installed from **`service-desk-crew/`**), which uses those tools; processing still uses up-to-date ticket content per §4.0. Credentials remain env-only (`JIRA_*`) in the worker/crew process environment. The worker stays a thin shell (poll DB, call `kickoff`, update job status). *Phase 2 used `worker/tools/jira.py`; Phase 3 consolidates on the crew package path above (with a thin **`worker/tools/jira.py`** re-export to `service_desk_crew.tools.jira`).*

### 7.2 kagent MCP (Kubernetes)

- **Usage:** Worker talks to kagent’s MCP tool server (in-cluster).
- **Tools (read-only allowlist):** e.g. get pods, get deployments, describe pod, get events in namespace. No apply/delete/scale/patch.
- **Config:** e.g. `config/mcp_endpoints.yml` with kagent MCP base URL and list of allowed tool names. Optional thin wrapper **`service-desk-crew/src/service_desk_crew/tools/mcp_k8s.py`** that calls MCP and enforces allowlist.

### 7.3 Loki (Optional for POC)

- **Option A (simplest):** Worker calls Loki HTTP API directly: `/loki/api/v1/query_range` with LogQL. Limit lines (e.g. 50) and time range.
- **Option B:** Small MCP server for Loki; worker calls it like any other tool. POC can use Option A.

### 7.4 Mimir (Optional for POC)

- **Option A:** Worker calls Mimir’s Prometheus-compatible API: `/prometheus/api/v1/query_range` with PromQL. Limit query range and step.
- **Option B:** MCP server for Mimir. POC can use Option A.

---

## 8. CrewAI Flow (Sequential for POC)

### 8.1 Role of CrewAI in this POC

CrewAI is used for **multi-agent orchestration**: it runs the right agent at the right step, passes state between steps, and supports branching (e.g. “if missing info → comment and stop” vs “if complete → run diagnostics → synthesize → comment”). It provides:

- **Multi-agent orchestration** — Several agents with distinct roles (Intake, Diagnostics, Synthesis) instead of one monolithic prompt.
- **Flows** — A defined pipeline with steps, branching, and shared state (e.g. `issue_key`, `ticket_raw`, `intake_output`, `diagnostics_artifact`, `synthesis_output`).
- **Agents + tools** — Each agent has a role, a goal, and tools (Jira, kagent MCP, optional Loki/Mimir). CrewAI handles which agent calls which tools with the configured LLM.

**Mapping from this spec to CrewAI concepts:**

| Spec concept | CrewAI concept |
|--------------|----------------|
| **Intake agent** (completeness, context extraction) | One CrewAI Agent with role/goal and tools (e.g. `jira_get_issue`). Output: structured (e.g. Pydantic) with `can_proceed`, `missing_fields`, etc. |
| **Diagnostics agent** (K8s + optional logs/metrics) | Another Agent with kagent MCP tools (and optional Loki/Mimir). Input: Intake’s structured context. Output: one artifact (text/summary). |
| **Synthesis agent** (triage + evidence + causes + next steps) | Third Agent; no K8s tools; input = ticket context + diagnostics artifact. Output: markdown for the comment. |
| **Pipeline:** load_ticket → intake_check → route → k8s_diagnostics → synthesize → post_comment → mark_processed | A CrewAI Flow: sequential steps with **branching** (e.g. after `intake_check`: if not `can_proceed` → post “please provide…” and end; else continue to diagnostics). |
| **State** (issue_key, ticket_raw, intake_output, diagnostics_artifact, synthesis_output) | **Flow state** (e.g. FlowState) that each step reads and writes. |

**Why CrewAI (vs a single script or single LLM):**

- **Branching** — “If missing info, comment and stop; if complete and K8s-related, run diagnostics then synthesize.” CrewAI Flows are built for conditional flow logic.
- **Separation of concerns** — Intake only does completeness/parsing; Diagnostics only runs tools; Synthesis only turns evidence into narrative. Different agents, different tools, same state.
- **Tools in a standard way** — Jira and kagent (MCP) are attached to agents as tools; the framework handles tool calls and results instead of hand-wiring every API call in a single script.
- **State between steps** — Flow state carries `intake_output` into Diagnostics and `diagnostics_artifact` into Synthesis without manual passing.

### 8.2 Flow steps

Single flow implemented in **`service-desk-crew/src/service_desk_crew/flow.py`** (module **`service_desk_crew.flow`**; e.g. class `L1SupportFlow`), with steps:

1. **load_ticket** — Fetch ticket from Jira by `issueKey`; check idempotency; if already processed, exit.
2. **intake_check** — Intake agent runs; if not `can_proceed`, post “please provide…” comment and **end**.
3. **route** — Simple rule: if ticket looks “k8s-ish” (keyword heuristic), continue; else optional short comment and **end**.
4. **k8s_diagnostics** — Diagnostics agent runs (K8s via kagent MCP; optionally Loki/Mimir). Produce one artifact.
5. **synthesize** — Synthesis agent produces structured summary + evidence + causes + next steps.
6. **post_comment** — Post internal comment (markdown). Optionally set idempotency marker here.
7. **mark_processed** — Store processed marker for `issueKey` (and optionally `updatedAt`) in the database.

State (e.g. CrewAI FlowState) carries: `issue_key`, `ticket_raw`, `intake_output`, `diagnostics_artifact`, `synthesis_output`.

---

## 9. Jira Integration

### 9.1 Trigger (Automatic on Ticket Creation)

- **Specification:** Whenever a **user creates** a Jira Service Management ticket (request), the agent pipeline must be **automatically triggered** to process it. There is no manual “run agent” step; creation of the ticket is the only trigger required.
- **Implementation:** Use a **Jira Service Management Automation rule** that fires on “Request created” (and optionally “Request updated” if re-processing on user edits is desired). The rule sends a webhook to the Go API URL. The API stores the job in the database; workers poll for pending jobs and run the CrewAI Flow for that ticket.
- Payload must include at least `issueKey` (or equivalent). Go API or worker fetches full issue via Jira API if needed.

### 9.2 Reading and Writing

- **Read:** Jira REST API (e.g. `/rest/api/3/issue/{issueIdOrKey}`) or JSM request API as appropriate.
- **Write:** Post comment via issue comment API or JSM request comment API (prefer JSM if you need internal vs public later). For POC, internal-only is enough.

### 9.3 Loop Prevention

- Do not trigger Automation on “comment created” by the bot (e.g. exclude bot user or avoid “comment created” trigger for this rule). And/or use idempotency so re-delivery of the same event does not run the flow again.

---

## 10. Configuration and Deliverables (Checklist)

| Item | Description |
|------|-------------|
| **`.env.example`** | All env var names (no secrets); copy to `.env` locally; cluster Secret uses **identical** keys (§3.8). |
| **config/required_fields.yml** | Required fields per ticket type (namespace, service, etc.). |
| **config/routing.yml** | Simple keyword list for “k8s-ish” routing. |
| **config/mcp_endpoints.yml** | kagent MCP base URL, allowed tools. |
| **`service-desk-crew/src/service_desk_crew/flow.py`** | CrewAI **Flow**: load_ticket → intake_check → route → k8s_diagnostics → synthesize → post_comment → mark_processed (§8.2). |
| **`service-desk-crew/src/service_desk_crew/crew.py`** | `@CrewBase` crew; agents/tasks from `.../config/agents.yaml` and `tasks.yaml` (**crewai create crew** layout). |
| **`service-desk-crew/src/service_desk_crew/main.py`** | Entry for `crewai run` from **`service-desk-crew/`**; worker imports `service_desk_crew` and calls Flow kickoff. |
| **`service-desk-crew/src/service_desk_crew/config/llm_factory.py`** | Sole module that constructs `LLM(...)` from env for all agents. |
| **`service-desk-crew/pyproject.toml`** | Installable package **`service_desk_crew`** (package dir `src/service_desk_crew`); pins `crewai`, Python ≥ 3.10. Worker: **`pip install -e service-desk-crew`** from monorepo root. |
| **`service-desk-crew/src/service_desk_crew/tools/jira.py`** | CrewAI tools: `get_issue`, `post_comment` (internal comments). |
| **`service-desk-crew/src/service_desk_crew/tools/mcp_k8s.py`** | Thin MCP client + allowlist for kagent K8s tools (Phase 4+). |
| **Optional: `.../tools/loki.py`, `mimir.py`** | Direct HTTP wrappers for Loki/Mimir for POC. |
| **`worker/`** | Poll database for pending jobs; **import** `service_desk_crew` and run Flow kickoff (`issue_key`, `job_id`, DB callbacks for `mark_processed` if needed); idempotency checks and job status updates; log job lifecycle, flow steps, errors (§3.5). Jira/MCP/tool execution runs inside the crew package. |
| **Go API** | Validate secret, fetch ticket, store job in database; log webhook, job stored, errors (see §3.5). |
| **Application logging** | Stdout/stderr with job_id/issue_key correlation; optional JSON and configurable level; doc for kubectl logs or log aggregation. |
| **K8s manifests or Helm** | Deploy Go API service, worker, database, secrets, service account + RBAC (read-only for worker). |
| **Jira Automation rule** | Configure “When request created” → send webhook to Go API so that **every ticket created by the user automatically triggers** the agent pipeline. |

---

## 11. Guardrails and Safety

- **Read-only:** All K8s tools are get/list/describe only; no apply/delete/scale. Enforce via MCP allowlist and (if applicable) Kubernetes RBAC for the worker’s service account.
- **Loki:** Cap line count (e.g. 50), fixed time window; consider redacting obvious secret patterns.
- **Mimir:** Cap query range and step size; no expensive global queries.
- **Timeouts:** Hard timeout per tool call and per flow run.
- **Idempotency:** Always check/set processed marker so the bot does not re-process on its own comment.
- **Human-in-the-loop:** All output is “suggested next steps”; no automatic changes to the cluster.

---

## 12. Success Criteria for POC

- **End-to-end in Jira:** Create ticket → receive internal comment with triage + evidence + recommended next steps (for “complete” path).
- **Missing-info path:** Create ticket without namespace/timeframe → bot posts comment asking for missing info and stops.
- **Read-only:** Zero write actions on Kubernetes.
- **Useful output:** At least one concrete, correct next-step suggestion on a known failure scenario (e.g. CrashLoopBackOff, or 5xx due to pod not ready).
- **No duplicate comments:** Idempotency prevents re-processing when the bot adds a comment.

---

## 13. Demo Script (How to Show It Works)

1. **Missing info:** Create a JSM request with no namespace/timeframe → expect one internal comment: “Please provide: namespace, cluster, timeframe, error snippet…” and no diagnostics.
2. **Happy path:** Create a request with namespace + service + symptom (e.g. “pods crashlooping in ns X”) → after a short delay, one internal comment with summary, pod/events evidence, likely causes, and suggested next steps.
3. **Non-K8s ticket (e.g. “Password reset”):** Either “not supported by POC” comment or only Intake clarifying questions; no K8s diagnostics.

---

## 14. Open Questions and Clarifications

- **CrewAI vs Flows:** Use CrewAI **Flows** in **`service-desk-crew/src/service_desk_crew/flow.py`** orchestrating **`@CrewBase`** crews from **`crew.py`**. Run **`crewai create crew service_desk_crew`** inside **`service-desk-crew/`** so the scaffold matches the CLI. Confirm CrewAI version and Flow API against current docs.
- **kagent deployment:** Confirm kagent is installed in the target cluster and the MCP server is reachable from the worker (same cluster is simplest). Document the exact MCP endpoint and tool names.
- **Jira request type and custom fields:** Map “Service degradation / workload unhealthy” to your JSM request type and custom field IDs (namespace, service, environment, etc.) so Intake and config are aligned.
- **Loki/Mimir labels:** Confirm label names (e.g. `namespace`, `app`, `pod`) so LogQL and PromQL in the diagnostic bundle use the correct labels.
- **Task creation:** The **user** creates the Jira Service Management request/task. The bot only **comments** on that existing ticket (analysis and suggested next steps). The POC does not create Jira issues.

---

## 15. Next Steps After POC

- **More ticket types and routing:** Add additional ticket classes and a fuller routing taxonomy (POC has one ticket type and keyword-based routing only).
- **Log/metrics specialists or MCP:** Add dedicated log and metrics specialist agents, and/or move Loki/Mimir behind MCP (POC already has optional direct HTTP to Loki/Mimir; this is the next evolution).
- **Production hardening:** Full observability pipeline (CrewAI + kagent/OTel tracing, metrics); RBAC per agent; evaluation (golden tickets, regression suite). *Application logging is already in POC (§3.5); this is OTel/tracing and agent-level observability.*
- **Optional — Jira output expansion:** Support creating Jira sub-tasks or linked issues from the synthesis output (post-POC; in POC the bot comments only).
- **Optional — portable deployment:** Configuration Portal (Next.js) for org-specific config and export; see portable implementation plan if offering the system to multiple clients.
- **Optional — operations:** Re-processing policy (e.g. re-run when user updates ticket); alerting on job/comment failures; rate limiting and backpressure; LLM token/cost tracking per ticket or org.

---
