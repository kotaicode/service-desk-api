# Service Desk API

Go API and Python worker for the Service Desk POC — L1 support automation (Jira webhook → queue → CrewAI flow). This repo implements the **webhook receiver** and **worker** as per the Service Desk POC Technical Specification.

**Phase 1 (Foundation):** Webhook → validate secret → store job in DB → worker polls, claims, and processes jobs. No Jira API or CrewAI yet; single env contract and logging.

## Architecture

- **Go API:** Receives Jira webhook at `POST /webhook/jira`, validates `X-Webhook-Secret`, parses `issue_key` from body, inserts a job into the database, returns 200.
- **Database:** PostgreSQL (`DATABASE_URL`); jobs table with `id`, `issue_key`, `status`, `payload`, `created_at`, `updated_at`.
- **Worker (Python):** Polls for `pending` jobs, claims one (sets `processing`), runs skeleton processing, sets `done` or `failed`. Later phases add CrewAI and Jira.

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
   # Edit .env: set WEBHOOK_SECRET (required), set DATABASE_URL and LOG_LEVEL if needed.
   ```

2. Install Go deps and run migrations (migrations run on API startup):
   ```sh
   go mod tidy
   ```

3. Install Python deps for the worker:
   ```sh
   cd worker && pip install -r requirements.txt && cd ..
   ```

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
   Worker reads `DATABASE_URL` and `LOG_LEVEL` from env, polls every 15s by default.

## Smoke tests

### 1. Manual job insert (no Jira)

Insert a pending job and confirm the worker picks it up:

```sh
psql "$DATABASE_URL" -c "INSERT INTO jobs (issue_key, status, payload) VALUES ('TEST-1', 'pending', '{}');"
# Watch worker logs: "job claimed", then "job completed" with status=done.
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

## Endpoints

| Method | Path            | Description                    |
|--------|-----------------|--------------------------------|
| GET    | `/ping`         | Liveness                       |
| GET    | `/health`       | Health status                  |
| POST   | `/webhook/jira` | Jira webhook (X-Webhook-Secret) |

## Optional: Jira Automation + tunnel

1. Expose the API via ngrok: `ngrok http 8080`.
2. In Jira Service Management: Automation rule **When request created** → **Send web request** to `https://<tunnel>/webhook/jira`, method POST, header `X-Webhook-Secret: <your secret>`, body with `issueKey` (or your payload shape).
3. Create a test request in Jira; confirm webhook fires, API stores job, worker processes it.

## Project layout

```
.
├── api/           # Go HTTP handlers (webhook, ping, health)
├── config/        # Env-only config
├── db/            # PostgreSQL connection, migrations, job operations
├── worker/        # Python worker (poll, claim, process)
├── main.go
├── .env.example
├── .gitignore
└── README.md
```

## Reference

- Service Desk POC Technical Specification (see `agentic-dev/service-desk-poc-tech-spec.md` when in a monorepo)
- Service Desk Implementation Plan (see `agentic-dev/service-desk-implementation-plan.md`)
