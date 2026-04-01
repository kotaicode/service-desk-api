# Service Desk POC — Implementation Plan

**Phases and steps to implement the Service Desk L1 support automation.**

This plan is derived from the [Service Desk POC Technical Specification](service-desk-poc-tech-spec.md). Complete all phases **locally first** (using `.env`, PostgreSQL, and optional tunnel for webhook); then deploy to server/cluster using the same env var names from Secrets.

---

## 1. Phase overview

| Phase | Name | Scope |
|-------|------|--------|
| **1** | Foundation (local first) ✅ | Wiring, trigger, logging, single env contract. Go API + Worker skeleton; webhook → DB → worker poll. |
| **2** | Jira and idempotency ✅ | Jira tools (env-only credentials); idempotency; minimal comment flow. |
| **3** | CrewAI Flow ✅ | **`service-desk-crew/`** — full **`crewai create crew service_desk_crew`** output under **`src/service_desk_crew/`** (outside `worker/`); **`service-desk-crew/pyproject.toml`**; L1 **`flow.py`** + **`llm_factory.py`**; worker: **`pip install -e ./service-desk-crew`** then **`import service_desk_crew`**; Diagnostics stub. |
| **4** | kagent integration | **`KAGENT_MCP_URL`** → kagent MCP (kind/minikube, tunnel, or in-cluster Service); **`config/mcp_endpoints.yml`** + **`mcp_k8s.py`** allowlist; Diagnostics uses real read-only K8s tools; same code path local vs cluster (**§3.1**, **§7.2**). |
| **5** | Optional: Loki/Mimir | **`LOKI_URL`** / **`MIMIR_URL`** (empty = off); **`loki.py`** / **`mimir.py`** direct HTTP (**§7.3–§7.4** Option A); extend Diagnostics artifact with capped LogQL/PromQL; guardrails **§11** (line/range limits, redaction). |
| **6** | Deploy to server / cluster | **Docker** images (Go API + Worker w/ **`service-desk-crew`**); **PostgreSQL** (in-cluster or managed); **Secrets** = same keys as **`.env`** (**`envFrom`**); **Ingress** for webhook; **kagent** Service URL; **Helm/Kustomize**; **RBAC** / **NetworkPolicy**; **`kubectl logs`** logging doc — **no app code changes** for config (**§2.2**, **§3.7–§3.8**, **§10**). |

### 1.1 Job queue statuses, `processed_issues`, and webhook upsert (reference implementation)

Aligns with tech spec **§4.3–4.4**. The **`jobs`** table has **at most one row per `issue_key`** (`UNIQUE(issue_key)`). The worker polls **`status = pending`** only.

**`jobs.status` values**

| Status | Meaning |
|--------|---------|
| **`pending`** | Ready to claim (inserted or reopened by Go API webhook). |
| **`processing`** | Worker claimed the row; CrewAI flow running. |
| **`completed_full`** | Full K8s path finished: intake → diagnostics → synthesis → final internal comment. **`set_processed`** inserts **`processed_issues`** for this `issue_key`. |
| **`awaiting_customer`** | Missing-info path: internal comment only; **no** `processed_issues` row. |
| **`completed_unsupported`** | Out-of-scope / non-K8s path: internal comment only; **no** `processed_issues` row. |
| **`skipped`** | `issue_key` already in **`processed_issues`**; flow not run (full-resolution idempotency). |
| **`failed`** | Error, timeout, or missing env; **no** `processed_issues` row unless a successful full path ran earlier. |

**`processed_issues`:** Insert **only** when the worker reaches **`completed_full`**. Do **not** insert after partial outcomes — the customer can update the ticket and a follow-up webhook can set the job back to **`pending`** for a later full run.

**Go API (`UpsertJobFromWebhook`):** (1) **Insert** if no row for `issue_key`. (2) **Reopen** to **`pending`** if current status is **`awaiting_customer`**, **`completed_unsupported`**, or **`failed`** (response may include **`reopened`: true**). (3) **Dedupe** if **`pending`** or **`processing`**: refresh **`payload`** only (**`deduped`: true**). (4) **Re-queue** other terminals (e.g. **`completed_full`**, **`skipped`**) to **`pending`** on the same row; the worker may still **`skip`** if **`processed_issues`** already has the issue.

**Edge cases:** Narrow Jira Automation triggers where possible; rely on **`issue_key`** uniqueness + upsert to avoid duplicate rows; redundant webhooks are safe (payload refresh or no-op).

---

## 2. Phase 1 — Foundation (local first) ✅ Completed

**Goal:** Webhook → Go API (validate secret, store job) → Database → Worker (poll, claim, skeleton process). No Jira API calls or CrewAI yet. Single env contract and logging as per tech spec §3.5.

### 2.1 Project and env contract

| Step | Action |
|------|--------|
| 1.1 | Create repository layout: e.g. `api/` (Go), `worker/` (Python), `config/` (optional). **Phase 3** adds **`service-desk-crew/`** with **`pyproject.toml`** + **`src/service_desk_crew/`** from **`crewai create crew`** (see tech spec §2.1.1). |
| 1.2 | Add **`.env.example`** with Phase 1 variables: `WEBHOOK_SECRET`, `DATABASE_URL`, `LOG_LEVEL`. Document: “Copy to `.env` and set values.” |
| 1.3 | Add `.env` to `.gitignore`. Document in README that config is env-only (§3.8). |

### 2.2 Database (PostgreSQL)

| Step | Action |
|------|--------|
| 2.1 | Choose DB driver: Go (`database/sql` + `github.com/jackc/pgx/v5/stdlib` or pgx pool); Python (`psycopg2` or SQLAlchemy). |
| 2.2 | Create **jobs** table: `id`, `issue_key`, `status`, `payload` (JSON/text), `created_at`, `updated_at`. Enforce **`UNIQUE(issue_key)`** so there is one queue row per ticket (tech spec **§4.4**). |
| 2.3 | Read `DATABASE_URL` from env only (no hardcoded DSN). Use PostgreSQL connection string (e.g. `postgres://localhost:5432/service_desk?sslmode=disable`). |

### 2.3 Go API (service-desk-api — webhook receiver)

| Step | Action |
|------|--------|
| 3.1 | Implement HTTP server (e.g. `net/http` or Chi/Gin) with one POST endpoint for the Jira webhook (e.g. `/webhook/jira` or `/api/webhook`). |
| 3.2 | **Validate webhook secret** on every request: read `X-Webhook-Secret` (or agreed header/query) and compare to `WEBHOOK_SECRET`; if missing or wrong, return 401 and do not store. Log “secret valid” or “secret invalid”. |
| 3.3 | **Parse body** to get `issue_key` (or equivalent from Jira Automation payload). Log “webhook received” with `issue_key` and request id if present. |
| 3.4 | **Upsert one row per `issue_key`** (e.g. **`UpsertJobFromWebhook`**): first webhook **inserts** `pending`; later webhooks **reopen**, **dedupe**, or **re-queue** per **§1.1** — never two rows for the same `issue_key`. |
| 3.5 | **Return 200** after insert. On DB or parse errors return 5xx and log with ERROR. |
| 3.6 | **Logging (§3.5):** Every request log: webhook received (`issue_key`, request id); secret valid/invalid; job stored (`job_id`, `issue_key`). On failure: ERROR with reason. Never log secrets. Honour `LOG_LEVEL`. |

### 2.4 Worker (skeleton)

| Step | Action |
|------|--------|
| 4.1 | Python 3.10+ project under `worker/` with dependency file (e.g. `requirements.txt`). No CrewAI or Jira yet. |
| 4.2 | Read config from env only: `DATABASE_URL`, `LOG_LEVEL`. |
| 4.3 | **Poll loop:** Periodically query DB for rows with `status = 'pending'` (e.g. every 10–30 s). Optionally limit to one job per worker. |
| 4.4 | **Claim job:** When a row is found, update `status` to `'processing'` (use transaction/lock so only one worker claims it). Log “job claimed” with `job_id`, `issue_key`. |
| 4.5 | **Skeleton processing:** For Phase 1, no Jira or CrewAI; e.g. no-op or short sleep, then set job `status` to a terminal value such as **`completed_full`** or **`failed`** (see **§1.1**). Validates pipeline only. |
| 4.6 | **Logging (§3.5):** Log job claimed (`job_id`, `issue_key`); on exception log ERROR; optionally log when job reaches a terminal status (**§1.1**). Honour `LOG_LEVEL`. |

### 2.5 Run locally and smoke test

| Step | Action |
|------|--------|
| 5.1 | Copy `.env.example` → `.env`; set `WEBHOOK_SECRET`, `DATABASE_URL`, `LOG_LEVEL`. |
| 5.2 | Start Go API (e.g. `go run ./api` or `./api`) listening on a port (e.g. `:8080`). |
| 5.3 | Start worker (e.g. `python -m worker` or `python worker/main.py`). Confirm it connects to DB and polls. |
| 5.4 | **Smoke test (no Jira):** Manually insert a row into jobs with `status = 'pending'` and `issue_key` (e.g. `TEST-1`). Confirm worker picks it up, logs “job claimed”, and updates status. Check logs on both API and worker. |
| 5.5 | **Smoke test (webhook):** Use curl/Postman to POST to the Go API webhook URL with header `X-Webhook-Secret` and body containing `issue_key` (match Jira Automation payload shape). Confirm 200, job created, worker processes it. Repeat with wrong secret → 401, no new job. |

### 2.6 Optional: Jira Automation + tunnel

| Step | Action |
|------|--------|
| 6.1 | Expose Go API via tunnel (e.g. ngrok): `https://<tunnel>/webhook/jira`. |
| 6.2 | In Jira Service Management: Automation rule “When request created” → “Send web request” to that URL, POST, header `X-Webhook-Secret`, body with `issueKey` (or your payload shape). |
| 6.3 | Create a test request in Jira; confirm webhook fires, API logs and stores job, worker logs “job claimed” and completes. |

### 2.7 Phase 1 deliverables checklist

**Status: ✅ Phase 1 completed** (service-desk-api repo)

- [x] `.env.example` with `WEBHOOK_SECRET`, `DATABASE_URL`, `LOG_LEVEL`; `.env` gitignored.
- [x] Jobs table and DB access via `DATABASE_URL` only.
- [x] Go API: webhook endpoint, secret validation, parse `issue_key`, **upsert** job per **`issue_key`** (**§1.1**), return 200; logging per §3.5.
- [x] Worker: poll DB, claim job, skeleton process, update status; logging per §3.5.
- [x] Smoke tests: manual job insert and webhook POST (valid and invalid secret) pass.
- [x] README: how to run API and worker locally and how to run smoke tests.

---

## 3. Phase 2 — Jira and idempotency ✅ Completed

**Goal:** Add Jira API integration to the worker (read issue, post comment) using env-only credentials; implement idempotency for **full-resolution** runs (**`processed_issues`** per **§1.1**). In the **`service-desk-api`** repo, Phase 2 foundations are merged with Phase 3: the worker runs **`run_l1_support`** (CrewAI) rather than a standalone “minimal comment only” loop. Logging per tech spec §3.5: idempotency skip, flow outcomes, comment posted.

**Status:** ✅ **Phase 2 completed** in **`service-desk-api`** — see verification notes in §3.7 below.

**Why Jira get-issue and post-comment are in the worker, not the Go API:** The worker runs the pipeline and needs the ticket at processing time (up-to-date per tech spec §4.0); the comment content is produced by the worker (and in Phase 3 by CrewAI), not by the webhook. The Go API only enqueues jobs—it does not run the flow or know what to post. Keeping Jira in the worker gives one place for Jira integration and credentials, and in Phase 3 CrewAI will use these as tools inside the Python runtime.

### 3.1 Env and config

| Step | Action |
|------|--------|
| 2.1.1 | Add to **`.env.example`** (and document in README): `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_EMAIL`. Use same env names as tech spec §3.8; cluster Secret will use identical keys later. |
| 2.1.2 | Document that the worker (not the Go API) uses Jira credentials; Go API continues to use only `WEBHOOK_SECRET`, `DATABASE_URL`, `LOG_LEVEL`. |
| 2.1.3 | Ensure `.env` is gitignored; never commit tokens. |

### 3.2 Database: idempotency storage

| Step | Action |
|------|--------|
| 2.2.1 | Add **idempotency storage** per tech spec **§4.3**: table **`processed_issues`** with **`issue_key`** (unique / PK), **`processed_at`**, optionally **`job_id`**. This marks **full L1 resolution** only, not every comment. |
| 2.2.2 | **Before** the worker runs the flow, **check** if `issue_key` is already in **`processed_issues`**. If yes: log “idempotency skip” (WARN), set job status to **`skipped`**, and do not run the flow or call Jira. |
| 2.2.3 | **After** the worker finishes with job status **`completed_full`** (full K8s + synthesis path), **insert** into **`processed_issues`**. Partial outcomes (**`awaiting_customer`**, **`completed_unsupported`**, **`failed`**) **do not** insert (**§1.1**). |

### 3.3 Jira tools in the worker

| Step | Action |
|------|--------|
| 2.3.1 | Implement **jira_get_issue(issue_key)** in the worker (e.g. `worker/tools/jira.py` for Phase 2): call Jira REST API (e.g. `/rest/api/3/issue/{issueIdOrKey}`) or JSM request API. Read credentials from env only: `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_EMAIL` (for Jira Cloud basic auth). Return summary, description, and any custom fields needed later for Intake. See tech spec §7.1, §9.2. |
| 2.3.2 | Implement **jira_post_comment(issue_key, body, internal=True)** in the worker: post comment via issue comment API or JSM request comment API; for POC internal-only is enough. Use same env credentials. |
| 2.3.3 | Handle Jira API errors (e.g. 401, 404, 5xx): log ERROR, mark job as `failed` or retry according to your policy; do not mark as processed if comment was not posted. |
| 2.3.4 | **Phase 3:** Move or reimplement these as CrewAI **tools** under **`service-desk-crew/src/service_desk_crew/tools/jira.py`** and **delete or shim** `worker/tools/jira.py` so there is a single Jira integration used by agents and optionally imported by the worker for non-agent utilities only if needed. |

### 3.4 Worker flow (minimal comment)

| Step | Action |
|------|--------|
| 2.4.1 | After claiming a job (as in Phase 1), **check idempotency**: if `issue_key` already processed, log “idempotency skip”, update job status, exit. |
| 2.4.2 | **Fetch ticket** from Jira inside the Flow / `run_l1_support` using Jira tools. Worker fetches at processing time (do not cache full ticket in DB per §4.0). If fetch fails, log ERROR, mark job **`failed`**, exit. |
| 2.4.3 | **L1 flow:** Intake → route → diagnostics → synthesis → post comment per **§8.2**; outcomes drive **`jobs.status`** (**§1.1**). |
| 2.4.4 | **Mark processed:** Call **`set_processed(issue_key, job_id)`** only when the run is **full resolution** (Phase 3+: CrewAI outcome **`FLOW_OUTCOME_FULL_RESOLUTION`** → job **`completed_full`**). Do **not** call **`set_processed`** for missing-info, unsupported, or error paths. Update **`jobs.status`** to the terminal value matching the outcome (**§1.1**). |
| 2.4.5 | **Loop prevention (§9.3):** Rely on idempotency so that when the bot’s comment triggers a webhook (if Jira Automation were on “comment created”), the worker would skip because `issue_key` is already processed. Prefer Jira Automation on “Request created” only. |

### 3.5 Logging

| Step | Action |
|------|--------|
| 2.5.1 | **Worker logging (§3.5):** Log **idempotency skip** (WARN) with `job_id`, `issue_key` when skipping. Log **comment posted** (INFO) with `issue_key` when the minimal comment is successfully posted. Log Jira API errors (ERROR) with reason. Keep correlation via `job_id` and `issue_key` in every log line. |
| 2.5.2 | Do not log `JIRA_API_TOKEN` or full ticket body at INFO; use summary or redact. |

### 3.6 Run locally and test

| Step | Action |
|------|--------|
| 2.6.1 | Set `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_EMAIL` in `.env`. Ensure Jira user has permission to read the issue and add comments (internal). |
| 2.6.2 | Run migrations or SQL to add `processed_issues` (or chosen idempotency schema). |
| 2.6.3 | **Test idempotency:** Create a job for an `issue_key` (e.g. via webhook or manual insert). Run worker; confirm it fetches ticket, posts minimal comment, marks processed. Insert a **second** job for the **same** `issue_key`; run worker again — confirm it logs “idempotency skip” and does not post a second comment. |
| 2.6.4 | **Test Jira failure:** Use an invalid token or wrong `issue_key`; confirm worker logs ERROR and marks job failed (and does not mark as processed). |

### 3.7 Phase 2 deliverables checklist

**Status: ✅ Phase 2 completed** (`service-desk-api` repo)

**Verification (code review):** `.env.example` and `worker/config.py` expose `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_EMAIL`. Migrations `db/migrations/00002_processed_issues.sql` and **`00003_jobs_issue_key_unique.sql`** define `processed_issues` and **`UNIQUE(issue_key)`** on **`jobs`** (one row per ticket). Jira tools live under **`service_desk_crew.tools.jira`** (Phase 3); `worker/run.py` implements `is_processed` / `set_processed`, `process_job`: claim → idempotency skip (`skipped`) → **`run_l1_support`** (CrewAI) → map flow outcome → **`completed_full`** only then **`set_processed`**; other outcomes **`awaiting_customer`** / **`completed_unsupported`** / **`failed`** without **`processed_issues`** insert. Missing Jira/LLM env or API errors → **`failed`**. Logging: WARN idempotency skip, INFO flow end per outcome, ERROR on failures with `job_id`/`issue_key`. README documents Phase 2–3 and manual test steps. *Automated tests and live Jira smoke tests are the operator’s responsibility before production use.*

- [x] `.env.example` includes `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_EMAIL`; worker reads Jira config from env only.
- [x] Idempotency: table or marker per `issue_key`; worker checks before processing and stores marker after successful comment.
- [x] Worker implements `jira_get_issue(issue_key)` and `jira_post_comment(issue_key, body, internal=True)` using env credentials.
- [x] Worker flow: claim job → check idempotency (skip → **`skipped`**) → run L1 flow → **`set_processed`** only on **`completed_full`** → terminal status per **§1.1**.
- [x] Logging: idempotency skip (WARN), comment posted (INFO), Jira errors (ERROR); correlation via `job_id`/`issue_key`.
- [x] Tests: (1) first run posts comment and marks processed; (2) second job for same issue_key skips with idempotency log; (3) invalid Jira creds or issue_key yields ERROR and job failed. *(Manual verification per README § smoke test 4.)*

---

## 4. Phase 3 — CrewAI Flow (`service-desk-crew` folder, import `service_desk_crew`) ✅ Completed

**Goal:** Place the **entire standard output of `crewai create crew service_desk_crew`** in monorepo folder **`service-desk-crew/`** (name with hyphens), **outside** `worker/`, per tech spec **§2.1.1** and **§10**. The Python package lives at **`service-desk-crew/src/service_desk_crew/`** (import **`service_desk_crew`**). Add **`flow.py`** and **`config/llm_factory.py`** for the L1 pipeline: **load_ticket** → **intake_check** → **route** → **k8s_diagnostics** (stub) → **synthesize** → **post_comment** → **mark_processed** (**§8.2**, agents **§6**). **Replace** Phase 2 “minimal comment only” with this Flow from the worker. **Idempotency** §4.3; **`set_processed`** / **`processed_issues`** only after **`completed_full`** (full K8s + synthesis path). Logging **§3.5**.

**Scaffold (illustrative):** `mkdir -p service-desk-crew && cd service-desk-crew && crewai create crew service_desk_crew` — align file paths with your installed CrewAI CLI version and [CrewAI project layout](https://docs.crewai.com/) documentation.

**Prerequisite:** Phase 2 complete. **Migrate** Jira from `worker/tools/jira.py` to **`service-desk-crew/src/service_desk_crew/tools/jira.py`** (step 4.1.5).

### 4.1 Scaffold **`service-desk-crew/`** (`crewai create crew` layout)

| Step | Action |
|------|--------|
| 3.1.1 | Create **`service-desk-crew/`** and run **`crewai create crew service_desk_crew`** **inside** it so **`src/service_desk_crew/`** contains **`crew.py`**, **`config/agents.yaml`**, **`config/tasks.yaml`**, **`tools/`**, **`main.py`**, plus **`pyproject.toml`** / **`README.md`** / optional **`knowledge/`** per [CrewAI docs](https://docs.crewai.com/). |
| 3.1.2 | **`service-desk-crew/pyproject.toml`**: installable **`service_desk_crew`**, **`src`** layout, Python **≥ 3.10**, **`crewai`** + provider extras; **`uv.lock`** if using **`uv`**. |
| 3.1.3 | Monorepo **`.env.example`**: LLM vars + **`JIRA_*`** (and **`DATABASE_URL`** on worker only if needed for callbacks). |
| 3.1.4 | From monorepo root: **`pip install -e ./service-desk-crew`** (document in README); **`worker/requirements.txt`** may list deps only, or **`-e file:../service-desk-crew`** relative to **`worker/`** — ensure **`import service_desk_crew`** works for **`worker/run.py`**. |
| 3.1.5 | **Jira tools:** **`service-desk-crew/src/service_desk_crew/tools/jira.py`**; remove or shim **`worker/tools/jira.py`**. |

### 4.2 LLM factory (inside `src/service_desk_crew/config/`)

| Step | Action |
|------|--------|
| 3.2.1 | Add **`service-desk-crew/src/service_desk_crew/config/llm_factory.py`**: **sole** place that builds `LLM(...)` from env. All agents use **`llm=get_llm()`** (or factory string) in **`crew.py`** / YAML per CrewAI docs. |
| 3.2.2 | Extend **`.env.example`** with LLM variables; align with **§10** and **§3.8**. |

### 4.3 Crew YAML — three agents, tasks

| Step | Action |
|------|--------|
| 3.3.1 | Edit **`service-desk-crew/src/service_desk_crew/config/agents.yaml`**: **Intake**, **Diagnostics**, **Synthesis** per **§6**; placeholders `{issue_key}`, `{ticket_context}`, etc. |
| 3.3.2 | Edit **`service-desk-crew/src/service_desk_crew/config/tasks.yaml`**: tasks; **`agent:`** keys; **expected_output** per §6. |
| 3.3.3 | Update **`service-desk-crew/src/service_desk_crew/crew.py`**: **`@CrewBase`**, **`@agent`**, **`@task`**, **`@crew`**; attach **Jira** and **stub diagnostic** tools. |

### 4.4 Repo-level POC config

| Step | Action |
|------|--------|
| 3.4.1 | **`config/required_fields.yml`** at monorepo root (sibling of **`worker/`**, **`service-desk-crew/`**) per **§4.2**. |
| 3.4.2 | **`config/routing.yml`** for k8s-ish routing (**§5.3–5.4**). |
| 3.4.3 | (Optional) **`config/mcp_endpoints.yml`** for Phase 4. |

### 4.5 Flow — `service-desk-crew/src/service_desk_crew/flow.py`

| Step | Action |
|------|--------|
| 3.5.1 | Add **`flow.py`**: CrewAI **Flow** (e.g. `L1SupportFlow`) with **`@start`**, **`@listen`**, **`@router`** (**§8.2**). **load_ticket** / **mark_processed**: prefer worker callbacks for DB **`processed_issues`**. |
| 3.5.2 | **Flow state (Pydantic):** `issue_key`, `ticket_raw`, `intake_output`, `diagnostics_artifact`, `synthesis_output` (**§8.2**). |
| 3.5.3 | **Branching:** not `can_proceed` → comment + **end**; not k8s-ish → unsupported + **end** (**§5.2, §5.4**). |
| 3.5.4 | **`post_comment`** → Jira tool; **`mark_processed`** → worker callback or return to worker. |

### 4.6 Stub diagnostics tool

| Step | Action |
|------|--------|
| 3.6.1 | **`service-desk-crew/src/service_desk_crew/tools/diagnostics_stub.py`** (or inline) — fixed text, no network (**§6.2**). |

### 4.7 Worker integration (thin shell)

| Step | Action |
|------|--------|
| 3.7.1 | **`worker/run.py`**: after claim + idempotency, **`import service_desk_crew`** — invoke Flow **`kickoff(...)`** (e.g. `L1SupportFlow`) or **`run_l1_support(...)`** from **`service_desk_crew.main`**. No **`@CrewBase`** in **`worker/`**. |
| 3.7.2 | Map CrewAI **outcome** to job status: **`completed_full`** (+ **`set_processed`**) only for full K8s resolution path; **`awaiting_customer`** / **`completed_unsupported`** / **`skipped`** / **`failed`** per **§1.1**; exceptions → **`failed`** without **`set_processed`**. |
| 3.7.3 | **Timeouts** **§11**. |

### 4.8 CLI entry (`crewai run`)

| Step | Action |
|------|--------|
| 3.8.1 | **`service-desk-crew/src/service_desk_crew/main.py`** **`run()`** for **`crewai run`** with working directory **`service-desk-crew/`** (per project **`pyproject.toml`**). |

### 4.9 Logging and redaction

| Step | Action |
|------|--------|
| 3.9.1 | Worker: log flow start/end, `job_id`, `issue_key`. Inside crew/flow: CrewAI verbose / structured logs per **§3.5**; truncate at INFO. |
| 3.9.2 | Optional: redact secrets in comment body before post (**§3.4**). |

### 4.10 Run locally and test

| Step | Action |
|------|--------|
| 3.10.1 | **`pip install -e ./service-desk-crew`** from monorepo root; run worker with `.env` (LLM + **`JIRA_*`**). |
| 3.10.2 | **Missing-info** / **k8s-ish** / **non-K8s** paths per **§12**. |
| 3.10.3 | **Idempotency:** if **`processed_issues`** already has `issue_key` → job **`skipped`**; webhook may still set **`pending`** again per **§1.1** (worker skips again). |
| 3.10.4 | Optional: **`cd service-desk-crew && crewai run`** (crew without DB). |

### 4.11 Phase 3 deliverables checklist

**Status: ✅ Phase 3 completed** in **`service-desk-api`** — verification: `service-desk-crew/` matches **`crewai create crew service_desk_crew`** layout with added **`flow.py`**, **`config/llm_factory.py`**, **`repo_config.py`** (loads **`config/required_fields.yml`**, **`config/routing.yml`**); **`L1SupportFlow`** implements branching (missing info / unsupported / K8s stub path); **`worker/run.py`** calls **`run_l1_support`** with **`FLOW_TIMEOUT_SECONDS`**; **`worker/tools/jira.py`** shims to **`service_desk_crew.tools.jira`**; **`.env.example`** includes **`OPENAI_*`**; README documents **`pip install -e ./service-desk-crew`** and Phase 3 smoke tests. *§12 success criteria and path coverage are validated by operator manual runs (same pattern as Phase 2).*

- [x] **`service-desk-crew/`** contains full **`crewai create crew`** scaffold under **`src/service_desk_crew/`**, plus **`flow.py`** and **`config/llm_factory.py`**.
- [x] **`service-desk-crew/pyproject.toml`**; **`pip install -e ./service-desk-crew`** documented; worker imports **`service_desk_crew`**.
- [x] **Jira** tools under **`service-desk-crew/src/service_desk_crew/tools/jira.py`**; Phase 2 **`worker/tools/jira.py`** removed or shimmed.
- [x] Monorepo **`config/required_fields.yml`**, **`config/routing.yml`** used by Flow/Intake.
- [x] **`flow.py`**: **§8.2** steps + state.
- [x] **YAML + `crew.py`**: Intake / Diagnostics stub / Synthesis.
- [x] **Worker**: DB poll + kickoff only; idempotency + **`processed_issues`** + job status (or callbacks).
- [x] Logging **§3.5**; tests **§12** *(manual / README smoke tests; no automated suite required for POC).*

---

## 5. Phase 4 — kagent integration

**Goal:** Replace the **Diagnostics stub** (Phase 3) with **read-only Kubernetes evidence** via **kagent MCP** (tech spec **§3.2**, **§6.2**, **§7.2**, **§10**, **§11**). The **worker** process runs CrewAI; **MCP calls execute inside `service_desk_crew`** using the same credentials/env as the rest of the flow (**§7.2** — “Worker talks to kagent’s MCP” from the worker/crew process). **Single code path** for local vs cluster: only **`KAGENT_MCP_URL`** (and optional auth) differ via **`.env`** or **Kubernetes Secret** (**§3.1**, **§3.8**). Enforce a **read-only allowlist** of MCP tool names; no apply/delete/scale/patch (**§7.2**, **§11**).

**Prerequisite:** Phase 3 complete (Flow, Intake, Diagnostics stub, Synthesis, worker **`run_l1_support`** / **`kickoff`**). A **kagent** (or compatible) MCP endpoint reachable from the machine running the worker: **kind/minikube**, **port-forward**, **VPN/tunnel** to a dev cluster, or **in-cluster Service** DNS (**§3.3**).

### 5.1 kagent MCP deployment and connectivity

| Step | Action |
|------|--------|
| 4.1.1 | **Install and expose kagent** in a target cluster (or local kind/minikube) per your org’s kagent documentation. Expose the MCP server as a **Kubernetes Service** (HTTP(S)) or use **port-forward** / **LoadBalancer** / **Ingress** for dev. |
| 4.1.2 | **Network path from worker:** Ensure the URL used in **`KAGENT_MCP_URL`** is reachable from the **worker process** host: **same cluster** → in-cluster DNS (e.g. `http://kagent-mcp.<namespace>.svc.cluster.local:...`); **local dev** → `localhost` after **`kubectl port-forward`**, kind/minikube node URL, or tunnel to remote cluster (**§3.3** Worker → kagent MCP). |
| 4.1.3 | **Authentication (optional):** If kagent requires a **bearer token**, **service account token**, or **mTLS**, store values in **`.env`** locally and later in **Kubernetes Secret** — **same variable names** as in **§3.3** / **§3.8**. Document placeholder lines in **`.env.example`**; never commit secrets. |

### 5.2 Environment and `.env.example`

| Step | Action |
|------|--------|
| 4.2.1 | Add **`KAGENT_MCP_URL=`** to **`.env.example`** under an **MCP** section (tech spec **§3.8** table: kind/minikube URL, tunnel, or mock endpoint in dev; in-cluster Service URL when deployed). |
| 4.2.2 | Application code reads **`os.environ` / `os.getenv` only** — no separate “local vs cluster” branches; switching environments is **only** how env is supplied (**§3.1**). |
| 4.2.3 | Optional: additional **`KAGENT_*`** (or generic **`MCP_*`**) vars for auth headers, CA bundle, or client cert paths if required; mirror keys in Phase 6 Secrets. |

### 5.3 `config/mcp_endpoints.yml`

| Step | Action |
|------|--------|
| 4.3.1 | **Populate** monorepo **`config/mcp_endpoints.yml`** (repo root, sibling of **`worker/`** and **`service-desk-crew/`**) per **§7.2** and **§10**: include **kagent MCP base URL** (or reference that URL is taken from **`KAGENT_MCP_URL`** env — avoid duplicating secrets in YAML). |
| 4.3.2 | **List allowed MCP tool names** explicitly (read-only): e.g. get/list pods, get deployments / statefulsets, describe pod, get events in namespace — **no** mutating operations (**§7.2**, **§11**). |
| 4.3.3 | Load this file from **`service_desk_crew`** (extend **`repo_config.py`** or add a small loader) using the **same repo-root path resolution** as **`config/required_fields.yml`** / **`config/routing.yml`**. |

### 5.4 `mcp_k8s.py` — MCP client and allowlist

| Step | Action |
|------|--------|
| 4.4.1 | Add **`service-desk-crew/src/service_desk_crew/tools/mcp_k8s.py`**: thin MCP client that invokes kagent’s tool server using **`KAGENT_MCP_URL`** (and optional auth from env), per **§7.2** and **§10** deliverables. |
| 4.4.2 | **Enforce allowlist:** reject or block any tool name not listed in **`config/mcp_endpoints.yml`** (and **§11** read-only rule). |
| 4.4.3 | **Timeouts:** apply a **hard timeout per MCP tool call** (**§11**); align upper bound with **`FLOW_TIMEOUT_SECONDS`** / worker flow timeout so the job cannot hang indefinitely. |
| 4.4.4 | **Errors:** on MCP/network failure, log **tool name** and outcome (**§3.5**); map to a controlled Diagnostics outcome (short error artifact or explicit “diagnostics unavailable” path) per product choice — do not silently return stub success. |

### 5.5 Crew YAML and `crew.py` — Diagnostics uses `mcp_k8s`

| Step | Action |
|------|--------|
| 4.5.1 | Update **`config/agents.yaml`** / **`config/tasks.yaml`**: **Diagnostics** agent remains **evidence-only**, minimal reasoning (**§6.2**); attach **CrewAI tools** backed by **`mcp_k8s.py`** instead of the stub. |
| 4.5.2 | Update **`crew.py`**: register **`mcp_k8s`** tools; **remove** **`diagnostics_stub`** from the default path or gate it behind **`USE_DIAGNOSTICS_STUB=true`** for offline dev only. |
| 4.5.3 | **Task prompts:** encode the **fixed diagnostic bundle** from **§6.2** — **Kubernetes (kagent MCP):** list/get pods in namespace, deployment/statefulset status, describe top **1–3** unhealthy pods, events in namespace (e.g. last **60–120** minutes); return one **compact artifact** for Synthesis. *(Loki/Mimir stay Phase 5 optional.)* |

### 5.6 Flow — `k8s_diagnostics` step

| Step | Action |
|------|--------|
| 4.6.1 | **`flow.py`:** **`k8s_diagnostics`** passes **structured context** from Intake (namespace, service, time window, etc.) into Diagnostics (**§8.2**, **§6.2**). No change to high-level step order vs Phase 3 unless you split tool calls for clarity. |
| 4.6.2 | **Missing / unreachable MCP:** If **`KAGENT_MCP_URL`** is unset or kagent is unreachable, define explicit behaviour: fail the step with a **user-visible internal comment** (e.g. “Kubernetes diagnostics unavailable”) or document a **temporary stub fallback** for demos — **do not** pretend real cluster evidence exists. |

### 5.7 Security (kagent RBAC and network)

| Step | Action |
|------|--------|
| 4.7.1 | **kagent ServiceAccount / RBAC:** grant **read-only** access to namespaces in scope (get/list/describe); **no** write verbs (**§3.4**, **§11**). |
| 4.7.2 | **Network:** Prefer worker egress **only** to **Jira**, **database**, **kagent MCP**, and optional observability endpoints (**§3.4**). If the worker talks **only** to kagent (not the API server directly), restrict accordingly. |
| 4.7.3 | **Logging:** do not log MCP bearer tokens, full kubectl-like dumps, or entire event streams at INFO — truncate or summarize (**§3.5**). |

### 5.8 Logging

| Step | Action |
|------|--------|
| 4.8.1 | Log **each MCP / kagent tool invocation**: tool name, **`issue_key`** / **`job_id`**, success or failure (**§3.5** worker table: “tool calls … kagent tool name, success/failure”). |
| 4.8.2 | Log **flow step** start/end for **`k8s_diagnostics`** (and duration if useful) at INFO in dev; align level with prod policy. |

### 5.9 Run locally and test

| Step | Action |
|------|--------|
| 4.9.1 | **`pip install -e ./service-desk-crew`** from monorepo root; set **`KAGENT_MCP_URL`** (and LLM + **`JIRA_*`**) in **`.env`**; run worker against DB + webhook path as in Phase 3. |
| 4.9.2 | **§12 success / demo:** Create a **k8s-routed** ticket with required fields → expect an internal comment whose **evidence** reflects **real** cluster state (pods, events, describe), not stub text. |
| 4.9.3 | **Read-only verification:** Confirm allowlist and kagent RBAC — **zero** mutating Kubernetes actions (**§12** “Read-only”). |
| 4.9.4 | **Idempotency unchanged:** second job for the same **`issue_key`** → skip per **`processed_issues`**. |
| 4.9.5 | Optional: document **exact kagent version**, MCP endpoint path, and **observed tool names** in README for reproducibility (**§13** follow-on). |

### 5.10 Phase 4 deliverables checklist

**Status:** *Pending implementation — use this checklist when implementing Phase 4 in **`service-desk-api`**. Verification: manual runs against a real or tunneled kagent; same pattern as Phase 2–3 smoke tests.*

- [ ] **`.env.example`** includes **`KAGENT_MCP_URL`** (and optional auth vars) per **§3.8**.
- [ ] **`config/mcp_endpoints.yml`** lists kagent-related config and **allowed tool names** (**§7.2**, **§10**).
- [ ] **`service-desk-crew/src/service_desk_crew/tools/mcp_k8s.py`** implements MCP client + **allowlist** + timeouts (**§10**).
- [ ] **Diagnostics** agent wired to **`mcp_k8s`**; stub removed or dev-only gated (**§6.2**).
- [ ] **`flow.py`** **`k8s_diagnostics`** step produces real evidence when MCP is available (**§8.2**).
- [ ] **Logging** includes MCP tool calls per **§3.5**; secrets not logged.
- [ ] **Guardrails:** read-only tools only; timeouts per **§11**; kagent RBAC read-only (**§3.4**).

---

## 6. Phase 5 — Optional: Loki/Mimir

**Goal:** Extend the **Diagnostics** step with **optional** log and metrics evidence from **Loki** and **Mimir** (tech spec **§3.2** Phase 5 row, **§6.2**, **§7.3–§7.4**, **§10** optional tools). Implement **Option A** (simplest): **direct HTTP** from the **`service_desk_crew`** tool layer — **`/loki/api/v1/query_range`** with **LogQL**, **`/prometheus/api/v1/query_range`** with **PromQL** (Mimir-compatible). **Option B** (MCP servers for Loki/Mimir) is out of scope for this POC plan unless you explicitly add it later (**§7.3–§7.4**). When **`LOKI_URL`** / **`MIMIR_URL`** are **unset or empty**, those integrations are **disabled**; behaviour matches **§3.8** (*optional vars: omit or leave empty*).

**Prerequisite:** Phase 3 Flow + Diagnostics path working; **Phase 4 (kagent)** strongly recommended so Diagnostics combines **K8s MCP evidence + optional logs/metrics** in one artifact (**§6.2**, **§8.2** step **k8s_diagnostics**). You can implement Loki/Mimir **without** kagent only if Diagnostics still receives namespace/service/time window from Intake (limited usefulness for cluster-scoped LogQL/PromQL).

### 6.1 Scope, Option A vs Option B

| Step | Action |
|------|--------|
| 5.1.1 | **Confirm POC approach:** Use **direct HTTP** tools under **`service-desk-crew/src/service_desk_crew/tools/`** — e.g. **`loki.py`**, **`mimir.py`** (**§7.3**, **§7.4** Option A; **§10** deliverables). |
| 5.1.2 | **Defer Option B** unless required: small **MCP servers** for Loki/Mimir (**§7.3–§7.4** Option B) — same agent wiring pattern as **`mcp_k8s`**, different transport. |
| 5.1.3 | **Single process / env:** Tools run in the **worker** process with CrewAI; read **`LOKI_URL`**, **`MIMIR_URL`** (and optional auth) from **`os.environ`** only — same pattern as **`KAGENT_MCP_URL`** (**§3.1**). |

### 6.2 Environment and `.env.example`

| Step | Action |
|------|--------|
| 5.2.1 | Add **`LOKI_URL=`** and **`MIMIR_URL=`** to **`.env.example`** under **Observability** (or **MCP + Observability**), per **§3.8** — document that **empty = disabled**. |
| 5.2.2 | Optional auth vars: e.g. **`LOKI_TOKEN`**, **`MIMIR_TOKEN`**, or basic-auth split vars if your stack requires them — align with **§3.3** (*Worker → Loki / Mimir — optional bearer or basic auth in Secret*). Never commit real values. |
| 5.2.3 | In code, **guard** each tool: if base URL is missing/empty, **no-op** or skip tool registration so Diagnostics does not error on optional backends. |

### 6.3 `loki.py` — LogQL and HTTP API

| Step | Action |
|------|--------|
| 5.3.1 | Implement **`service-desk-crew/src/service_desk_crew/tools/loki.py`**: HTTP client to **`{LOKI_URL}/loki/api/v1/query_range`** with **LogQL** built from Intake context (namespace, app/service labels, time range). |
| 5.3.2 | **Guardrails (§11):** Cap returned **lines** (e.g. **50**); use a **fixed time window** from Intake (e.g. last N minutes aligned with **§6.2** events window). **Consider redacting** obvious secret patterns in returned log lines. |
| 5.3.3 | **Timeouts:** Hard timeout per request (**§11**); treat 4xx/5xx and timeouts as soft failures — return short error text to Diagnostics, don’t crash the Flow. |
| 5.3.4 | **Queries:** Prefer **errors** / high-signal streams for the service/namespace (per **§6.2** “Loki (errors for service/namespace, last N lines)”). Document **label assumptions** (e.g. `namespace`, `app`, `pod`) in README — **§13** notes confirming label names for your cluster. |

### 6.4 `mimir.py` — PromQL and Prometheus-compatible API

| Step | Action |
|------|--------|
| 5.4.1 | Implement **`service-desk-crew/src/service_desk_crew/tools/mimir.py`**: HTTP client to **`{MIMIR_URL}/prometheus/api/v1/query_range`** (or your Mimir tenant path if different) with **PromQL**. |
| 5.4.2 | **Guardrails (§11):** **Cap query range** and **step** size; **no expensive global** or unbounded cardinality queries (**§7.4**). Example intent from **§6.2:** restarts, readiness, CPU/memory over **~30m** (tune to Intake window). |
| 5.4.3 | **Timeouts and errors:** Same pattern as Loki — bounded calls, graceful degradation. |
| 5.4.4 | Document **required labels** and example queries in README for operator verification (**§13**). |

### 6.5 Crew YAML, `crew.py`, and Diagnostics bundle

| Step | Action |
|------|--------|
| 5.5.1 | Register **`loki`** / **`mimir`** tools on the **Diagnostics** agent **only when** the corresponding env URL is set (or register always but tools return “disabled” quickly — prefer conditional registration for clarity). |
| 5.5.2 | Update **`tasks.yaml`** prompts so the **diagnostic bundle** explicitly includes (**§6.2**): **Kubernetes (kagent)** evidence, then **optional** Loki lines, then **optional** Mimir samples — still **one compact artifact** for Synthesis. |
| 5.5.3 | Keep **Synthesis** unchanged in role: it still turns **ticket + single diagnostics artifact** into the comment (**§6.3**). |

### 6.6 Flow — `k8s_diagnostics` step

| Step | Action |
|------|--------|
| 5.6.1 | **`flow.py`:** No new top-level step required if Diagnostics already aggregates K8s + optional tools into **`diagnostics_artifact`**; ensure **state** passes **namespace / service / time window** into the crew step (**§8.2**). |
| 5.6.2 | If Loki/Mimir fail partially, prefer an artifact that says **what succeeded** and **what failed** (consistent with **§12** “useful output” — avoid empty silence). |

### 6.7 Authentication and network

| Step | Action |
|------|--------|
| 5.7.1 | Implement **Authorization** headers from env (bearer) or basic auth as required by your Loki/Mimir gateway (**§3.3**). |
| 5.7.2 | **NetworkPolicy / egress:** Allow worker egress to **`LOKI_URL`** / **`MIMIR_URL`** hosts when enabled (**§3.4**). |

### 6.8 Guardrails (read-only, cost, safety)

| Step | Action |
|------|--------|
| 5.8.1 | **Read-only:** Loki/Mimir calls are **query-only**; no writes (**§11** alongside K8s read-only). |
| 5.8.2 | Enforce **line cap**, **time cap**, and **step/range limits** in code defaults (constants or **`config/`** YAML if you want operators to tune without code changes). |
| 5.8.3 | **Human-in-the-loop** unchanged: suggested triage only — no automatic infra changes (**§11**). |

### 6.9 Logging

| Step | Action |
|------|--------|
| 5.9.1 | Log **Loki/Mimir tool invocations** at INFO: backend name, **`issue_key`**, success/failure, **not** full response bodies at INFO (**§3.5**). |
| 5.9.2 | On failure, log **HTTP status** and **truncated** error message; DEBUG may include query shape but not auth headers. |

### 6.10 Run locally and test

| Step | Action |
|------|--------|
| 5.10.1 | Point **`LOKI_URL`** / **`MIMIR_URL`** at dev instances (or leave empty to confirm **disabled** path still passes **§12** k8s-only scenarios). |
| 5.10.2 | **§12 / demo:** With both URLs set, create a k8s-routed ticket — internal comment should include **log and/or metric snippets** where data exists. |
| 5.10.3 | **Load test:** Confirm capped queries do not overwhelm Loki/Mimir; adjust limits if queries timeout. |
| 5.10.4 | **Idempotency:** Unchanged — **`processed_issues`** still gates duplicate runs. |

### 6.11 Phase 5 deliverables checklist

**Status:** *Optional phase — implement when observability endpoints are available. Verification: manual runs with and without URLs set.*

- [ ] **`.env.example`** documents **`LOKI_URL`**, **`MIMIR_URL`**, and optional auth vars (**§3.8**, **§3.3**).
- [ ] **`loki.py`** — **`query_range`** + LogQL; **line** and **time** caps (**§7.3**, **§11**).
- [ ] **`mimir.py`** — **`query_range`** + PromQL; **range/step** caps (**§7.4**, **§11**).
- [ ] **Diagnostics** agent uses these tools when enabled; **one artifact** to Synthesis (**§6.2**).
- [ ] **Graceful degradation** when URLs unset or backends error.
- [ ] **Logging** per **§3.5**; **no secrets** in logs.
- [ ] README: **label names** and example queries for your environment (**§13**).

---

## 7. Phase 6 — Deploy to server / cluster

**Goal:** Package the already-working POC (**Phases 1–5**) as **container images** and run it on **Kubernetes** (or equivalent) with **PostgreSQL**, **no change to configuration loading in application source** — only **how** env vars are supplied: **`.env`** locally vs **Secret → Pod env** on cluster (tech spec **§2.2**, **§3.2** Phase 6 row, **§3.7–§3.8**, **§10**). Deliver **Helm and/or Kustomize** (or plain manifests), **documented** `kubectl` / Ingress / Jira Automation steps, **structured logging** guidance for **`kubectl logs`**, and a **security pass** (RBAC, network, secrets).

**Prerequisite:** End-to-end POC validated **locally** per **§3.2** order (`.env`, DB, tunnel optional). **Phase 4** kagent reachable from where the worker will run (in-cluster **Service** DNS is typical). **Config YAML** (`config/*.yml`) behaviour understood — ship via **image `COPY`** and/or **ConfigMap** mount (same files as repo root layout **§2.1.1**).

### 7.1 Principles (no config fork)

| Step | Action |
|------|--------|
| 6.1.1 | **Confirm:** Go API and Worker read **only** **`os.Getenv` / `os.environ`** with **fixed names** — **§3.8** (*Single contract*). Moving to cluster = **build/push images + wire Secrets**; **binaries unchanged** (**§3.8** *No code change*). |
| 6.1.2 | **Do not** add K8s-specific config loaders in app code for POC; optional **External Secrets Operator** still surfaces **identical** env var **names** in Pods (**§3.8**). |

### 7.2 Container images

| Step | Action |
|------|--------|
| 6.2.1 | **Go API:** Add a **`Dockerfile`** (multi-stage build) for **`api/`** — compile static binary, minimal runtime image; expose HTTP port used by webhook handler. |
| 6.2.2 | **Worker:** Add a **`Dockerfile`** that installs **`service-desk-crew`** (**`pip install` / copy `service-desk-crew/`** and install editable or wheel) so **`import service_desk_crew`** works at runtime (**§2.1.1**, **§10** `pyproject.toml`). Use **Python 3.10+** base image. |
| 6.2.3 | **Repo `config/`** (`required_fields.yml`, `routing.yml`, `mcp_endpoints.yml`, …): **`COPY`** into the **worker** image at paths matching **`repo_config.py`** / monorepo-root expectations, **or** mount a **ConfigMap** at the same paths — document the chosen approach (**§10** checklist items under **`config/`**). |
| 6.2.4 | **Tag and push** images to your registry (org policy: ACR, ECR, GCR, Harbor, etc.); parameterize image name/tag in Helm/Kustomize. |

### 7.3 PostgreSQL

| Step | Action |
|------|--------|
| 6.3.1 | Run **PostgreSQL** in-cluster (**StatefulSet** / Helm chart) **or** use **managed** RDS/Cloud SQL/Aurora — **§3.7** *Database* row. |
| 6.3.2 | Set **`DATABASE_URL`** in Secret (TLS/`sslmode` as required for prod). Apply **migrations** from **`db/migrations/`** (or your migration process) against cluster DB before cutover. |
| 6.3.3 | Restrict DB network access to **Go API** and **Worker** only (security groups / K8s network rules). |

### 7.4 Kubernetes Secrets and env mapping

| Step | Action |
|------|--------|
| 6.4.1 | Create a Secret (or separate Secrets by component) whose **keys match `.env` variable names exactly** — **§3.7–§3.8**, **§10** *`.env.example`*. Include at minimum: **`WEBHOOK_SECRET`**, **`DATABASE_URL`**, **`JIRA_BASE_URL`**, **`JIRA_API_TOKEN`**, **`JIRA_EMAIL`**, LLM keys (**`OPENAI_*`** or provider vars per **`llm_factory.py`**), **`KAGENT_MCP_URL`**, optional **`LOKI_URL`**, **`MIMIR_URL`**, optional MCP/auth tokens. |
| 6.4.2 | **Deployments** use **`envFrom: - secretRef: name: …`** so Pod env mirrors **`.env`** — **no second naming scheme** (**§3.7** *Helm / Kustomize*). |
| 6.4.3 | Optional **ConfigMap** for non-secret **`LOG_LEVEL`**, **`JIRA_BASE_URL`** if you prefer not to put them in Secret — still **same env var names** at runtime. |

### 7.5 Workloads: Go API, Worker, networking

| Step | Action |
|------|--------|
| 6.5.1 | **Go API:** **Deployment** + **Service** + **Ingress** (TLS) so **Jira Automation** can **HTTPS** POST to your webhook path — **§2.2** (*Go API reachable by Jira*), **§3.7** table. |
| 6.5.2 | **Worker:** **Deployment** (no Ingress required unless you add ops endpoints). **Same cluster as kagent recommended** — **§3.7** *Worker* / *kagent* rows; set **`KAGENT_MCP_URL`** to in-cluster DNS (e.g. `http://kagent-mcp.<ns>.svc.cluster.local:...`). |
| 6.5.3 | **Replicas:** Start with **1** worker replica if using DB polling + single-job semantics; scale only after reviewing **idempotency** and DB locking (**§4**). |
| 6.5.4 | **Resource requests/limits** and **liveness/readiness** probes as appropriate (Go API HTTP; worker process — optional exec probe or rely on restart policy). |

### 7.6 kagent, Loki, Mimir connectivity

| Step | Action |
|------|--------|
| 6.6.1 | **kagent:** Installed in **target** cluster; **Service** exposed; worker egress allowed — **§3.7** *kagent*. |
| 6.6.2 | **Optional `LOKI_URL` / `MIMIR_URL`:** Point to in-cluster or managed observability endpoints; auth via Secret (**§3.3** *Worker → Loki / Mimir*). |
| 6.6.3 | **Cross-cluster** kagent or observability: use reachable URLs + bearer/mTLS per **§3.3**; store material in Secrets. |

### 7.7 RBAC, ServiceAccount, NetworkPolicy

| Step | Action |
|------|--------|
| 6.7.1 | **Worker `ServiceAccount`:** If the worker must call **Kubernetes API** directly, bind **least-privilege RBAC** — often **none** if all K8s reads go through **kagent** (**§3.4**, **§10** *read-only for worker*). |
| 6.7.2 | **NetworkPolicy** (or cloud firewall): restrict egress from API/worker to **Jira**, **database**, **kagent**, optional **Loki/Mimir**, LLM provider endpoints, DNS — **§3.4** *Network policies*. |
| 6.7.3 | **kagent**’s own **ServiceAccount** remains **read-only** on namespaces in scope — **§3.4** *kagent* row. |

### 7.8 Jira Automation and ingress cutover

| Step | Action |
|------|--------|
| 6.8.1 | Update **Jira Automation** “Send web request” URL to the **public Ingress URL** of the Go API; keep **shared secret** header/query aligned with **`WEBHOOK_SECRET`** — **§3.3**, **§10** *Jira Automation rule*. |
| 6.8.2 | **HTTPS only** for webhook (**§3.3**). Validate certificate chain from Jira’s perspective (public CA or trusted internal CA). |

### 7.9 Observability and operations docs

| Step | Action |
|------|--------|
| 6.9.1 | **Structured logging doc:** how to **`kubectl logs -l app=…`**, filter by **`issue_key`** / **`job_id`** when logs use **JSON** or key fields — **§3.5**, Phase 6 spec row (*Structured logging doc for `kubectl logs`*). |
| 6.9.2 | Optional: **HPA** / monitoring dashboards later; POC only needs basic log access and failure visibility. |

### 7.10 Security review (Phase 6 checklist)

| Step | Action |
|------|--------|
| 6.10.1 | **Secrets:** No secrets in images, git, or INFO logs — **§3.5** *What not to log*. |
| 6.10.2 | **Read-only K8s:** Confirm no mutating paths — **§11**; align with Phase 4 allowlist. |
| 6.10.3 | **Loop prevention:** Jira Automation on **request created**, not bot **comment created** — **§9.3**. |

### 7.11 Runbook: validate on cluster

| Step | Action |
|------|--------|
| 6.11.1 | Apply manifests / Helm; wait for Pods **Ready**; run DB migrations. |
| 6.11.2 | **Smoke:** Trigger webhook (or insert job) → worker processes → internal Jira comment — **§12**. |
| 6.11.3 | **Document** operator checklist: **local** (`.env.example` → `.env`) then **cluster** (build/push, Secret, apply, Jira URL) — **§3.7** *Documentation* row. |

### 7.12 Phase 6 deliverables checklist

**Status:** *Apply when moving from local dev to shared/stage/prod cluster.*

- [ ] **Dockerfiles** for Go API and Worker; images build and run with **env-only** config.
- [ ] **Worker image** includes **`service_desk_crew`** + repo **`config/`** (image or ConfigMap).
- [ ] **PostgreSQL** reachable; migrations applied; **`DATABASE_URL`** in Secret.
- [ ] **Kubernetes Secret(s)** — keys **identical** to **`.env`**; **`envFrom`** on Deployments (**§3.8**).
- [ ] **Go API** Service + **Ingress** (TLS); **Worker** Deployment; **`KAGENT_MCP_URL`** in-cluster (or documented cross-cluster).
- [ ] **Helm and/or Kustomize** (or YAML) committed; image tags parameterised (**§3.7**).
- [ ] **RBAC** / **NetworkPolicy** / kagent read-only alignment (**§3.4**, **§10**).
- [ ] **Jira Automation** updated to cluster webhook URL (**§10**).
- [ ] **Logging / ops** short doc for **`kubectl logs`** (**§3.5**).
- [ ] **Security review** completed (**Phase 6** scope in **§3.2** table).

---

## 8. Reference

- **Tech spec:** [service-desk-poc-tech-spec.md](service-desk-poc-tech-spec.md) — §2.2 (deployment topology), §3.2 (phases), §3.3–§3.4 (auth, security), §3.5 (logging), §3.7–§3.8 (cluster deployment, config), §4 (data, idempotency), §5–§6 (paths and agents), §7 (tools; **§7.2 kagent MCP**, **§7.3 Loki**, **§7.4 Mimir**), §8 (CrewAI Flow), §9.3 (loop prevention), §10 (deliverables incl. **K8s manifests or Helm**), §11 (guardrails), §12 (success criteria), §13 (demo / label notes).
- **Portable / Configuration Portal:** [service-desk-portable-implementation-plan.md](service-desk-portable-implementation-plan.md) — optional extension for org-specific config and deployment.
