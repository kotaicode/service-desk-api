# Service Desk API

Go API and Python worker for the Service Desk POC — L1 support automation (Jira webhook → queue → CrewAI flow). This repo implements the **webhook receiver** and **worker** as per the Service Desk POC Technical Specification.

**Phase 1 (Foundation):** Webhook → validate secret → store job in DB → worker polls, claims, and processes jobs. Single env contract and logging.

**Phase 2 (Jira and idempotency):** ~~Minimal comment-only path~~ superseded by Phase 3.

**Phase 3 (CrewAI Flow):** Installable package `service_desk_crew` under `service-desk-crew/` (from `crewai create crew service_desk_crew`). The worker runs `L1SupportFlow`: load ticket → Intake → route (missing info / unsupported / K8s) → diagnostics stub → Synthesis → internal Jira comment. Jira integration lives in `service-desk-crew/src/service_desk_crew/tools/jira.py`. Repo-root `config/required_fields.yml` and `config/routing.yml` drive intake and “k8s-ish” routing. **`processed_issues`** records only **full-resolution** runs (K8s path with final synthesis comment), not missing-info or unsupported branches — so another webhook for the same `issue_key` can run again after `awaiting_customer` or `completed_unsupported`.

## Architecture

- **Go API:** Receives Jira webhook at `POST /webhook/jira`, validates `X-Webhook-Secret`, parses `issue_key` from body. **`jobs` has at most one row per `issue_key`** (unique index). **`UpsertJobFromWebhook`:** first issue → insert **`pending`**; **`awaiting_customer`**, **`completed_unsupported`**, or **`failed`** → **`pending`** again (**`reopened`: true**); **`pending`** / **`processing`** → refresh payload only (**`deduped`: true**); other terminals (e.g. **`completed_full`**, **`skipped`**) → **`pending`** on the same row for another pass. Response JSON includes **`reopened`** and **`deduped`**. Uses only `WEBHOOK_SECRET`, `DATABASE_URL`, `LOG_LEVEL`.
- **Database:** PostgreSQL (`DATABASE_URL`); `jobs` table (queue); `processed_issues` table (idempotency by `issue_key`).
- **Worker (Python):** Polls for `pending` jobs, claims one, checks idempotency (**skip only if `issue_key` is already in `processed_issues`**, i.e. a prior **full-resolution** run), runs **`service_desk_crew`** L1 flow, updates `jobs.status` to a terminal value (`completed_full`, `awaiting_customer`, `completed_unsupported`, `skipped`, `failed`), and inserts `processed_issues` **only** for `completed_full`. Uses `JIRA_*`, `OPENAI_API_KEY`, optional `OPENAI_MODEL_NAME`, `FLOW_TIMEOUT_SECONDS`, and `DATABASE_URL` from env.
- **`service-desk-crew/`:** CrewAI project (`pip install -e ./service-desk-crew` from repo root). CLI: `cd service-desk-crew && crewai run` (uses `SERVICE_DESK_ISSUE_KEY` or demo key — requires Jira + OpenAI env).

Configuration is **env-only** (§3.8): same variable names locally (`.env`) and on cluster (Kubernetes Secrets). No code fork.

## Prerequisites

- Go 1.22+
- Python 3.10+
- PostgreSQL (local or remote; create a database e.g. `service_desk`)
- (Optional) ngrok or similar for exposing webhook to Jira

## Setup

1. Create a PostgreSQL database and copy env template:
   ```sh
   createdb service_desk   # or use your existing DB
   cp .env.example .env
   # Edit .env: set WEBHOOK_SECRET (required), DATABASE_URL, LOG_LEVEL.
   # For Phase 2+: set JIRA_BASE_URL, JIRA_API_TOKEN, JIRA_EMAIL (worker only).
   # For Phase 3+: set OPENAI_API_KEY (and optionally OPENAI_MODEL_NAME).
   ```

2. Install Go deps. Run the API once so migrations run (creates `jobs` and `processed_issues` tables):
   ```sh
   go mod tidy
   go run .   # then Ctrl+C; or run API and worker as below
   ```

3. Install the CrewAI package and worker deps (from **repository root**):
   ```sh
   pip install -e ./service-desk-crew
   pip install -r worker/requirements.txt
   ```
   The editable install exposes `import service_desk_crew` to the worker process.

## Run locally

1. Start the Go API (loads `.env` automatically):
   ```sh
   go run .
   ```
   API listens on `:8080` (or `PORT` from env).

2. In another terminal, start the worker:
   ```sh
   python -m worker
   ```
   Worker reads `DATABASE_URL`, `LOG_LEVEL`, `JIRA_*`, `OPENAI_API_KEY`, and optional `FLOW_TIMEOUT_SECONDS` from env; polls every 15s by default.

## Smoke tests

### 1. Manual job insert (no Jira)

Insert a pending job and confirm the worker picks it up:

```sh
psql "$DATABASE_URL" -c "INSERT INTO jobs (issue_key, status, payload) VALUES ('TEST-1', 'pending', '{}');"
# Watch worker logs: "job claimed", then "job completed" with a terminal status (e.g. awaiting_customer if intake fails; completed_full on full K8s path).
```

### 2. Webhook (valid secret)

```sh
curl -X POST http://localhost:8080/webhook/jira \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: YOUR_SECRET_FROM_ENV" \
  -d '{"issueKey": "PROJ-123"}'
```

Expect `200` and `{"ok":true,"job_id":...,"issue_key":"PROJ-123"}`. Check API logs for "webhook received" and "job stored". Worker should claim and complete the job.

### 3. Webhook (invalid secret)

```sh
curl -X POST http://localhost:8080/webhook/jira \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: wrong" \
  -d '{"issueKey": "PROJ-456"}'
```

Expect `401` and no new job in the database.

### 4. Phase 2–3: Jira, LLM, idempotency

- Set `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_EMAIL`, and **`OPENAI_API_KEY`** in `.env`. Ensure the Jira user can read the issue and add internal comments.
- Create a job for a real issue key. Run the worker; it should run the L1 flow and post an **internal comment** (missing-info request, unsupported notice, or full synthesis on the K8s path — depending on ticket text and intake). The job row ends as **`awaiting_customer`**, **`completed_unsupported`**, or **`completed_full`** (or **`failed`** on errors).
- **`processed_issues`:** A row is inserted **only** when the job finishes **`completed_full`**. Missing-info and unsupported paths do **not** insert; a later webhook for the same `issue_key` can enqueue another run.
- Create a **second** job for the **same** `issue_key` **after** a **`completed_full`** run: the worker should log **idempotency skip** (WARN); job status **`skipped`**. If the first run ended **`awaiting_customer`** or **`completed_unsupported`**, a second job should **run** the flow again (no row in `processed_issues` yet).
- Invalid Jira credentials or missing `OPENAI_API_KEY` should yield ERROR and job **`failed`** without updating `processed_issues`.

## Endpoints

| Method | Path            | Description                    |
|--------|-----------------|--------------------------------|
| GET    | `/ping`         | Liveness                       |
| GET    | `/health`       | Health status                  |
| POST   | `/webhook/jira` | Jira webhook (X-Webhook-Secret) |

## Testing Jira push notifications with ngrok (localhost)

Use ngrok to expose your local API so Jira Automation can send webhooks to it.

### 1. Install ngrok

- **macOS (Homebrew):** `brew install ngrok`
- **Or:** sign up at [ngrok.com](https://ngrok.com), download the binary, and add it to your PATH.

### 2. Start your stack

In separate terminals:

```sh
# Terminal 1: Go API
go run .

# Terminal 2: Worker
python -m worker
```

Leave both running.

### 3. Start ngrok tunnel

```sh
ngrok http 8080
```

Note the **HTTPS Forwarding** URL (e.g. `https://abc123.ngrok-free.app`).  
**Free tier:** this URL changes each time you restart ngrok.

### 4. Configure Jira Service Management Automation

1. In your Jira Service Management project: **Project settings** → **Automation** (or **Apps** → **Automation**).
2. Create a new rule:
   - **Trigger:** **When request created** (or **When issue is created** for Jira Software).
3. Add action: **Send web request**.
   - **URL:** `https://<your-ngrok-url>/webhook/jira`  
     Example: `https://abc123.ngrok-free.app/webhook/jira`
   - **Method:** `POST`
   - **Headers:** add one header:
     - **Name:** `X-Webhook-Secret`
     - **Value:** the same value as `WEBHOOK_SECRET` in your `.env`
   - **Body:** choose **JSON** and send the issue key. For example:
     ```json
     {
       "issueKey": "{{issue.key}}"
     }
     ```
     (Use your project’s smart value for the issue key if different, e.g. `{{request.key}}` for JSM.)
4. Save and enable the rule.

5. **Second rule (re-run after the customer updates the request):** Add another automation (or branch) with trigger **When request is updated** (or **Issue updated**), same **Send web request** URL, headers, and JSON body as above. There is **only one `jobs` row per `issue_key`**: the API **updates** that row (`awaiting_customer` / `completed_unsupported` / **`failed`** → **`pending`**, or payload refresh only when **`pending`** / **`processing`**). After **`completed_full`**, a further webhook sets **`pending`** again on the same row; the worker may **`skip`** if `processed_issues` already has the issue.

6. **Reduce noise (recommended):** Narrow the **update** rule with conditions so Jira does not call the webhook on every edit. Examples: **only when** description or a specific custom field changes, **or** when a label such as `ready-for-bot` is added, **or** use JQL/advanced conditions your Jira plan supports. That avoids spamming webhooks; the API still **dedupes** payload when status is **`pending`** or **`processing`**.

7. **Webhook response fields:** `reopened` — previous status was **`awaiting_customer`**, **`completed_unsupported`**, or **`failed`** and was set back to **`pending`**. `deduped` — status was **`pending`** or **`processing`**; only the payload was refreshed. If both are false, the row was either newly inserted or re-queued from another terminal state (e.g. **`completed_full`**) on the **same** `issue_key` row.

8. **Database:** Migration **`00003_jobs_issue_key_unique.sql`** removes duplicate `issue_key` rows (keeps newest `id`) and adds a **unique index** on **`issue_key`**.

### 5. Test

1. Create a new request/issue in the project.
2. In the ngrok terminal you should see an HTTP request to `/webhook/jira`.
3. In the Go API terminal you should see logs: `webhook received`, `job stored` with the issue key.
4. In the worker terminal you should see `job claimed` and `job completed` for that issue key.
5. Optionally check the database: `psql "$DATABASE_URL" -c "SELECT id, issue_key, status FROM jobs ORDER BY id DESC LIMIT 5;"`

### Troubleshooting

- **401 from API:** Ensure the header `X-Webhook-Secret` in Jira exactly matches `WEBHOOK_SECRET` in `.env` (no extra spaces).
- **URL not reachable:** Ensure ngrok is running and the URL in the Automation rule uses **https** and includes `/webhook/jira`.
- **URL changed:** After restarting ngrok, update the webhook URL in the Jira Automation rule.

## Project layout

```
.
├── api/           # Go HTTP handlers (webhook, ping, health)
├── config/        # Env-only config
├── db/            # PostgreSQL connection, migrations, job operations
├── worker/        # Python worker (poll, claim, process, Jira)
│   └── tools/     # Jira get/post; later MCP wrappers
├── main.go
├── .env.example
├── .gitignore
└── README.md
```

## Documentation

- **[service-desk-docs](https://github.com/kotaicode/service-desk-docs)** (mdBook) — architecture, Kagent MCP, Jira, queue, and L1 flow.

## Reference

- Service Desk POC Technical Specification (see `agentic-dev/service-desk-poc-tech-spec.md` when in a monorepo)
- Service Desk Implementation Plan: `documents/service-desk-implementation-plan.md`
